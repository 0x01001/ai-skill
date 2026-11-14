"""Per-run Jev budget in `<run-dir>/jev-budget.json`, several counters under one file lock (plan 3.6).

- `search`: `jg ... --ai-run-dir` searches, shared by the coordinator and every worker.
- `decide`: HTTP attempts sent to `/systemone` by `ai decide ... --ai-run-dir` (retries count).
- `decide_reserved`: attempts kept for the safety kinds (guard, claims, check_output,
  finding_scope); they spend it only once `decide` is used up.
- `usage`: totals for the final report only; no decision reads them.

A file written by the old shim (`{"limit", "used", "calls"}`, one counter) is read as `search`.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .credentials import atomic_write_json, now_iso

BUDGET_FILE = "jev-budget.json"
BUDGET_LOCK = "jev-budget.lock"
LOCK_WAIT = 5.0

SEARCH_LIMIT = 12
DECIDE_LIMIT = 400
DECIDE_RESERVED_LIMIT = 100
LIMITS = {"search": SEARCH_LIMIT, "decide": DECIDE_LIMIT, "decide_reserved": DECIDE_RESERVED_LIMIT}
DECIDE_COUNTERS = ("decide", "decide_reserved")


class BudgetError(Exception):
    """The budget refuses or cannot be read; always reason `budget`."""

    reason = "budget"


# --------------------------------------------------------------------------- lock and file

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # raises OSError while another handle holds it

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


def _acquire_lock(lock: Path) -> int:
    """Take an OS lock on `lock` (the file stays), or give up after LOCK_WAIT.

    The OS drops the lock when its holder exits or dies, so there is no stale lock to delete
    and no window where two processes both believe they hold it. Any OSError while opening or
    locking waits like a held lock; nothing spins past LOCK_WAIT.
    """
    deadline = time.monotonic() + LOCK_WAIT
    fd = None
    while True:
        try:
            if fd is None:
                fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
            _try_lock(fd)
            return fd
        except OSError:
            pass
        if time.monotonic() > deadline:
            if fd is not None:
                os.close(fd)
            raise BudgetError("budget counter is locked")
        time.sleep(0.05)


def _release_lock(fd: int) -> None:
    try:
        _unlock(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _counter(data: dict, name: str) -> dict:
    entry = data.get(name)
    if not isinstance(entry, dict):
        entry = {"used": 0, "limit": LIMITS[name]}
        data[name] = entry
    if not isinstance(entry.get("used"), int) or not isinstance(entry.get("limit"), int):
        raise BudgetError(f"budget counter {name} is malformed")
    return entry


def normalize(data: object) -> dict:
    """Bring any readable budget file to the multi-counter shape."""
    if not isinstance(data, dict):
        raise BudgetError("budget file is malformed")
    if "search" not in data and isinstance(data.get("calls"), list):  # old single-counter format
        used = data.get("used", 0)
        if not isinstance(used, int):
            raise BudgetError("budget file is malformed")
        data = {"search": {"used": used, "limit": SEARCH_LIMIT, "calls": data["calls"]}}
    for name in LIMITS:
        _counter(data, name)
    if not isinstance(data["search"].get("calls"), list):
        data["search"]["calls"] = []
    usage = data.get("usage")
    if not isinstance(usage, dict):
        usage = {}
        data["usage"] = usage
    for field in ("decide_input_tokens", "decide_ms", "abstain"):
        if not isinstance(usage.get(field), int):
            usage[field] = 0
    return data


def with_budget(run_dir: str, update: Callable[[dict], object]):
    """Run `update(data)` under the lock and write the file back. Returns what `update` returns."""
    directory = Path(run_dir) if run_dir else None
    if directory is None or not directory.is_dir():
        raise BudgetError("run dir not found")
    fd = _acquire_lock(directory / BUDGET_LOCK)
    try:
        path = directory / BUDGET_FILE
        try:
            data = normalize(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            data = normalize({})
        except (OSError, ValueError):
            raise BudgetError("budget file is unreadable")
        result = update(data)
        try:
            atomic_write_json(path, data)
        except OSError:
            raise BudgetError("budget file could not be written")
        return result
    finally:
        _release_lock(fd)


def read(run_dir: str) -> dict:
    return with_budget(run_dir, lambda data: json.loads(json.dumps(data)))


# --------------------------------------------------------------------------- search (jg shim)

def consume_search(run_dir: str) -> int:
    """Count one search before it starts (unknown outcomes stay counted). Returns its index."""

    def update(data: dict) -> int:
        entry = data["search"]
        if entry["used"] >= entry["limit"]:
            raise BudgetError(f"run budget used ({entry['used']}/{entry['limit']})")
        entry["used"] += 1
        entry["calls"].append({"at": now_iso(), "status": "started"})
        return len(entry["calls"]) - 1

    return with_budget(run_dir, update)


def record_search(run_dir: str, index: int, status: str, coverage: Optional[str]) -> None:
    def update(data: dict) -> None:
        calls = data["search"]["calls"]
        if 0 <= index < len(calls) and isinstance(calls[index], dict):
            calls[index].update({"status": status, "coverage": coverage, "ended": now_iso()})

    try:
        with_budget(run_dir, update)
    except BudgetError:
        pass


# --------------------------------------------------------------------------- decide

def _take(data: dict, need: int, safe: bool) -> Optional[Dict[str, int]]:
    """Take `need` attempts for one item, whole or nothing: `decide` first, then the reserve."""
    counters = DECIDE_COUNTERS if safe else DECIDE_COUNTERS[:1]
    free = {name: data[name]["limit"] - data[name]["used"] for name in counters}
    if sum(max(0, n) for n in free.values()) < need:
        return None
    taken: Dict[str, int] = {}
    left = need
    for name in counters:
        part = min(left, max(0, free[name]))
        if part:
            data[name]["used"] += part
            taken[name] = part
            left -= part
    return taken


def reserve_decide(run_dir: str, needs: List[int], safe: bool) -> List[Optional[Dict[str, int]]]:
    """Reserve each item's request count in order; an item that does not fit gets None."""

    def update(data: dict):
        return [_take(data, need, safe) for need in needs]

    return with_budget(run_dir, update)


def take_retry(run_dir: str, safe: bool) -> bool:
    """One more attempt (a 429/529 retry), taken under the lock right before it is sent."""
    try:
        return with_budget(run_dir, lambda data: _take(data, 1, safe) is not None)
    except BudgetError:
        return False


def finish_decide(run_dir: str, refunds: List[Dict[str, int]], input_tokens: int, ms: int,
                  abstain: int) -> None:
    """Give back what was reserved for items never sent, and add to `usage`."""

    def update(data: dict) -> None:
        for refund in refunds:
            for name, count in refund.items():
                if name in DECIDE_COUNTERS and count > 0:
                    data[name]["used"] = max(0, data[name]["used"] - count)
        usage = data["usage"]
        usage["decide_input_tokens"] += max(0, int(input_tokens))
        usage["decide_ms"] += max(0, int(ms))
        usage["abstain"] += max(0, int(abstain))

    try:
        with_budget(run_dir, update)
    except BudgetError:
        pass
