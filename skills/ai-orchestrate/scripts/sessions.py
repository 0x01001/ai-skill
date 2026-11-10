#!/usr/bin/env python3
"""Inspect and wait on Claude Code background sessions (sub-sessions).

  status [id|name...]       compact JSON for the given sessions (all background sessions if none)
  last <id|name>            last assistant text from the session's transcript
  usage <id|name...>        tokens by model from each session's transcript and its subagents
  wait <id|name...> [--all] [--timeout <sec>=3600] [--interval <sec>=30]
                            return once any (or, with --all, every) listed session leaves state "working"
"""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

LAUNCH_GRACE_SEC = 60


def list_sessions(timeout=None):
    claude = shutil.which("claude")
    if claude is None:
        raise RuntimeError("claude CLI not found on PATH")
    p = subprocess.Popen(
        [claude, "agents", "--json", "--all"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
        start_new_session=os.name != "nt",
    )
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Kill the whole tree: on Windows claude is a .cmd shim whose node child would keep the pipes open.
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        else:
            os.killpg(p.pid, signal.SIGKILL)
        try:
            p.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        raise
    if p.returncode:
        raise subprocess.CalledProcessError(p.returncode, p.args, out, err)
    return json.loads(out)


def find(sessions, q):
    # Exact id/sessionId first; a reused name resolves to the newest attempt; a sessionId prefix must be unique.
    if not q:
        return None
    exact = [s for s in sessions if q in (s.get("id"), s.get("sessionId"))]
    if exact:
        return exact[0]
    named = [s for s in sessions if s.get("name") == q]
    if named:
        return max(reversed(named), key=lambda s: s.get("startedAt") or 0)  # list is oldest-first
    prefixed = [s for s in sessions if (s.get("sessionId") or "").startswith(q)]
    if len(prefixed) > 1:
        raise ValueError(f"ambiguous session prefix {q!r}: {len(prefixed)} sessions match")
    return prefixed[0] if prefixed else None


def pick(s):
    return {k: s[k] for k in ("id", "name", "sessionId", "cwd", "status", "state") if k in s}


def row(q, s):
    return {"query": q, **(pick(s) if s else {"state": "missing"})}


def is_settled(s, was_seen, elapsed):
    # A session that vanished counts as settled only once it was seen, or after a launch grace period.
    if s is None:
        return was_seen or elapsed > LAUNCH_GRACE_SEC
    if s.get("state"):
        return s["state"] != "working"
    return s.get("status") != "busy"


def emit(data):
    print(json.dumps(data, indent=2, ensure_ascii=False))


def status(queries):
    sessions = list_sessions()
    if queries:
        emit([row(q, find(sessions, q)) for q in queries])
    else:
        emit([pick(s) for s in sessions if s.get("kind") == "background"])


def message_text(content):
    if isinstance(content, list):
        return "".join(c.get("text", "") for c in content if c.get("type") == "text")
    return content if isinstance(content, str) else ""


def transcript_of(query, sessions=None):
    if sessions is None:
        try:
            sessions = list_sessions()
        except (subprocess.CalledProcessError, ValueError, OSError, RuntimeError):
            sessions = []  # CLI unavailable: treat the query as a sessionId
    s = find(sessions, query)
    session_id = (s or {}).get("sessionId") or query
    root = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    transcript = next((p for p in root.glob(f"*/{session_id}.jsonl")), None)
    if transcript is None:
        raise RuntimeError(f"no transcript for {query} (sessionId {session_id})")
    return session_id, transcript


def last(query):
    _, transcript = transcript_of(query)
    for line in reversed(transcript.read_text(encoding="utf-8").splitlines()):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "assistant" or e.get("isSidechain"):
            continue
        text = message_text((e.get("message") or {}).get("content"))
        if text.strip():
            print(text)
            return
    print("(no assistant text yet)")


USAGE_KEYS = {"input": "input_tokens", "cache_read": "cache_read_input_tokens",
              "cache_write": "cache_creation_input_tokens", "output": "output_tokens"}


def usage(queries):
    try:
        sessions = list_sessions()
    except (subprocess.CalledProcessError, ValueError, OSError, RuntimeError):
        sessions = []
    rows = []
    for q in queries:
        # One bad id gets its own error row; the other ids still report.
        try:
            session_id, transcript = transcript_of(q, sessions)
            files = [transcript, *sorted(transcript.with_suffix("").glob("subagents/*.jsonl"))]
            # Each content block of one API message repeats its usage: keep one entry per message id (the last).
            messages = {}
            for f in files:
                for line in f.read_text(encoding="utf-8").splitlines():
                    try:
                        m = json.loads(line).get("message") or {}
                    except (ValueError, AttributeError):
                        continue
                    if isinstance(m, dict) and m.get("id") and m.get("usage") and m.get("model") != "<synthetic>":
                        messages[(f.name, m["id"])] = (m.get("model") or "unknown", m["usage"])
        except (RuntimeError, OSError, ValueError) as err:
            rows.append({"query": q, "error": str(err)})
            continue
        totals = {}
        for model, u in messages.values():
            t = totals.setdefault(model, dict.fromkeys(USAGE_KEYS, 0))
            for k, src in USAGE_KEYS.items():
                t[k] += u.get(src) or 0
        rows.append({"query": q, "sessionId": session_id, "usage": totals})
    emit(rows)
    failed = [r["query"] for r in rows if "error" in r]
    if failed:
        raise RuntimeError(f"usage failed for: {', '.join(failed)}")


def wait(queries, every, timeout, interval):
    seen = set()
    rows, error = [], None
    start = time.monotonic()
    while True:
        try:
            # Bound the CLI call by the time left (at least 1s, so the check at the deadline still runs).
            sessions = list_sessions(timeout=max(timeout - (time.monotonic() - start), 1))
        except subprocess.TimeoutExpired:
            sessions, error = None, "claude agents did not answer before --timeout"
        except (subprocess.CalledProcessError, ValueError, OSError) as err:
            sessions, error = None, str(err)  # transient CLI failure: try again next interval
        elapsed = time.monotonic() - start
        if sessions is not None:
            error = None
            rows = []
            for q in queries:
                s = find(sessions, q)
                rows.append({**row(q, s), "settled": is_settled(s, q in seen, elapsed)})
                if s:
                    seen.add(q)
            done = all(r["settled"] for r in rows) if every else any(r["settled"] for r in rows)
            if done:
                emit({"timedOut": False, "sessions": rows})
                return
        if elapsed >= timeout:
            # rows are from the last successful check; error is set when the last CLI call failed
            emit({"timedOut": True, "sessions": rows, **({"error": error} if error else {})})
            if error:
                raise RuntimeError(f"claude agents failed: {error}")
            return
        time.sleep(min(interval, timeout - elapsed))


def main():
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    parser = argparse.ArgumentParser(prog="sessions.py", description="Inspect and wait on Claude Code background sessions.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="compact JSON for the given sessions (all background sessions if none)")
    p.add_argument("queries", nargs="*", metavar="id|name")
    p = sub.add_parser("last", help="last assistant text from the session's transcript")
    p.add_argument("query", metavar="id|name")
    p = sub.add_parser("usage", help="tokens by model from each session's transcript and its subagents")
    p.add_argument("queries", nargs="+", metavar="id|name")
    p = sub.add_parser("wait", help='return once any (or, with --all, every) session leaves state "working"')
    p.add_argument("queries", nargs="+", metavar="id|name")
    p.add_argument("--all", action="store_true", dest="every")
    p.add_argument("--timeout", type=float, default=3600, help="seconds (default 3600)")
    p.add_argument("--interval", type=float, default=30, help="seconds between checks (default 30)")
    args = parser.parse_args()
    try:
        if args.cmd == "status":
            status(args.queries)
        elif args.cmd == "last":
            last(args.query)
        elif args.cmd == "usage":
            usage(args.queries)
        else:
            wait(args.queries, args.every, args.timeout, args.interval)
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError) as err:
        print(err, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
