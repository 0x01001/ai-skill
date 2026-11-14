"""Provider presets, input validation and the CLI's own HTTP to `<base_url>/systemone` (plan 7).

Two callers: the one validation request `ai auth` sends (15 s, no retry) and `post_systemone`
for `ai decide` (20 s per attempt, bounded 429/529 retries, a deadline). Both use stdlib, TLS
verification on, no redirects and a bounded response. A response body that contains the key is
dropped as `provider_error`; only parsed answers, an HTTP status and a reason code leave here.
"""

from __future__ import annotations

import http.client
import json
import math
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional, Tuple
from urllib.parse import urlsplit

from . import __version__

# Defaults of `jg` 0.8.5 (dist/bin/index.js). Prices change; the wizard shows them as "at install time".
PRESETS = {
    "typesafe": {
        "label": "TypeSafe (native API)",
        "base_url": "https://api.typesafe.ai/v1",
        "model": "jev-1.14.0",
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
DECIDE_ATTEMPT_TIMEOUT = 20.0
RETRY_STATUSES = (429, 529)
DEFAULT_RETRY_AFTER = 1.0
MAX_RESPONSE_BYTES = 64 * 1024
ABORT_SLACK = 0.1  # seconds past the deadline the attempt gets to end on its own before it is cut
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


class _Watch:
    """The socket of one attempt, so the caller can cut it once the attempt's time is up."""

    def __init__(self):
        self.lock = threading.Lock()
        self.sock: Optional[socket.socket] = None
        self.aborted = False

    def attach(self, sock):
        with self.lock:
            self.sock = sock
            if self.aborted:
                self._cut()
        return sock

    def abort(self) -> None:
        with self.lock:
            self.aborted = True
            self._cut()

    def _cut(self) -> None:
        if self.sock is None:
            return
        try:  # the plain socket call: SSLSocket.shutdown would race the read in the other thread
            socket.socket.shutdown(self.sock, socket.SHUT_RDWR)
        except OSError:  # already closed, or the TCP socket the TLS wrap took over (then it is re-attached)
            pass


def _watched(base, watch: _Watch):
    class Connection(base):
        def connect(self):
            self._create_connection = lambda *a, **kw: watch.attach(socket.create_connection(*a, **kw))
            super().connect()
            watch.attach(self.sock)

    return Connection


def _watched_https(watch: _Watch):
    class Connection(http.client.HTTPSConnection):
        def connect(self):
            """HTTPSConnection.connect, with the handshake run on a socket the watch can already cut.

            The stock `wrap_socket` detaches the TCP socket and shakes hands before it returns, so a
            server trickling handshake bytes could not be cut until it ended.
            """
            self._create_connection = lambda *a, **kw: watch.attach(socket.create_connection(*a, **kw))
            http.client.HTTPConnection.connect(self)
            server_hostname = self._tunnel_host or self.host
            sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname,
                                             do_handshake_on_connect=False)
            self.sock = watch.attach(sock)
            sock.do_handshake()

    return Connection


class _HTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, watch: _Watch):
        super().__init__()
        self._watch = watch

    def http_open(self, req):
        return self.do_open(_watched(http.client.HTTPConnection, self._watch), req)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, watch: _Watch):
        super().__init__(context=ssl.create_default_context())
        self._watch = watch

    def https_open(self, req):
        return self.do_open(_watched_https(self._watch), req, context=self._context)


def _opener(url: str, watch: Optional[_Watch] = None) -> urllib.request.OpenerDirector:
    watch = watch or _Watch()
    handlers = [_NoRedirect(), _HTTPHandler(watch), _HTTPSHandler(watch)]
    if is_loopback_http(url):
        handlers.append(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(*handlers)


def _headers(key: str) -> dict:
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": f"ai-skill-jev/{__version__}",
    }


def contains_key(data: bytes, key: Optional[str]) -> bool:
    """True when `data` holds the key, raw or JSON-escaped."""
    if not key:
        return False
    escaped = json.dumps(key)[1:-1]
    return key.encode("utf-8") in data or escaped.encode("utf-8") in data


