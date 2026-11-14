"""The `jg` shim: the only path from the skills (ai-* and upstream jevgrep) to Jev (plan 6.5).

- `ai auth` never ran, or `ai logout` finished: no Jev key, so `jg` is not used at all. Every
  command falls back (`reason=no_key`, exit 3) without running the real `jg`; callers keep the
  normal workflow. `jg auth` is always blocked: the key only comes from `ai auth`.
- A key is configured: `--help`, `--version`, `files`, `skill`, `cache` and
  bare `jg` run without credentials; search and `jg doctor` pass the gate, then run with
  temporary credentials built from the OS secret store.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from . import EXIT_FALLBACK, JG_INSTALL_COMMAND
from .credentials import (KeyringStore, StateError, atomic_write_json, is_active,
                          load_metadata, now_iso, read_key)
from .jg_runner import SHIM_ENV, jg_command, prepare_search_args, run_jg, split_command
from .pathenv import lookup_jg
from .provider import jg_credentials_payload

RUN_DIR_FLAG = "--ai-run-dir"
NO_CREDENTIAL_COMMANDS = {"files", "skill", "cache"}
BUDGET_FILE = "jev-budget.json"
BUDGET_LOCK = "jev-budget.lock"
BUDGET_LIMIT = 3
LOCK_WAIT = 5.0
LOCK_STALE = 30.0

AUTH_BLOCKED = ("ai-skill jev: the Jev key is managed by `ai auth`; run `ai auth` in a terminal. "
                "Never paste the key into chat.")


class Fallback(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def strip_run_dir(argv: Sequence[str]) -> Tuple[List[str], Optional[str]]:
    """Remove every `--ai-run-dir X` / `--ai-run-dir=X` (before a `--` terminator)."""
    out: List[str] = []
    run_dir = None
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--":
            out.extend(argv[i:])
            break
        if token == RUN_DIR_FLAG:
            run_dir = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
            continue
        if token.startswith(RUN_DIR_FLAG + "="):
            run_dir = token.split("=", 1)[1]
            i += 1
            continue
        out.append(token)
        i += 1
    return out, run_dir


# --------------------------------------------------------------------------- budget

def _acquire_lock(lock: Path) -> int:
    """Take the budget lock, or fall back after LOCK_WAIT whatever blocks it.

    Every iteration passes the deadline check, so nothing can spin past LOCK_WAIT. A stale lock
    (older than LOCK_STALE) that we manage to delete is retried without sleeping. A held lock, a
    lock whose mtime cannot be read or that cannot be deleted, and a create that fails with
    another OSError (on Windows a lock still pending delete reports access denied) all wait.
    """
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        removed_stale = False
        try:
            return os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > LOCK_STALE:
                    os.unlink(lock)
                    removed_stale = True
            except OSError:
                pass  # gone, unreadable or undeletable: wait like a held lock
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise Fallback("budget", "budget counter is locked")
        if not removed_stale:
            time.sleep(0.05)


def _with_budget(run_dir: str, update: Callable[[dict], None]) -> dict:
    directory = Path(run_dir)
    if not run_dir or not directory.is_dir():
        raise Fallback("budget", "run dir not found")
    lock = directory / BUDGET_LOCK
    fd = _acquire_lock(lock)
    try:
        path = directory / BUDGET_FILE
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("calls"), list):
                raise ValueError
        except FileNotFoundError:
            data = {"limit": BUDGET_LIMIT, "used": 0, "calls": []}
        except (OSError, ValueError):
            raise Fallback("budget", "budget file is unreadable")
        update(data)
        atomic_write_json(path, data)
        return data
    finally:
        os.close(fd)
        try:
            os.unlink(lock)
        except OSError:
            pass


def consume_budget(run_dir: str) -> int:
    """Count one search before it starts (unknown outcomes stay counted). Returns its index."""
    index = {}

    def update(data: dict) -> None:
        limit = data.get("limit", BUDGET_LIMIT)
        used = data.get("used", 0)
        if not isinstance(limit, int) or not isinstance(used, int) or used >= limit:
            raise Fallback("budget", f"run budget used ({used}/{limit})")
        data["used"] = used + 1
        data["calls"].append({"at": now_iso(), "status": "started"})
        index["i"] = len(data["calls"]) - 1

    _with_budget(run_dir, update)
    return index["i"]


def record_budget(run_dir: str, index: int, status: str, coverage: Optional[str]) -> None:
    def update(data: dict) -> None:
        if 0 <= index < len(data["calls"]):
            data["calls"][index].update({"status": status, "coverage": coverage, "ended": now_iso()})

    try:
        _with_budget(run_dir, update)
    except Fallback:
        pass


# --------------------------------------------------------------------------- main flow

def _write(stream, data: bytes) -> None:
    if data:
        stream.write(data)
        stream.flush()


def _say(err, text: str) -> None:
    _write(err, (text + "\n").encode("utf-8"))


def _fallback_message(reason: str, detail: str = "") -> str:
    text = f"ai-skill jev: unavailable (reason={reason}). Use grep/read files instead."
    if reason == "policy" and detail:
        text += f"\nai-skill jev: refused: {detail}. Do not retry with other flags or roots."
    if reason == "no_key":
        text += "\nai-skill jev: Jev is off (no key); the user turns it on with `ai auth` in a terminal."
    if reason == "auth_error":
        text += "\nai-skill jev: the provider rejected the Jev key; the user runs `ai auth` again in a terminal."
    if reason == "engine_missing":
        text += f"\nai-skill jev: jg not found; install: {JG_INSTALL_COMMAND}"
    return text


def run(argv: Sequence[str], env, *, store_factory=KeyringStore.by_name, out=None, err=None) -> int:
    out = out if out is not None else sys.stdout.buffer
    err = err if err is not None else sys.stderr.buffer
    try:
        return _run(argv, env, store_factory, out, err)
    except Fallback as exc:
        _say(err, _fallback_message(exc.reason, exc.detail))
        return EXIT_FALLBACK


def _run(argv, env, store_factory, out, err) -> int:
    if env.get(SHIM_ENV) == "1":
        raise Fallback("engine_missing", "the real jg resolved back to the shim")
    args, run_dir = strip_run_dir(argv)
    real = lookup_jg(env).real
    try:
        meta = load_metadata(env)
    except StateError as exc:
        raise Fallback(exc.reason, str(exc))
    command, info = split_command(args)
    if command == "auth":  # with or without a key: the only way in is `ai auth`
        _say(err, AUTH_BLOCKED)
        return EXIT_FALLBACK
    if not is_active(meta):
        # No Jev key: jg is not used at all; the caller keeps the normal workflow.
        raise Fallback("no_key")
    if real is None:
        raise Fallback("engine_missing")
    cmd = jg_command(real, env)

    try:
        if info or command is None or command in NO_CREDENTIAL_COMMANDS:
            result = run_jg(cmd, args, env=env)
            _write(out, result.stdout)
            _write(err, result.stderr)
            if result.reason:
                raise Fallback(result.reason)
            return result.exit_code

        search = command != "doctor"
        # Policy first: a refused search never reads the key, never spends budget, never runs jg.
        jg_args = prepare_search_args(args, os.getcwd()) if search else list(args)
        # No version gate: whatever jg is installed runs (upstream updates it); a jg that cannot
        # start surfaces as OSError -> engine_missing below.
        key = read_key(meta, store_factory)
        credentials = jg_credentials_payload(meta, key)
        budget_index = consume_budget(run_dir) if (search and run_dir is not None) else None
        try:
            result = run_jg(cmd, jg_args, env=env, credentials=credentials, key=key, search=search)
        finally:
            del key, credentials
        if budget_index is not None:
            record_budget(run_dir, budget_index, result.reason or f"exit={result.exit_code}", result.coverage)
    except StateError as exc:
        raise Fallback(exc.reason, str(exc))
    except OSError as exc:
        raise Fallback("engine_missing", exc.__class__.__name__)

    if result.reason:
        raise Fallback(result.reason)
    _write(out, result.stdout)
    _write(err, result.stderr)
    if search:
        _say(err, f"ai-skill jev: coverage={result.coverage} (jg exit={result.exit_code})")
    return result.exit_code


def main() -> int:
    try:
        return run(sys.argv[1:], os.environ)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
