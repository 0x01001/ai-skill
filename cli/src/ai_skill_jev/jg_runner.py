"""Run the real `jg` with temporary credentials (plan 6.2, 8.1, 8.3).

Each call gets a fresh private dir holding `jevgrep/credentials.json`, pointed to by
XDG_CONFIG_HOME for the child only. The dir is removed in `finally`; after a timeout the whole
process tree is killed first. Output that contains the key is dropped.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

from . import EXIT_FALLBACK
from .credentials import StateError, ensure_private_dir, run_root, write_private_file

DEADLINE_SECONDS = 90.0
VERSION_TIMEOUT = 20.0
DEFAULT_MAX_REQUESTS = "100"
END_MARKER = b"End context."
# jg ends a byte-limited result with this line before the footer: the context is cut short.
TRUNCATED_MARKER = b"Output truncated at the byte limit"
# Plan 6.4: groups never sent to the provider, on top of jg's own defaults (hidden files,
# dependency/build dirs, binary, .env, private keys, credentials.json, sensitive content).
EXCLUDE_PATTERNS = (
    ".env*", "*.pem", "*.key", "*.p12", "*.pfx", "id_*", "*credentials*",
    "*.log", "ai/running/", "venv/", "node_modules/",
)
DROPPED_ENV = ("TYPESAFE_API_KEY", "TYPESAFE_AI_API_KEY")
SHIM_ENV = "AI_SKILL_JG_SHIM"
NPM_ENTRY = ("node_modules", "@dzhng", "jevgrep", "dist", "bin", "index.js")
CMD_METACHARS = set('"%^&|<>!()\r\n')


@dataclass
class RunResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    reason: Optional[str] = None      # None, timeout, auth_error, provider_error
    coverage: Optional[str] = None    # complete | partial (search only)


# --------------------------------------------------------------------------- command

def jg_command(real_jg: str, env) -> List[str]:
    """argv prefix for the real `jg`. On Windows the npm `.cmd` is bypassed (no cmd.exe parsing)."""
    if sys.platform == "win32" and real_jg.lower().endswith((".cmd", ".bat")):
        directory = os.path.dirname(real_jg)
        entry = os.path.join(directory, *NPM_ENTRY)
        if os.path.isfile(entry):
            local_node = os.path.join(directory, "node.exe")
            node = local_node if os.path.isfile(local_node) else shutil.which("node", path=env.get("PATH"))
            if node:
                return [node, entry]
    return [real_jg]


def check_cmd_args(cmd: Sequence[str], args: Sequence[str]) -> None:
    """A `.cmd` that is not the npm layout runs through cmd.exe; refuse arguments it would reinterpret."""
    if sys.platform == "win32" and cmd and cmd[0].lower().endswith((".cmd", ".bat")):
        for arg in args:
            if any(ch in CMD_METACHARS for ch in arg):
                raise StateError("engine_missing",
                                 "argument contains characters cmd.exe would reinterpret; jg layout not supported")


# --------------------------------------------------------------------------- argv

VALUE_OPTIONS = {
    "provider", "base-url", "model", "agent", "concurrency", "max-requests",
    "max-source-bytes", "max-output-bytes", "exclude",
}


def split_command(args: Sequence[str]) -> tuple:
    """(first positional or None, has_help_or_version) following jg's parseArgs rules."""
    first = None
    info = False
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            if first is None and i + 1 < len(args):
                first = args[i + 1]
            break
        if token.startswith("--"):
            name = token[2:].split("=", 1)[0]
            if name in ("help", "version"):
                info = True
            if name in VALUE_OPTIONS and "=" not in token:
                i += 1
        elif token.startswith("-") and len(token) > 1:
            if "h" in token[1:]:
                info = True
        elif first is None:
            first = token
        i += 1
    return first, info


# Flags that widen what jg sends past the exclusions (plan 6.4): hidden files, ignored files,
# dependency dirs, and jg's own secret-content filter. jg 0.8.5 has no short aliases for them.
BLOCKED_SEARCH_FLAGS = ("include-sensitive", "hidden", "no-ignore", "include-dependencies")
MAX_REQUESTS_CAP = int(DEFAULT_MAX_REQUESTS)


