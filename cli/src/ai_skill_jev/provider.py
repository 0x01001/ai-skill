"""Provider presets, input validation and the one validation request `ai auth` sends (plan 7).

The CLI's own HTTP is used for validation only. Stdlib, TLS verification on, no redirects,
15 s timeout, bounded response, no retry. Only an HTTP status and a safe reason code ever
leave this module; response bodies are dropped because a provider may echo the key back.
"""

from __future__ import annotations

import json
import math
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from . import __version__

# Defaults of `jg` 0.8.5 (dist/bin/index.js). Prices change; the wizard shows them as "at install time".
PRESETS = {
    "typesafe": {
        "label": "TypeSafe (native API)",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-1.13.0",
        "price": "0.042 USD per 1M input tokens, output tokens free (docs.typesafe.ai, read 2026-11-14)",
    },
    "vercel": {
        "label": "Vercel AI Gateway",
        "base_url": "https://ai-gateway.vercel.sh/typesafe/v1",
        "model": "typesafe-ai/jev",
        "price": "set by Vercel AI Gateway; check its pricing page before use",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model": "typesafe/jev-1.13",
        "price": "set by OpenRouter; check its pricing page before use",
    },
    "opencode": {
        "label": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "model": "jev-1.13",
        "price": "set by OpenCode Zen; check its pricing page before use",
    },
}
PROVIDER_ORDER = ("typesafe", "vercel", "openrouter", "opencode", "custom")

MAX_KEY_BYTES = 8192
MAX_MODEL_CHARS = 200
VALIDATION_TIMEOUT = 15.0
MAX_RESPONSE_BYTES = 64 * 1024
LOOPBACK_HOSTS = ("localhost", "127.0.0.1")

VALIDATION_SOURCE = "export function recordEvent(event) { events.push(event); }"
VALIDATION_QUESTION_ID = "relevant"


class InputError(ValueError):
    """User input that cannot be used. The message never contains the input itself."""


def validate_key(raw: str) -> str:
    key = (raw or "").strip()
    if not key:
        raise InputError("the key is empty")
    if len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise InputError(f"the key is longer than {MAX_KEY_BYTES} bytes")
    if any(not (0x21 <= ord(ch) <= 0x7E) for ch in key):
        raise InputError("the key contains whitespace or characters that are not printable ASCII")
    return key


def validate_model(raw: str) -> str:
    model = (raw or "").strip()
    if not model:
        raise InputError("the model is empty")
    if len(model) > MAX_MODEL_CHARS:
        raise InputError(f"the model is longer than {MAX_MODEL_CHARS} characters")
    if any(not (0x21 <= ord(ch) <= 0x7E) for ch in model):
        raise InputError("the model contains whitespace or characters that are not printable ASCII")
    return model