def _read_body(response, end: float) -> bytes:
    """Read at most MAX_RESPONSE_BYTES + 1 bytes, all of it before `end` (a time.monotonic() value).

    `timeout` on the socket bounds each read, not the whole body: a server that sends one byte
    per second would otherwise hold the call far past its deadline.
    """
    sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
    read = getattr(response, "read1", None) or response.read
    parts, total = [], 0
    while total <= MAX_RESPONSE_BYTES:
        left = end - time.monotonic()
        if left <= 0:
            raise socket.timeout("response body past the deadline")
        if sock is not None:
            try:
                sock.settimeout(left)
            except OSError:  # already closed by urllib; what is left is in the buffer
                sock = None
        chunk = read(min(65536, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        parts.append(chunk)
        total += len(chunk)
    return b"".join(parts)


def _send_once(url: str, data: bytes, key: str, timeout: float) -> Tuple[Optional[int], bytes, dict, str]:
    """One POST, all of it (connect, TLS, status line, headers, body) within `timeout` seconds.

    Returns (status, body, headers, reason); reason is "" when a response arrived. The socket
    timeout bounds each read only, and `_read_body` cannot see the header phase: a server that
    sends a header line now and then would hold `open()` far past the deadline. So the attempt
    runs on a daemon thread that the caller waits for until the deadline, then cuts.
    """
    end = time.monotonic() + timeout
    watch = _Watch()
    box: dict = {}

    def attempt() -> None:
        try:
            box["out"] = _attempt(url, data, key, timeout, end, watch)
        except BaseException as exc:  # re-raised in the caller, as if called directly
            box["error"] = exc

    worker = threading.Thread(target=attempt, name="ai-post", daemon=True)
    worker.start()
    worker.join(max(0.0, end - time.monotonic()) + ABORT_SLACK)
    if worker.is_alive():
        watch.abort()  # the worker unblocks and ends on its own; whatever it gets is dropped
        return None, b"", {}, "timeout"
    if "error" in box:
        raise box["error"]
    return box["out"]


def _attempt(url: str, data: bytes, key: str, timeout: float, end: float,
             watch: _Watch) -> Tuple[Optional[int], bytes, dict, str]:
    request = urllib.request.Request(url, data=data, method="POST", headers=_headers(key))
    try:
        with _opener(url, watch).open(request, timeout=timeout) as response:
            return response.status, _read_body(response, end), dict(response.headers), ""
    except urllib.error.HTTPError as exc:
        headers = dict(exc.headers or {})
        exc.close()
        return exc.code, b"", headers, ""
    except (socket.timeout, TimeoutError):
        return None, b"", {}, "timeout"
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            return None, b"", {}, "timeout"
        return None, b"", {}, "network_error"
    except (OSError, ValueError, http.client.HTTPException):
        # HTTPException: a malformed status line or header (BadStatusLine, LineTooLong, ...)
        return None, b"", {}, "network_error"


def validate_credentials(base_url: str, model: str, key: str, timeout: float = VALIDATION_TIMEOUT) -> ValidationResult:
    """POST one synthetic Noul request (no user source) to `<base_url>/systemone`."""
    data = json.dumps(validation_body(model)).encode("utf-8")
    status, body, _, reason = _send_once(f"{base_url}/systemone", data, key, timeout)
    if reason:
        return ValidationResult(False, reason)
    if status in (401, 403):
        return ValidationResult(False, "auth_error", status)
    if status != 200 or len(body) > MAX_RESPONSE_BYTES or contains_key(body, key):
        return ValidationResult(False, "provider_error", status)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return ValidationResult(False, "provider_error", status)
    usage = check_validation_response(payload)
    if usage is None:
        return ValidationResult(False, "provider_error", status)
    return ValidationResult(True, "ok", status, usage)


# --------------------------------------------------------------------------- ai decide

@dataclass
class PostResult:
    ok: bool
    reason: str  # ok | auth_error | provider_error | timeout | budget | cancelled
    http_status: Optional[int] = None
    payload: Optional[dict] = None
    attempts: int = 0  # requests actually sent (each one is budget spent)


def _retry_after(headers: dict) -> float:
    for name, value in headers.items():
        if name.lower() == "retry-after":
            try:
                seconds = float(str(value).strip())
            except ValueError:
                return DEFAULT_RETRY_AFTER
            return seconds if math.isfinite(seconds) and seconds >= 0 else DEFAULT_RETRY_AFTER
    return DEFAULT_RETRY_AFTER


def post_systemone(base_url: str, model: str, key: str, body: dict, *,
                   timeout: float = DECIDE_ATTEMPT_TIMEOUT, deadline: Optional[float] = None,
                   retry_429: int = 0, before_retry: Optional[Callable[[], bool]] = None,
                   cancel=None, clock: Callable[[], float] = time.monotonic,
                   sleep: Callable[[float], None] = time.sleep) -> PostResult:
    """POST `body` (its `model` is set here) and return the parsed JSON object on HTTP 200.

    - 401/403: auth_error, never retried. Another status, bad JSON, an oversized body or a body
      that contains the key: provider_error.
    - 429/529: retried up to `retry_429` times after `retry-after` (1 s when absent), only when
      the wait still fits before `deadline` and, after the wait, `before_retry()` (one more unit
      of budget) agrees.
    - `deadline` (a `clock()` value) caps every attempt; nothing is sent past it (timeout).
    - `cancel` (a threading.Event) is checked before each attempt and during waits (cancelled).
    """
    url = f"{base_url}/systemone"
    data = json.dumps(dict(body, model=model)).encode("utf-8")
    attempts = 0
    status: Optional[int] = None
    while True:
        if cancel is not None and cancel.is_set():
            return PostResult(False, "cancelled", status, None, attempts)
        allowed = timeout if deadline is None else min(timeout, deadline - clock())
        if allowed <= 0:
            return PostResult(False, "timeout", status, None, attempts)
        attempts += 1
        status, raw, headers, reason = _send_once(url, data, key, allowed)
        if reason == "timeout":
            return PostResult(False, "timeout", None, None, attempts)
        if reason:
            return PostResult(False, "provider_error", None, None, attempts)
        if status in (401, 403):
            return PostResult(False, "auth_error", status, None, attempts)
        if status in RETRY_STATUSES:
            if attempts > retry_429:
                return PostResult(False, "provider_error", status, None, attempts)
            wait = _retry_after(headers)
            if deadline is not None and clock() + wait >= deadline:
                return PostResult(False, "timeout", status, None, attempts)
            if cancel is not None:
                if cancel.wait(wait):
                    return PostResult(False, "cancelled", status, None, attempts)
            else:
                sleep(wait)
            if cancel is not None and cancel.is_set():
                return PostResult(False, "cancelled", status, None, attempts)
            if before_retry is not None and not before_retry():  # the unit is taken right before sending
                return PostResult(False, "budget", status, None, attempts)
            continue
        if status != 200 or len(raw) > MAX_RESPONSE_BYTES or contains_key(raw, key):
            return PostResult(False, "provider_error", status, None, attempts)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return PostResult(False, "provider_error", status, None, attempts)
        if not isinstance(payload, dict):
            return PostResult(False, "provider_error", status, None, attempts)
        return PostResult(True, "ok", status, payload, attempts)
