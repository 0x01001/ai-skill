"""Finding `jg` on PATH, telling the shim from the real `jg`, and the user-level PATH fix (plan 5.3 step 10).

The shim is recognised by content: every launcher pip/pipx writes for it (Windows `.exe` with an
appended zip, or a POSIX script) contains the module name `ai_skill_jev.jg_shim`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

SHIM_MARKER = b"ai_skill_jev.jg_shim"
REG_SZ = 1          # winreg.REG_SZ
REG_EXPAND_SZ = 2   # winreg.REG_EXPAND_SZ
MAX_LAUNCHER_BYTES = 16 * 1024 * 1024


def is_shim_file(path) -> bool:
    try:
        if os.path.getsize(path) > MAX_LAUNCHER_BYTES:
            return False
        with open(path, "rb") as handle:
            return SHIM_MARKER in handle.read()
    except OSError:
        return False


def _pathext(env) -> List[str]:
    if sys.platform != "win32":
        return [""]
    raw = env.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    return [ext.lower() for ext in raw.split(";") if ext]


def split_path(value: Optional[str]) -> List[str]:
    return [entry for entry in (value or "").split(os.pathsep) if entry.strip()]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.expandvars(path.strip().strip('"'))))


def jg_in_dir(directory: str, env) -> List[str]:
    """Executables named `jg` in one PATH entry, in the order the shell would try them."""
    found = []
    base = os.path.expandvars(directory.strip().strip('"'))
    for ext in _pathext(env):
        candidate = os.path.join(base, "jg" + ext)
        if os.path.isfile(candidate) and (sys.platform == "win32" or os.access(candidate, os.X_OK)):
            found.append(candidate)
    return found


def jg_candidates(env) -> List[str]:
    out = []
    seen = set()
    for entry in split_path(env.get("PATH")):
        for candidate in jg_in_dir(entry, env):
            key = os.path.normcase(os.path.abspath(candidate))
            if key not in seen:
                seen.add(key)
                out.append(candidate)
    return out


@dataclass
class JgLookup:
    first: Optional[str]      # what `jg` resolves to in this PATH
    first_is_shim: bool
    real: Optional[str]       # first non-shim `jg` (the one the shim runs)
    shim: Optional[str]       # first shim on PATH


def lookup_jg(env) -> JgLookup:
    first = real = shim = None
    first_is_shim = False
    for candidate in jg_candidates(env):
        shim_here = is_shim_file(candidate)
        if first is None:
            first, first_is_shim = candidate, shim_here
        if shim_here and shim is None:
            shim = candidate
        if not shim_here and real is None:
            real = candidate
    return JgLookup(first, first_is_shim, real, shim)


def own_shim_dir() -> Optional[str]:
    """Directory holding the `jg` shim installed next to this `ai` (pipx bin dir or venv Scripts)."""
    dirs = []
    if sys.argv and sys.argv[0]:
        dirs.append(os.path.dirname(os.path.abspath(sys.argv[0])))
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    dirs.extend([exe_dir, os.path.join(exe_dir, "Scripts"), os.path.join(exe_dir, "bin")])
    for directory in dirs:
        for candidate in jg_in_dir(directory, os.environ):
            if is_shim_file(candidate):
                return directory
    return None


# --------------------------------------------------------------------------- user PATH (Windows)

class UserEnv:
    """Access to the persistent PATH variables. Tests inject a fake; only WindowsUserEnv touches the registry."""

    def read_user_path(self) -> Tuple[Optional[str], Optional[int]]:
        raise NotImplementedError

    def write_user_path(self, value: Optional[str], value_type: Optional[int]) -> None:
        raise NotImplementedError

    def machine_path(self) -> str:
        raise NotImplementedError

    def broadcast(self) -> None:
        pass


class WindowsUserEnv(UserEnv):
    USER_KEY = r"Environment"
    MACHINE_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def read_user_path(self):
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.USER_KEY, 0, winreg.KEY_READ) as key:
            try:
                value, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                return None, None
        return value, value_type

    def write_user_path(self, value, value_type):
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.USER_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if value is None:
                try:
                    winreg.DeleteValue(key, "Path")
                except FileNotFoundError:
                    pass
            else:
                winreg.SetValueEx(key, "Path", 0, value_type or winreg.REG_EXPAND_SZ, value)

    def machine_path(self):
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, self.MACHINE_KEY, 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
                return value or ""
        except OSError:
            return ""

    def broadcast(self):
        import ctypes
        from ctypes import wintypes

        result = wintypes.DWORD()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result))


@dataclass
class PathPlan:
    status: str               # ready | change | shadowed_machine | no_shim
    new_user_path: Optional[str] = None
    blocking_dir: Optional[str] = None


def plan_user_path(user_path: Optional[str], machine_path: str, shim_dir: Optional[str],
                   classify: Callable[[str], Optional[str]]) -> PathPlan:
    """Decide how to make the shim win over the npm `jg` in new shells.

    Windows builds a new shell's PATH as machine PATH followed by user PATH. `classify(dir)`
    returns "shim", "real" or None for one PATH entry.
    """
    if not shim_dir:
        return PathPlan("no_shim")
    machine_entries = split_path(machine_path)
    user_entries = split_path(user_path)
    for origin, entry in [("machine", e) for e in machine_entries] + [("user", e) for e in user_entries]:
        kind = classify(entry)
        if kind == "shim":
            return PathPlan("ready")
        if kind == "real":
            if origin == "machine":
                return PathPlan("shadowed_machine", blocking_dir=entry)
            break
    shim_key = _norm(shim_dir)
    rest = [e for e in user_entries if _norm(e) != shim_key]
    return PathPlan("change", new_user_path=os.pathsep.join([shim_dir] + rest))


def classify_dir(env) -> Callable[[str], Optional[str]]:
    def classify(entry: str) -> Optional[str]:
        found = jg_in_dir(entry, env)
        if not found:
            return None
        return "shim" if is_shim_file(found[0]) else "real"

    return classify


def apply_user_path_fix(user_env: UserEnv, shim_dir: Optional[str], env=None) -> PathPlan:
    """Apply the plan: move the shim dir (the pipx bin dir) to the front of the user PATH.

    The change is permanent on purpose: `ai logout` keeps it, so `jg` still resolves to the shim,
    which refuses to run without a key (reason=no_key). Uninstalling ai-skill-jev removes the shim
    and `jg` falls through to npm's again, so the order never needs undoing.
    """
    env = os.environ if env is None else env
    value, value_type = user_env.read_user_path()
    plan = plan_user_path(value, user_env.machine_path(), shim_dir, classify_dir(env))
    if plan.status != "change":
        return plan
    new_type = value_type if value_type in (REG_SZ, REG_EXPAND_SZ) else REG_EXPAND_SZ
    user_env.write_user_path(plan.new_user_path, new_type)
    user_env.broadcast()
    return plan


def entries_text(entries: Iterable[str]) -> str:
    return os.pathsep.join(entries)
