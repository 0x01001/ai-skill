"""Metadata, private directories and the OS secret store (plan 6.1, 6.3, 5.4).

`jev.json` never holds the key. The key lives in the OS secret store under service
`ai-skill/jev` with a random account; the stored value binds the key to its provider,
endpoint and model so an edited `jev.json` cannot redirect the key to another host.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
import stat
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from . import POLICY_VERSION, SECRET_SERVICE

METADATA_SCHEMA = 1
SECRET_FORMAT = 1
STORE_READ_TIMEOUT = 10.0
# CRED_MAX_CREDENTIAL_BLOB_SIZE is 2560 bytes and keyring stores UTF-16, so 1280 characters.
WINVAULT_MAX_CHARS = 1280


class StateError(Exception):
    """A state problem with a stable reason code (plan 9.2) and a message without secrets."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


# --------------------------------------------------------------------------- paths

def _abs_env(env, name: str) -> Optional[Path]:
    value = env.get(name)
    if value and os.path.isabs(value):
        return Path(value)
    return None


def config_dir(env=None) -> Path:
    env = os.environ if env is None else env
    if sys.platform == "win32":
        base = _abs_env(env, "APPDATA")
        if base is None:
            raise StateError("no_cli", "APPDATA is not set")
        return base / "ai-skill"
    base = _abs_env(env, "XDG_CONFIG_HOME") or Path.home() / ".config"
    return base / "ai-skill"


def metadata_path(env=None) -> Path:
    return config_dir(env) / "jev.json"


def run_root(env=None) -> Path:
    """Parent of the per-run dirs that hold temporary `jg` credentials (plan 6.2)."""
    env = os.environ if env is None else env
    if sys.platform == "win32":
        base = _abs_env(env, "LOCALAPPDATA")
        if base is None:
            raise StateError("no_cli", "LOCALAPPDATA is not set")
        return base / "ai-skill" / "jg-run"
    runtime = _abs_env(env, "XDG_RUNTIME_DIR")
    if runtime is not None:
        return runtime / "ai-skill" / "jg-run"
    cache = _abs_env(env, "XDG_CACHE_HOME") or Path.home() / ".cache"
    return cache / "ai-skill" / "jg-run"


# --------------------------------------------------------------------------- private files

def _is_link_or_junction(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def ensure_private_dir(path: Path, top: Optional[Path] = None) -> Path:
    """Create `path` (and parents up to `top`) as a private, non-link directory.

    POSIX: mode 0700 on every dir we own. Windows: a protected DACL (user, SYSTEM,
    Administrators) on `top`, inherited below it, then read back and verified.
    """
    path = Path(path)
    top = Path(top) if top is not None else path
    parts = path.relative_to(top).parts
    chain = [top] + [top.joinpath(*parts[: i + 1]) for i in range(len(parts))]
    for candidate in chain:
        if _is_link_or_junction(candidate):
            raise StateError("no_cli", f"refusing to use a link or junction: {candidate}")
        if not candidate.exists():
            os.makedirs(candidate, mode=0o700, exist_ok=True)
        if not candidate.is_dir():
            raise StateError("no_cli", f"not a directory: {candidate}")
    if sys.platform == "win32":
        from . import winsec

        # SetFileSecurityW does not propagate to existing children, so every dir we own is checked.
        for candidate in chain:
            if not winsec.is_private(candidate):
                winsec.protect_dir(candidate)
        if not winsec.is_private(path):
            raise StateError("no_cli", f"directory ACL grants access beyond the current user: {path}")
    else:
        for candidate in chain:
            os.chmod(candidate, 0o700)
            st = os.lstat(candidate)
            if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) & 0o077:
                raise StateError("no_cli", f"directory is not private: {candidate}")
    return path


