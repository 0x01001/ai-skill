"""Input cleaning shared by every `ai decide` kind (plan 3.5).

- `redact`: secret-shaped strings and the key in use become `[redacted]`.
- `is_excluded_path`: the shim's exclusion groups (`.env*`, keys, credentials, logs, dependency
  dirs); an excerpt from such a file is dropped, never sent.
- `clip`: cut a field to its limit, from the end (`tail=True` keeps the last characters).
"""

from __future__ import annotations

import fnmatch
import re
from typing import Optional, Tuple

from .jg_runner import EXCLUDE_PATTERNS

REDACTED = "[redacted]"

# A name that says "secret": DB_PASSWORD, AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN, access_token,
# client_secret, apiKey, "password" (JSON). Bare `pass` is left out: test logs say `pass: 5`.
_SECRET_NAME = (r"[A-Za-z0-9_.\-]*(?:passw(?:or)?d|pwd|secret|api[_\-]?key|apikey|token|access[_\-]?key"
                r"|private[_\-]?key|credentials?|auth)")
_VALUE = r"""(?P<secret>"[^"\n]*"|'[^'\n]*'|[^\s,;&}\]"']+)"""

# Each pattern either is the secret, or marks it with the group `secret` (the rest stays readable).
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    # scheme://user:password@host
    re.compile(r"\b[A-Za-z][A-Za-z0-9+.\-]*://[^\s:/@]+:(?P<secret>[^\s@/]+)@"),
    # Authorization: Bearer x / Basic x, and a bare `Bearer x`
    re.compile(r"(?i)\b(?:bearer|basic)\s+(?P<secret>[A-Za-z0-9._~+/\-]{8,}=*)"),
    # NAME=value, NAME: value, "name": "value"
    re.compile(r"(?i)(?<![A-Za-z0-9])" + _SECRET_NAME + r"[\"']?(?:\s*[:=]\s*)" + _VALUE),
)


# The same names, alone: a field whose name says "secret" (`DB_PASSWORD`, `db password`). A name that
# only starts with one (`DB_PASSWORD_FILE`) or merely contains the letters (`author`) is not one.
_SECRET_FIELD = re.compile(r"(?i)(?<![A-Za-z0-9])" + _SECRET_NAME + r"[\"']?\s*\Z")


def is_secret_name(name: str) -> bool:
    """True when a key/value field named `name` holds a secret, so its whole value must go."""
    return isinstance(name, str) and _SECRET_FIELD.search(name) is not None


def _replace(match: "re.Match") -> str:
    if "secret" not in match.re.groupindex or match.group("secret") is None:
        return REDACTED
    whole, base = match.group(0), match.start()
    return whole[:match.start("secret") - base] + REDACTED + whole[match.end("secret") - base:]


def redact(text: str, key: Optional[str] = None) -> str:
    if not isinstance(text, str) or not text:
        return text
    if key:
        text = text.replace(key, REDACTED)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(_replace, text)
    return text


def _parts(path: str):
    return [p for p in re.split(r"[\\/]+", path) if p not in ("", ".")]


def is_excluded_path(path: str) -> bool:
    """True when `path` falls in one of the shim's exclusion groups (case-insensitive)."""
    parts = [p.lower() for p in _parts(path or "")]
    if not parts:
        return False
    name = parts[-1]
    joined = "/".join(parts) + "/"
    for pattern in EXCLUDE_PATTERNS:
        pattern = pattern.lower()
        if pattern.endswith("/"):
            # a directory group: any component sequence, e.g. `ai/running/` or `node_modules/`
            if ("/" + joined).find("/" + pattern) >= 0:
                return True
        elif fnmatch.fnmatchcase(name, pattern):
            return True
    return False


def clip(text: str, limit: int, tail: bool = False) -> Tuple[str, bool]:
    """Return (text cut to `limit` characters, whether it was cut)."""
    if not isinstance(text, str) or len(text) <= limit:
        return text, False
    return (text[-limit:] if tail else text[:limit]), True


def chunks(text: str, size: int, overlap: int = 200):
    """Split into pieces of `size` characters that overlap by `overlap` (plan 3.5.1)."""
    if len(text) <= size:
        return [text]
    out, start, step = [], 0, size - overlap
    while start < len(text):
        out.append(text[start:start + size])
        if start + size >= len(text):
            break
        start += step
    return out