def _policy(detail: str) -> StateError:
    return StateError("policy", detail)


def _git_root(path: str) -> Optional[str]:
    """Nearest ancestor of `path` (itself included) holding `.git` (a dir, or a file in worktrees)."""
    current = path
    while True:
        if os.path.lexists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _is_within(path: str, base: str) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(path), os.path.normcase(base)]) == os.path.normcase(base)
    except ValueError:  # different drives
        return False


def check_search_root(root: Optional[str], cwd: str) -> str:
    """The search root must lie inside the git repo of the working directory (the search scope)."""
    base_cwd = os.path.realpath(cwd)
    repo = _git_root(base_cwd)
    if repo is None:
        raise _policy("the working directory is not inside a git repo")
    target = os.path.realpath(os.path.join(base_cwd, root)) if root is not None else base_cwd
    if not os.path.exists(target):
        raise _policy("the search root does not exist")
    if not _is_within(target, repo):
        raise _policy("the search root is outside the git repo of the working directory")
    return target


def prepare_search_args(args: Sequence[str], cwd: Optional[str] = None) -> List[str]:
    """Apply the search policy, then add the exclude groups before a `--` terminator if any.

    Refuses (StateError "policy") the flags in BLOCKED_SEARCH_FLAGS, a `--max-requests` that is
    not a positive integer, and a root outside the git repo of `cwd`. Clamps `--max-requests` to
    MAX_REQUESTS_CAP and adds it when absent. Options are read the way jg's parseArgs reads them:
    a value option takes the next token, and every token after `--` is positional.
    """
    args = list(args)
    out: List[str] = []
    positionals: List[str] = []
    has_cap = False
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--":
            positionals.extend(args[i + 1:])
            break
        if token.startswith("--"):
            name, eq, value = token[2:].partition("=")
            if name in BLOCKED_SEARCH_FLAGS:
                raise _policy(f"--{name} is not allowed through ai-skill")
            if name == "max-requests":
                if not eq:
                    if i + 1 >= len(args):
                        raise _policy("--max-requests needs a value")
                    value = args[i + 1]
                    i += 1
                if not value.isdigit() or not value.isascii() or int(value) < 1:
                    raise _policy("--max-requests must be a positive integer")
                out += ["--max-requests", str(min(int(value), MAX_REQUESTS_CAP))]
                has_cap = True
                i += 1
                continue
            out.append(token)
            # jg (strict parseArgs) rejects a value starting with "-" as ambiguous; not taking it
            # here keeps `--exclude --hidden` from slipping a blocked flag past the check.
            if name in VALUE_OPTIONS and not eq and i + 1 < len(args) and not args[i + 1].startswith("-"):
                out.append(args[i + 1])
                i += 1
        else:
            if not (token.startswith("-") and len(token) > 1):
                positionals.append(token)
            out.append(token)
        i += 1
    check_search_root(positionals[1] if len(positionals) > 1 else None, os.getcwd() if cwd is None else cwd)

    rest = args[i:]  # the `--` terminator and what follows, untouched
    extra: List[str] = []
    if not has_cap:
        extra += ["--max-requests", DEFAULT_MAX_REQUESTS]
    for pattern in EXCLUDE_PATTERNS:
        extra += ["--exclude", pattern]
    return out + extra + rest


# --------------------------------------------------------------------------- env, dirs, processes

def child_env(env, config_home: str) -> dict:
    out = {k: v for k, v in env.items() if k.upper() not in DROPPED_ENV and k.upper() != "XDG_CONFIG_HOME"}
    out["XDG_CONFIG_HOME"] = config_home
    out[SHIM_ENV] = "1"
    return out


def prepare_root(env) -> Path:
    root = run_root(env)
    ensure_private_dir(root, top=root.parent)
    return root


def cleanup_orphans(root: Path, max_age: float = DEADLINE_SECONDS + 60) -> int:
    """Remove run dirs left behind by a crash. Returns the number removed."""
    removed = 0
    cutoff = time.time() - max_age
    try:
        entries = list(os.scandir(root))
    except OSError:
        return 0
    for entry in entries:
        if not entry.name.startswith("run-"):
            continue
        try:
            if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                _rmtree(Path(entry.path))
                removed += 1
        except OSError:
            continue
    return removed