def write_private_file(path: Path, data: bytes) -> None:
    """Exclusive create with mode 0600; fails if anything already sits at `path`."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, payload: dict) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    write_private_file(tmp, data)
    try:
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- metadata

def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_metadata(env=None) -> Optional[dict]:
    """None when `ai auth` never ran (or `ai logout` finished). Corrupt files raise StateError."""
    path = metadata_path(env)
    if _is_link_or_junction(path):
        raise StateError("no_cli", "jev.json is a link; refusing to read it")
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise StateError("no_cli", f"cannot read jev.json ({exc.__class__.__name__})") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise StateError("no_cli", "jev.json is not valid JSON") from exc
    if not isinstance(data, dict) or data.get("schema_version") != METADATA_SCHEMA:
        raise StateError("no_cli", "jev.json has an unknown schema")
    return data


def save_metadata(meta: dict, env=None) -> None:
    path = metadata_path(env)
    ensure_private_dir(path.parent)
    atomic_write_json(path, meta)


def delete_metadata(env=None) -> None:
    try:
        os.unlink(metadata_path(env))
    except FileNotFoundError:
        pass


def new_metadata() -> dict:
    return {
        "schema_version": METADATA_SCHEMA,
        "enabled": False,
        "provider": None,
        "base_url": None,
        "model": None,
        "endpoint_host": None,
        "secret_backend": None,
        "credential_ref": None,
        "profile_generation": 0,
        "consent": None,
        "validated_at": None,
        "engine": {"name": "jg"},
        "pending_secret_delete": [],
    }


def is_active(meta: Optional[dict]) -> bool:
    """A Jev key is configured: the only state in which the shim runs the real `jg`."""
    return bool(meta) and bool(meta.get("enabled")) and bool(meta.get("credential_ref"))


def consent_valid(meta: Optional[dict]) -> bool:
    if not meta:
        return False
    consent = meta.get("consent")
    return (
        isinstance(consent, dict)
        and consent.get("provider") == meta.get("provider")
        and consent.get("endpoint") == meta.get("base_url")
        and consent.get("policy_version") == POLICY_VERSION
    )


# --------------------------------------------------------------------------- secret store

def _backend_candidates():
    """Allowlisted keyring backends for this platform, as (name, class). Never keyring.get_keyring()."""
    out = []
    try:
        if sys.platform == "win32":
            from keyring.backends.Windows import WinVaultKeyring

            out.append(("WinVaultKeyring", WinVaultKeyring))
        elif sys.platform == "darwin":
            from keyring.backends.macOS import Keyring as MacKeyring

            out.append(("macOS.Keyring", MacKeyring))
        else:
            from keyring.backends.SecretService import Keyring as SecretServiceKeyring
            from keyring.backends.kwallet import DBusKeyring

            out.append(("SecretService.Keyring", SecretServiceKeyring))
            out.append(("kwallet.DBusKeyring", DBusKeyring))
    except ImportError:
        return []
    return out


class KeyringStore:
    """Thin wrapper over one allowlisted keyring backend."""

    def __init__(self, backend_name: str, backend):
        self.backend_name = backend_name
        self._backend = backend

    @classmethod
    def detect(cls) -> Optional["KeyringStore"]:
        for name, klass in _backend_candidates():
            try:
                if klass.viable:
                    return cls(name, klass())
            except Exception:  # noqa: BLE001 - a broken backend is simply not available
                continue
        return None

    @classmethod
    def by_name(cls, name: str) -> Optional["KeyringStore"]:
        for candidate, klass in _backend_candidates():
            if candidate == name:
                try:
                    return cls(name, klass()) if klass.viable else None
                except Exception:  # noqa: BLE001
                    return None
        return None

    def max_secret_chars(self) -> Optional[int]:
        return WINVAULT_MAX_CHARS if self.backend_name == "WinVaultKeyring" else None

    def get(self, account: str) -> Optional[str]:
        return self._backend.get_password(SECRET_SERVICE, account)

    def set(self, account: str, value: str) -> None:
        self._backend.set_password(SECRET_SERVICE, account, value)

    def delete(self, account: str) -> None:
        from keyring.errors import PasswordDeleteError

        try:
            self._backend.delete_password(SECRET_SERVICE, account)
        except PasswordDeleteError:
            if self._backend.get_password(SECRET_SERVICE, account) is not None:
                raise


def new_credential_ref() -> str:
    return secrets.token_hex(16)


def encode_secret(profile: dict, key: str) -> str:
    return json.dumps(
        {"v": SECRET_FORMAT, "provider": profile["provider"], "base_url": profile["base_url"],
         "model": profile["model"], "key": key},
        separators=(",", ":"),
    )


def decode_secret(value: Optional[str], meta: dict) -> str:
    """Return the key if the stored value is bound to the profile in `meta`; else raise no_key."""
    if not value:
        raise StateError("no_key", "the secret store has no key for this profile")
    try:
        data = json.loads(value)
    except ValueError as exc:
        raise StateError("no_key", "the stored secret is not in the expected format") from exc
    if not isinstance(data, dict) or data.get("v") != SECRET_FORMAT:
        raise StateError("no_key", "the stored secret is not in the expected format")
    for field in ("provider", "base_url", "model"):
        if data.get(field) != meta.get(field):
            raise StateError("no_key", "the stored secret is bound to a different provider or endpoint")
    key = data.get("key")
    if not isinstance(key, str) or not key:
        raise StateError("no_key", "the stored secret has no key")
    return key


def read_key(meta: dict, store_factory: Callable[[str], Optional[object]] = KeyringStore.by_name,
             timeout: float = STORE_READ_TIMEOUT) -> str:
    """Read and bind-check the key with a deadline, so a locked store cannot hang discovery."""
    ref = meta.get("credential_ref")
    backend = meta.get("secret_backend")
    if not ref or not backend:
        raise StateError("no_key", "no key is configured")
    box: dict = {}

    def worker():
        try:
            store = store_factory(backend)
            if store is None:
                box["err"] = StateError("locked_store", "the secret store is not available")
                return
            box["value"] = store.get(ref)
        except Exception as exc:  # noqa: BLE001 - backend errors carry no key
            box["err"] = StateError("locked_store", f"the secret store failed ({exc.__class__.__name__})")

    thread = threading.Thread(target=worker, name="ai-skill-keyring", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise StateError("locked_store", "the secret store did not answer in time")
    if "err" in box:
        raise box["err"]
    return decode_secret(box.get("value"), meta)