def is_loopback_http(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and (parts.hostname or "") in LOOPBACK_HOSTS


def validate_base_url(raw: str) -> str:
    """HTTPS base URL, or HTTP on literal localhost/127.0.0.1; no userinfo, query or fragment."""
    url = (raw or "").strip()
    if not url:
        raise InputError("the base URL is empty")
    if any(ch.isspace() or ord(ch) < 0x21 or ord(ch) > 0x7E for ch in url):
        raise InputError("the base URL contains whitespace or non-ASCII characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise InputError("the base URL cannot be parsed") from exc
    del port
    if parts.scheme not in ("https", "http"):
        raise InputError("the base URL must start with https://")
    host = parts.hostname or ""
    if not host:
        raise InputError("the base URL has no host")
    if parts.scheme == "http" and host not in LOOPBACK_HOSTS:
        raise InputError("plain http is only accepted for localhost or 127.0.0.1")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise InputError("the base URL must not contain a user name or password")
    if parts.query or "?" in url:
        raise InputError("the base URL must not contain a query string")
    if parts.fragment or "#" in url:
        raise InputError("the base URL must not contain a fragment")
    return url.rstrip("/")


def host_of(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or ""
    return f"{host}:{parts.port}" if parts.port else host


def resolve_profile(provider: str, base_url: Optional[str] = None, model: Optional[str] = None) -> dict:
    """Return {provider, base_url, model, endpoint_host} for a preset or a validated custom endpoint."""
    if provider in PRESETS:
        preset = PRESETS[provider]
        base, mdl = preset["base_url"], preset["model"]
    elif provider == "custom":
        base, mdl = validate_base_url(base_url or ""), validate_model(model or "")
    else:
        raise InputError(f"unknown provider: {provider}")
    return {"provider": provider, "base_url": base, "model": mdl, "endpoint_host": host_of(base)}


def jg_credentials_payload(profile: dict, key: str) -> dict:
    """`credentials.json` as `jg` 0.8.5 reads it (preset vs custom)."""
    if profile["provider"] == "custom":
        return {"provider": "custom", "baseURL": profile["base_url"], "model": profile["model"], "apiKey": key}
    return {"provider": profile["provider"], "apiKey": key}


@dataclass
class ValidationResult:
    ok: bool
    reason: str  # ok | auth_error | provider_error | timeout | network_error
    http_status: Optional[int] = None
    usage: str = "unknown"

    def describe(self) -> str:
        status = f", HTTP {self.http_status}" if self.http_status else ""
        return f"{self.reason}{status}"


VALIDATION_INSTRUCTIONS = "Does this source implement recording an event?"


def validation_body(model: str) -> dict:
    """The request `jg` 0.8.5 itself sends (`jg doctor` probe, dist/bin/index.js).

    The SDK turns a `boolean` question into `{"type": "noul", "instructions": ...}` with no
    `criteria`; every search question has that shape. Validating with the same shape means an
    endpoint that rejects it (a gateway that requires `criteria.true` answers 422) fails here,
    before `ai auth` enables anything, instead of failing on every search later.
    """
    return {
        "model": model,
        "state": {"source": VALIDATION_SOURCE},
        "questions": {
            VALIDATION_QUESTION_ID: {"type": "noul", "instructions": VALIDATION_INSTRUCTIONS},
        },
    }


def check_validation_response(payload: object, question_ids=(VALIDATION_QUESTION_ID,)) -> Optional[str]:
    """Return a usage note when the response matches the native schema, None when it does not."""
    if not isinstance(payload, dict):
        return None
    answers = payload.get("answers")
    if not isinstance(answers, dict) or set(answers) != set(question_ids):
        return None
    for qid in question_ids:
        answer = answers[qid]
        if not isinstance(answer, dict) or answer.get("type") != "noul":
            return None
        value = answer.get("noul")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return None
        if value <= 0.5:
            return None  # `jg doctor` rejects this probe's answer at <= 0.5 too
    usage = payload.get("usage")
    if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
        out = usage.get("output_tokens")
        return f"input_tokens={usage['input_tokens']}, output_tokens={out if isinstance(out, int) else 'unknown'}"
    return "unknown"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib API
        return None


def _opener(url: str) -> urllib.request.OpenerDirector:
    handlers = [_NoRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context())]
    if is_loopback_http(url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def validate_credentials(base_url: str, model: str, key: str, timeout: float = VALIDATION_TIMEOUT) -> ValidationResult:
    """POST one synthetic Noul request (no user source) to `<base_url>/systemone`."""
    url = f"{base_url}/systemone"
    data = json.dumps(validation_body(model)).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {key}",
            "User-Agent": f"ai-skill-jev/{__version__}",
        },
    )
    try:
        with _opener(url).open(request, timeout=timeout) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        reason = "auth_error" if status in (401, 403) else "provider_error"
        return ValidationResult(False, reason, status)
    except (socket.timeout, TimeoutError):
        return ValidationResult(False, "timeout")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            return ValidationResult(False, "timeout")
        return ValidationResult(False, "network_error")
    except (OSError, ValueError):
        return ValidationResult(False, "network_error")
    if status != 200 or len(body) > MAX_RESPONSE_BYTES:
        return ValidationResult(False, "provider_error", status)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ValidationResult(False, "provider_error", status)
    usage = check_validation_response(payload)
    if usage is None:
        return ValidationResult(False, "provider_error", status)
    return ValidationResult(True, "ok", status, usage)