def _rmtree(path: Path) -> None:
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(0.2 * (attempt + 1))
    shutil.rmtree(path, ignore_errors=True)


def _popen(argv: List[str], env: dict) -> subprocess.Popen:
    kwargs = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(argv, **kwargs)


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _scrub_needles(key: Optional[str]) -> List[bytes]:
    if not key:
        return []
    escaped = json.dumps(key)[1:-1]
    return list({key.encode("utf-8"), escaped.encode("utf-8")})


# --------------------------------------------------------------------------- run

def run_jg(cmd: Sequence[str], args: Sequence[str], *, env, credentials: Optional[dict] = None,
           key: Optional[str] = None, search: bool = False, timeout: float = DEADLINE_SECONDS) -> RunResult:
    """Run `cmd + args` with a private XDG_CONFIG_HOME (with or without credentials)."""
    check_cmd_args(cmd, args)
    root = prepare_root(env)
    cleanup_orphans(root)
    run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    proc = None
    try:
        if credentials is not None:
            cred_dir = run_dir / "jevgrep"
            os.mkdir(cred_dir, 0o700)
            write_private_file(cred_dir / "credentials.json", json.dumps(credentials).encode("utf-8"))
        proc = _popen(list(cmd) + list(args), child_env(env, str(run_dir)))
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            try:
                proc.communicate(timeout=10)
            except (subprocess.TimeoutExpired, ValueError):
                pass
            return RunResult(EXIT_FALLBACK, b"", b"", reason="timeout", coverage="partial" if search else None)
        except BaseException:
            kill_tree(proc)
            raise
    finally:
        if proc is not None and proc.poll() is None:
            kill_tree(proc)
        _rmtree(run_dir)
    for needle in _scrub_needles(key):
        if needle in stdout or needle in stderr:
            return RunResult(EXIT_FALLBACK, b"", b"", reason="provider_error",
                             coverage="partial" if search else None)
    coverage = None
    if search:
        if proc.returncode != 0 and not stdout.strip():
            # jg produced no context at all (rejected key, provider down): a fallback, not a result.
            return RunResult(EXIT_FALLBACK, b"", b"", reason=failure_reason(stderr), coverage="partial")
        coverage = "complete" if proc.returncode == 0 and is_complete(stdout) else "partial"
    return RunResult(proc.returncode, stdout, stderr, coverage=coverage)


# jg 0.8.x: "Jev evaluation failed: authentication", "AI Gateway authentication failed: Invalid
# API key or token.", "Invalid credentials. Run jg auth again.", provider "HTTP 401: ...".
AUTH_FAILURE = re.compile(
    rb"\b40[13]\b|unauthori[sz]ed|forbidden|authenticat|invalid (?:api )?(?:key|token)|invalid credentials",
    re.IGNORECASE)


def failure_reason(stderr: bytes) -> str:
    """Reason code for a search that failed without output: auth_error or provider_error."""
    return "auth_error" if AUTH_FAILURE.search(stderr) else "provider_error"


def is_complete(stdout: bytes) -> bool:
    """True only when jg's own footer is the last line and the output was not cut short.

    A quoted source excerpt may contain "End context." anywhere, so a substring match
    would call a cut-off result complete.
    """
    lines = stdout.rstrip().splitlines()
    if not lines or lines[-1] != END_MARKER:
        return False
    return not any(line.startswith(TRUNCATED_MARKER) for line in lines[-3:-1])


def jg_version(cmd: Sequence[str], env) -> Optional[str]:
    """`jg --version` without any credentials; None when it cannot be run."""
    try:
        result = run_jg(cmd, ["--version"], env=env, timeout=VERSION_TIMEOUT)
    except (OSError, StateError):
        return None
    if result.exit_code != 0:
        return None
    text = result.stdout.decode("utf-8", "replace").strip().splitlines()
    return text[0].strip() if text else None
