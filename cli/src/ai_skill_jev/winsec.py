"""Windows ACL helpers (plan 6.2, 6.3): give our private dirs a protected DACL and verify it.

Only the current user, SYSTEM and Administrators may hold access. A profile can carry extra
inherited ACEs (for example a sandbox group with read access), so the dirs we create get an
explicit protected DACL instead of trusting inheritance; the DACL is then read back and checked.
"""

from __future__ import annotations

import os
import re
import sys

ALLOWED_ALIASES = {"SY", "BA", "S-1-5-18", "S-1-5-32-544"}
INHERIT_ALIASES = {"CO", "S-1-3-0"}  # CREATOR OWNER: replaced by the creator's SID on children
_ACE_RE = re.compile(r"\(([^()]*)\)")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p

    _advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi32.OpenProcessToken.restype = wintypes.BOOL
    _advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _advapi32.GetTokenInformation.restype = wintypes.BOOL
    _advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    _advapi32.SetFileSecurityW.restype = wintypes.BOOL
    _advapi32.GetFileSecurityW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _advapi32.GetFileSecurityW.restype = wintypes.BOOL
    _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW.restype = wintypes.BOOL

    _TOKEN_QUERY = 0x0008
    _TOKEN_USER = 1
    _DACL_SECURITY_INFORMATION = 0x00000004
    _PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
    _SDDL_REVISION_1 = 1

    def _raise_last(what: str) -> None:
        raise OSError(ctypes.get_last_error(), f"{what} failed")

    def _take_wstr(ptr: "ctypes.c_void_p") -> str:
        try:
            return ctypes.wstring_at(ptr.value)
        finally:
            _kernel32.LocalFree(ptr)

    def current_user_sid() -> str:
        token = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(_kernel32.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
            _raise_last("OpenProcessToken")
        try:
            size = wintypes.DWORD()
            _advapi32.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            if not _advapi32.GetTokenInformation(token, _TOKEN_USER, buf, size, ctypes.byref(size)):
                _raise_last("GetTokenInformation")
            sid_ptr = ctypes.c_void_p.from_buffer(buf).value  # TOKEN_USER.User.Sid
            out = ctypes.c_void_p()
            if not _advapi32.ConvertSidToStringSidW(sid_ptr, ctypes.byref(out)):
                _raise_last("ConvertSidToStringSidW")
            return _take_wstr(out)
        finally:
            _kernel32.CloseHandle(token)

    def set_dacl(path, sddl: str) -> None:
        """Replace the DACL of `path` with the protected DACL `sddl`."""
        sd = ctypes.c_void_p()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, _SDDL_REVISION_1, ctypes.byref(sd), None):
            _raise_last("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            if not _advapi32.SetFileSecurityW(
                    os.fspath(path), _DACL_SECURITY_INFORMATION | _PROTECTED_DACL_SECURITY_INFORMATION, sd):
                _raise_last("SetFileSecurityW")
        finally:
            _kernel32.LocalFree(sd)

    def protect_dir(path) -> None:
        """Replace the DACL of `path` with a protected one: user, SYSTEM, Administrators."""
        set_dacl(path, private_dacl_sddl(current_user_sid()))

    def dacl_sddl(path) -> str:
        target = os.fspath(path)
        size = wintypes.DWORD()
        _advapi32.GetFileSecurityW(target, _DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(size))
        if not size.value:
            _raise_last("GetFileSecurityW")
        buf = ctypes.create_string_buffer(size.value)
        if not _advapi32.GetFileSecurityW(target, _DACL_SECURITY_INFORMATION, buf, size, ctypes.byref(size)):
            _raise_last("GetFileSecurityW")
        out = ctypes.c_void_p()
        if not _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                buf, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(out), None):
            _raise_last("ConvertSecurityDescriptorToStringSecurityDescriptorW")
        return _take_wstr(out)

    def _sddl_form_of(sid: str) -> str:
        """The spelling Windows uses for `sid` inside SDDL (for example `LA` for the built-in admin)."""
        sd = ctypes.c_void_p()
        if not _advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"D:(A;;FA;;;{sid})", _SDDL_REVISION_1, ctypes.byref(sd), None):
            _raise_last("ConvertStringSecurityDescriptorToSecurityDescriptorW")
        try:
            out = ctypes.c_void_p()
            if not _advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
                    sd, _SDDL_REVISION_1, _DACL_SECURITY_INFORMATION, ctypes.byref(out), None):
                _raise_last("ConvertSecurityDescriptorToStringSecurityDescriptorW")
            aces = _ACE_RE.findall(_take_wstr(out))
        finally:
            _kernel32.LocalFree(sd)
        return aces[0].split(";")[5] if aces else sid


def private_dacl_sddl(user_sid: str) -> str:
    return f"D:P(A;OICI;FA;;;{user_sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"


def sddl_is_private(sddl: str, *user_forms: str) -> bool:
    """True when every allow ACE names the user, SYSTEM or Administrators.

    Inherit-only ACEs (`IO`) are checked too: they do not apply to the dir itself but are copied
    onto the files created below it, such as `credentials.json`. `CREATOR OWNER` (`CO`) is
    accepted because it becomes the creating user's SID on the child. A NULL DACL
    (`D:NO_ACCESS_CONTROL`, or no `D:` part) fails; deny ACEs only remove access and are ignored.
    """
    if not sddl.startswith("D:") or "NO_ACCESS_CONTROL" in sddl:
        return False
    allowed = ALLOWED_ALIASES | INHERIT_ALIASES | set(user_forms)
    for ace in _ACE_RE.findall(sddl):
        fields = ace.split(";")
        if len(fields) < 6:
            return False
        ace_type, sid = fields[0], fields[5]
        if ace_type in ("D", "OD", "XD"):
            continue
        if sid not in allowed:
            return False
    return True


def is_private(path) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Windows only")
    try:
        sid = current_user_sid()
        return sddl_is_private(dacl_sddl(path), sid, _sddl_form_of(sid))
    except OSError:
        return False
