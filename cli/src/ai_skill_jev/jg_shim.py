"""The `jg` shim: the only path from the skills (ai-* and upstream jevgrep) to Jev (plan 6.5).

- `ai auth` never ran, or `ai logout` finished: no Jev key, so `jg` is not used at all. Every
  command falls back (`reason=no_key`, exit 3) without running the real `jg`; callers keep the
  normal workflow. `jg auth` is always blocked: the key only comes from `ai auth`.
- A key is configured: `--help`, `--version`, `files`, `skill`, `cache` and
  bare `jg` run without credentials; search and `jg doctor` pass the gate, then run with
  temporary credentials built from the OS secret store.
"""

from __future__ import annotations

import os
import sys
from typing import List, Optional, Sequence, Tuple

from . import EXIT_FALLBACK, budget
from .budget import BUDGET_FILE, BUDGET_LOCK  # noqa: F401  (re-exported for callers and tests)
from .credentials import KeyringStore, StateError, is_active, load_metadata, read_key
from .engine import find_engine, install_command
from .jg_runner import SHIM_ENV, prepare_search_args, run_jg, split_command
from .provider import jg_credentials_payload

RUN_DIR_FLAG = "--ai-run-dir"
NO_CREDENTIAL_COMMANDS = {"files", "skill", "cache"}

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

def consume_budget(run_dir: str) -> int:
    """Count one search in the shared run budget (plan 3.6); a refusal becomes a fallback."""
    try:
        return budget.consume_search(run_dir)
    except budget.BudgetError as exc:
        raise Fallback("budget", str(exc))


def record_budget(run_dir: str, index: int, status: str, coverage: Optional[str]) -> None:
    budget.record_search(run_dir, index, status, coverage)


# --------------------------------------------------------------------------- main flow

def _write(stream, data: bytes) -> None:
    if data:
        stream.write(data)
        stream.flush()


def _say(err, text: str) -> None:
    _write(err, (text + "\n").encode("utf-8"))


def _fallback_message(reason: str, detail: str = "", env=None) -> str:
    text = f"ai-skill jev: unavailable (reason={reason}). Use grep/read files instead."
    if reason == "policy" and detail:
        text += f"\nai-skill jev: refused: {detail}. Do not retry with other flags or roots."
    if reason == "no_key":
        text += "\nai-skill jev: Jev is off (no key); the user turns it on with `ai auth` in a terminal."
    if reason == "auth_error":
        text += "\nai-skill jev: the provider rejected the Jev key; the user runs `ai auth` again in a terminal."
    if reason == "engine_missing":
        text += f"\nai-skill jev: jg engine not found; the user installs it with: {install_command(env)}"
    return text


def run(argv: Sequence[str], env, *, store_factory=KeyringStore.by_name, out=None, err=None) -> int:
    out = out if out is not None else sys.stdout.buffer
    err = err if err is not None else sys.stderr.buffer
    try:
        return _run(argv, env, store_factory, out, err)
    except Fallback as exc:
        _say(err, _fallback_message(exc.reason, exc.detail, env))
        return EXIT_FALLBACK


def _run(argv, env, store_factory, out, err) -> int:
    if env.get(SHIM_ENV) == "1":
        raise Fallback("engine_missing", "the real jg resolved back to the shim")
    args, run_dir = strip_run_dir(argv)
    engine = find_engine(env)
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
    if engine is None:
        raise Fallback("engine_missing")
    cmd = engine.cmd

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
