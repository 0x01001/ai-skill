"""`ai decide <kind>`: gate, budget, fan-out to `/systemone`, mapping and the one-line output (plan 3.1-3.2).

Order: gate (metadata + key, no network without a key) -> input schema and cleaning (exit 64) ->
size check per request (`too_large`) -> budget reservation per item (`--ai-run-dir` only) ->
up to 4 items in parallel, the requests of one item in sequence, 30 s per item and 120 s per
command -> response schema -> thresholds and the kind's mapping -> one JSON line on stdout.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

from . import TOOL_ID
from . import budget as bud
from . import credentials as cred
from . import provider
from .decisions import KINDS, MAX_ITEMS, BadInput, Context, Item, evaluated_kinds, result
from .systemone import SchemaError, fits, parse_answers, usage_tokens

EXIT_DECIDED = 0
EXIT_ABSTAIN = 2
EXIT_UNAVAILABLE = 3
EXIT_BAD_INPUT = 64

PARALLEL = 4
ITEM_DEADLINE = 30.0
COMMAND_DEADLINE = 120.0
RETRY_429 = 2  # per item, across all of its requests (plan 3.2)
WAIT_GRACE = 5.0  # real seconds past COMMAND_DEADLINE before a stuck item is abandoned

STATUS_EXIT = {"decided": EXIT_DECIDED, "abstain": EXIT_ABSTAIN, "unavailable": EXIT_UNAVAILABLE}


def unavailable(reason: str, detail: Optional[dict] = None) -> dict:
    return result("unavailable", None, reason, detail)


class Runner:
    """One `ai decide` call. Everything that touches the outside world is injectable for tests."""

    def __init__(self, kind: str, meta: dict, key: str, run_dir: Optional[str] = None, *,
                 post: Callable = provider.post_systemone, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, run_git=None,
                 item_deadline: float = ITEM_DEADLINE, command_deadline: float = COMMAND_DEADLINE,
                 parallel: int = PARALLEL, input_dir: Optional[str] = None):
        self.kind = KINDS[kind]
        self.meta, self.key, self.run_dir = meta, key, run_dir
        self.post, self.clock, self.sleep = post, clock, sleep
        self.item_deadline, self.command_deadline, self.parallel = item_deadline, command_deadline, parallel
        # `input_dir` lets the eval runner point `guard` at fixtures without a budget file there.
        self.ctx = Context(key=key, run_dir=input_dir or run_dir)
        if run_git is not None:
            self.ctx.run_git = run_git
        self.cancel = threading.Event()
        self.lock = threading.Lock()
        self.attempts = 0
        self.input_tokens = 0
        self.auth_error = False

    # ------------------------------------------------------------------ steps

    def prepare(self, raw) -> List[Item]:
        items = self.kind.prepare(raw, self.ctx)
        if len(items) > MAX_ITEMS:
            raise BadInput(f"at most {MAX_ITEMS} items per call (got {len(items)})")
        ids = [item.id for item in items]
        if len(set(ids)) != len(ids):
            raise BadInput("duplicate item id")
        for item in items:
            if item.result is None and not item.bodies:
                item.result = unavailable("provider_error")
            if item.result is None and not all(fits(body) for body in item.bodies):
                item.bodies = []
                item.result = result("abstain", None, "too_large", {"truncated": item.truncated})
        return items

    def reserve(self, items: List[Item]) -> Tuple[Dict[str, dict], bool]:
        """Return (allocation per item id, budget exhausted for the whole command)."""
        pending = [item for item in items if item.result is None]
        if not self.run_dir or not pending:
            return {}, False
        try:
            taken = bud.reserve_decide(self.run_dir, [len(item.bodies) for item in pending], self.kind.safe)
        except bud.BudgetError:
            taken = [None] * len(pending)
        allocation = {}
        for item, got in zip(pending, taken):
            if got is None:
                item.result = unavailable("budget")
            else:
                allocation[item.id] = got
        return allocation, not allocation

    def before_retry(self) -> bool:
        if not self.run_dir:
            return True
        return bud.take_retry(self.run_dir, self.kind.safe)

    def run_item(self, item: Item, command_end: float) -> Tuple[dict, int]:
        """Send the item's requests in order. Returns (result, requests never sent)."""
        start = self.clock()
        if self.cancel.is_set():
            return unavailable("cancelled"), len(item.bodies)
        if start >= command_end:
            return unavailable("cancelled"), len(item.bodies)
        end = min(start + self.item_deadline, command_end)
        answers = []
        retries_left = RETRY_429
        for n, body in enumerate(item.bodies):
            if self.cancel.is_set():
                return unavailable("cancelled"), len(item.bodies) - n
            res = self.post(self.meta["base_url"], self.meta["model"], self.key, body, deadline=end,
                            retry_429=retries_left, before_retry=self.before_retry, cancel=self.cancel,
                            clock=self.clock, sleep=self.sleep)
            retries_left = max(0, retries_left - max(0, res.attempts - 1))
            with self.lock:
                self.attempts += res.attempts
                if res.payload is not None:
                    self.input_tokens += usage_tokens(res.payload)
            if not res.ok:
                unsent = len(item.bodies) - n - (1 if res.attempts else 0)
                if res.reason == "auth_error":
                    self.auth_error = True
                    self.cancel.set()
                return unavailable(res.reason), unsent
            try:
                answers.append(parse_answers(res.payload, body))
            except SchemaError:
                return unavailable("provider_error"), len(item.bodies) - n - 1
        try:
            out = self.kind.combine(item, answers)
        except (KeyError, TypeError, ValueError):
            return unavailable("provider_error"), 0
        if item.truncated:
            out["detail"] = dict(out.get("detail") or {}, truncated=item.truncated)
        return out, 0

    # ------------------------------------------------------------------ whole call

    def run(self, raw) -> Tuple[int, dict]:
        started = self.clock()
        try:
            items = self.prepare(raw)
        except BadInput as exc:
            return EXIT_BAD_INPUT, self.output("bad_input", None, {"error": str(exc)}, "bad_input")
        # Items the budget could not cover are `unavailable/budget`; the others (local rules, or
        # a later item that got a share) keep their result, so nothing already decided is lost.
        allocation, _ = self.reserve(items)
        results: Dict[str, dict] = {item.id: item.result for item in items if item.result is not None}
        to_send = [item for item in items if item.result is None]
        unsent: Dict[str, int] = {}
        command_end = started + self.command_deadline
        if to_send:
            done = self.send_all(to_send, command_end)
            for item in to_send:
                results[item.id], unsent[item.id] = done[item.id]
        extra = self.kind.finalize(items, results) if self.kind.finalize else {}
        self.settle_budget(allocation, unsent, results, started)
        return self.assemble(items, results, extra)

    def send_all(self, items: List[Item], command_end: float) -> Dict[str, Tuple[dict, int]]:
        """Run items on daemon threads; return (result, unsent) for every item by the real deadline.

        Not a ThreadPoolExecutor: its exit hook joins every worker, so one request stuck in a
        socket read would keep the process alive past the command deadline. A daemon thread
        left behind here dies with the process.
        """
        queue, done, errors = deque(items), {}, []
        guard = threading.Lock()

        def worker() -> None:
            while True:
                with guard:
                    if not queue or self.cancel.is_set():
                        return
                    item = queue.popleft()
                try:
                    out = self.run_item(item, command_end)
                except BaseException as exc:  # re-raised in the caller, like future.result()
                    with guard:
                        errors.append(exc)
                    return
                with guard:
                    done.setdefault(item.id, out)

        threads = [threading.Thread(target=worker, name=f"ai-decide-{n}", daemon=True)
                   for n in range(max(1, min(self.parallel, len(items))))]
        for thread in threads:
            thread.start()
        # Each attempt is capped by its own deadline; this is the backstop in real time.
        end = time.monotonic() + self.command_deadline + WAIT_GRACE
        for thread in threads:
            thread.join(max(0.0, end - time.monotonic()))
        with guard:
            self.cancel.set()
            never_started = {item.id for item in queue}
            queue.clear()
            finished, failure = dict(done), (errors[0] if errors else None)
        if failure is not None:
            raise failure
        out = {}
        for item in items:
            if item.id in finished:
                out[item.id] = finished[item.id]
            elif item.id in never_started:
                out[item.id] = (unavailable("cancelled"), len(item.bodies))
            else:  # stuck mid-request: it may have been sent, so nothing is refunded
                out[item.id] = (unavailable("timeout"), 0)
        return out

    def settle_budget(self, allocation: Dict[str, dict], unsent: Dict[str, int], results: Dict[str, dict],
                      started: float) -> None:
        if not self.run_dir:
            return
        refunds = []
        for item_id, count in unsent.items():
            got = dict(allocation.get(item_id) or {})
            back = {}
            for name in reversed(bud.DECIDE_COUNTERS):  # the reserve was taken last, give it back first
                part = min(count, got.get(name, 0))
                if part:
                    back[name] = part
                    count -= part
            if back:
                refunds.append(back)
        abstained = sum(1 for r in results.values() if r["status"] == "abstain")
        bud.finish_decide(self.run_dir, refunds, self.input_tokens, int((self.clock() - started) * 1000), abstained)

    def assemble(self, items: List[Item], results: Dict[str, dict], extra: dict) -> Tuple[int, dict]:
        ordered = [(item.id, results[item.id]) for item in items]
        if not self.kind.multi:
            _, r = ordered[0]
            reason = "auth_error" if self.auth_error else r["reason"]
            status = "unavailable" if self.auth_error else r["status"]
            detail = dict(r.get("detail") or {}, **extra)
            return STATUS_EXIT[status], self.output(status, r.get("value"), detail, reason)
        decision = {i: {"status": r["status"], "value": r.get("value"), "reason": r["reason"]} for i, r in ordered}
        detail = {"items": {i: r.get("detail") or {} for i, r in ordered}}
        detail.update(extra)
        statuses = [r["status"] for _, r in ordered]
        partial = any(s != "decided" for s in statuses)
        if self.auth_error:
            status, reason = "unavailable", "auth_error"
        elif "decided" in statuses:
            status, reason = "decided", None
        elif "abstain" in statuses:
            status, reason = "abstain", None
        else:
            status = "unavailable"
            reasons = [r["reason"] for _, r in ordered]
            reason = max(set(reasons), key=reasons.count)
        return STATUS_EXIT[status], self.output(status, decision, detail, reason, partial)

    def output(self, status: str, decision, detail: dict, reason: Optional[str], partial: bool = False) -> dict:
        meta = self.meta
        out = {"tool": TOOL_ID, "kind": self.kind.name, "status": status, "decision": decision, "detail": detail,
               "reason": reason, "model": meta.get("model"), "requests": self.attempts,
               "input_tokens": self.input_tokens,
               "evaluated": self.kind.name in evaluated_kinds(meta.get("provider"), meta.get("endpoint_host"),
                                                              meta.get("model"))}
        if partial:
            out["partial"] = True
        return out


# --------------------------------------------------------------------------- entry used by cli.py

def gate_output(kind: str, reason: str, model: Optional[str] = None) -> dict:
    return {"tool": TOOL_ID, "kind": kind, "status": "unavailable", "decision": None, "detail": {},
            "reason": reason, "model": model, "requests": 0, "input_tokens": 0, "evaluated": False}


def gate(env, store_by_name) -> Tuple[Optional[dict], Optional[str], Optional[str]]:
    """Return (metadata, key, None) or (metadata or None, None, reason). Never touches the network."""
    try:
        meta = cred.load_metadata(env)
    except cred.StateError as exc:
        return None, None, exc.reason
    if not cred.is_active(meta) or not meta.get("base_url") or not meta.get("model"):
        return meta, None, "no_key"
    try:
        key = cred.read_key(meta, store_by_name)
    except cred.StateError as exc:
        return meta, None, exc.reason
    return meta, key, None


def serialize(out: dict, key: Optional[str]) -> Optional[str]:
    """One JSON line, or None when it would contain the key (the caller reports `provider_error`)."""
    line = json.dumps(out, ensure_ascii=True)
    if key and provider.contains_key(line.encode("utf-8"), key):
        return None
    return line


def run_decide(kind: str, text: str, run_dir: Optional[str], env, store_by_name, **runner_kw) -> Tuple[int, str]:
    meta, key, reason = gate(env, store_by_name)
    if reason:
        return EXIT_UNAVAILABLE, json.dumps(gate_output(kind, reason, (meta or {}).get("model")))
    try:
        raw = json.loads(text)
    except ValueError as exc:
        out = gate_output(kind, "bad_input", meta.get("model"))
        out.update(status="bad_input", detail={"error": f"input is not valid JSON ({exc.msg})"})
        return EXIT_BAD_INPUT, json.dumps(out)
    code, out = Runner(kind, meta, key, run_dir, **runner_kw).run(raw)
    line = serialize(out, key)
    if line is None:  # 3.2 step 8: never print the key, drop the whole output
        return EXIT_UNAVAILABLE, json.dumps(gate_output(kind, "provider_error", meta.get("model")))
    return code, line
