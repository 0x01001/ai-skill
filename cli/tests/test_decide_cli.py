"""`ai decide` end to end through `main()` (plan 3.1-3.2, test list in 8): gate, exit codes,
HTTP failures, deadlines, budget, the key never printed, and the doctor fields."""

import io
import json
import threading
import unittest
from unittest import mock

from ai_skill_jev import budget as bud
from ai_skill_jev import credentials as cred
from ai_skill_jev import decisions as d
from ai_skill_jev.cli import Deps, main
from ai_skill_jev.provider import PostResult

from tests.support import SENTINEL_KEY, ProviderServer, Sandbox, active_metadata, answer_all

LANE = {"request": "Fix the typo in README", "facts": {}, "has_resume_dir": False}
DONE = {"items": ["`npm test` passes", "README has no typo", "CI is green"]}


def lane_answers(body, n):
    return 200, answer_all(body, noul=lambda qid, q: 0.05 if qid in ("multi_deliverable", "hands_off") else 0.95,
                           choice=lambda qid, q: ("direct", {"direct": 0.95, "orchestrate": 0.03, "auto": 0.02})), None


class DecideCliTests(Sandbox):
    def activate(self, **kw):
        meta = active_metadata(**kw)
        cred.save_metadata(meta, self.env)
        self.store.data[meta["credential_ref"]] = cred.encode_secret(meta, SENTINEL_KEY)
        return meta

    def decide(self, kind, raw, *args, **decide_kw):
        self.out = io.StringIO()
        stdin = io.StringIO(raw if isinstance(raw, str) else json.dumps(raw))
        deps = Deps(env=self.env, out=self.out, store_by_name={"FakeStore": self.store}.get, stdin=stdin,
                    decide_kw=decide_kw)
        code = main(["decide", kind, *args], deps=deps)
        lines = self.out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, lines)
        self.assertNotIn(SENTINEL_KEY, lines[0])
        return code, json.loads(lines[0])

    def doctor(self):
        self.out = io.StringIO()
        deps = Deps(env=self.env, out=self.out, store_by_name={"FakeStore": self.store}.get,
                    shim_dir=lambda: None, jg_version=lambda path: "0.8.5", npm_prefix=lambda env: None)
        self.assertEqual(main(["doctor", "--json"], deps=deps), 0)
        return json.loads(self.out.getvalue())

    # -- gate ------------------------------------------------------------------------------

    def test_no_key_never_touches_the_network(self):
        post = mock.Mock(side_effect=AssertionError("no request without a key"))
        code, out = self.decide("lane", LANE, post=post)
        self.assertEqual((code, out["status"], out["reason"], out["requests"]), (3, "unavailable", "no_key", 0))

    def test_locked_store_is_unavailable(self):
        self.activate()
        self.store.fail_get = True
        code, out = self.decide("lane", LANE, post=mock.Mock(side_effect=AssertionError))
        self.assertEqual(code, 3)
        self.assertNotEqual(out["reason"], None)

    # -- happy path and exit codes ---------------------------------------------------------

    def test_decided_over_http(self):
        with ProviderServer(responder=lane_answers) as server:
            self.activate(base_url=server.url)
            code, out = self.decide("lane", LANE)
        self.assertEqual((code, out["status"], out["decision"]), (0, "decided", {"lane": "direct"}))
        self.assertEqual((out["requests"], out["input_tokens"], out["evaluated"]), (1, 100, False))
        (request,) = server.requests
        self.assertEqual(request["path"], "/v1/systemone")
        self.assertEqual(request["body"]["model"], "jev-1.14.0")
        self.assertEqual(request["headers"].get("Authorization"), f"Bearer {SENTINEL_KEY}")

    def test_abstain_is_exit_2(self):
        def unsure(body, n):
            return 200, answer_all(body, noul=0.5), None

        with ProviderServer(responder=unsure) as server:
            self.activate(base_url=server.url)
            code, out = self.decide("lane", LANE)
        self.assertEqual((code, out["status"], out["reason"]), (2, "abstain", "low_confidence"))

    def test_bad_input_is_64_without_request(self):
        self.activate()
        post = mock.Mock(side_effect=AssertionError)
        for raw in ("{not json", {"request": ""}, json.dumps({"items": [f"x{i}" for i in range(61)]})):
            kind = "done_means" if "items" in str(raw) else "lane"
            code, out = self.decide(kind, raw, post=post)
            self.assertEqual((code, out["status"]), (64, "bad_input"), raw)
        code, out = self.decide("lane", LANE, "--ai-run-dir", "relative/dir", post=post)
        self.assertEqual(code, 64)
        leads = [{"path": "a.ts", "range": "", "excerpt": "x", "jg_rank": 0}]
        for policy in (["tests"], {}):  # unhashable: must be bad_input, not a TypeError traceback
            code, out = self.decide("leads", {"question": "q", "leads": leads, "policy": policy}, post=post)
            self.assertEqual((code, out["status"]), (64, "bad_input"), policy)

    def test_input_file(self):
        self.activate()
        path = self.tmp / "in.json"
        path.write_text(json.dumps(LANE), encoding="utf-8")
        post = mock.Mock(return_value=PostResult(True, "ok", 200, None, 1))
        with ProviderServer(responder=lane_answers) as server:
            self.activate(base_url=server.url)
            code, out = self.decide("lane", "", "--input", str(path))
        self.assertEqual(code, 0)
        code, out = self.decide("lane", "", "--input", str(self.tmp / "missing.json"), post=post)
        self.assertEqual(code, 64)

    # -- HTTP failures ---------------------------------------------------------------------

    def test_401_is_auth_error_and_cancels_the_rest(self):
        with ProviderServer(status=401, body={"error": "bad key"}) as server:
            self.activate(base_url=server.url)
            code, out = self.decide("done_means", DONE, parallel=1)
        self.assertEqual((code, out["status"], out["reason"]), (3, "unavailable", "auth_error"))
        self.assertEqual(len(server.requests), 1)
        self.assertEqual({v["reason"] for k, v in out["decision"].items() if k != "1"}, {"cancelled"})

    def test_429_with_retry_after_then_success(self):
        def flaky(body, n):
            if n == 0:
                return 429, {"error": "slow down"}, {"retry-after": "0"}
            return lane_answers(body, n)

        with ProviderServer(responder=flaky) as server:
            self.activate(base_url=server.url)
            code, out = self.decide("lane", LANE)
        self.assertEqual((code, out["requests"], len(server.requests)), (0, 2, 2))

    def test_response_echoing_the_key_is_dropped(self):
        with ProviderServer(body={"answers": {}, "note": SENTINEL_KEY}) as server:
            self.activate(base_url=server.url)
            code, out = self.decide("lane", LANE)
        self.assertEqual((code, out["reason"]), (3, "provider_error"))

    def test_output_with_the_key_is_dropped(self):
        self.activate()
        leaky = d.Kind("lane", lambda raw, ctx: [d.Item(id="lane", bodies=[],
                                                         result=d.result("decided", {"x": SENTINEL_KEY}))],
                       d.KINDS["lane"].combine, False, None)
        with mock.patch.dict(d.KINDS, {"lane": leaky}):
            code, out = self.decide("lane", LANE)
        self.assertEqual((code, out["status"], out["reason"], out["decision"]), (3, "unavailable", "provider_error", None))

    def test_schema_error_is_provider_error(self):
        with ProviderServer(body={"answers": {"lane": {"type": "noul", "noul": 3}}}) as server:
            self.activate(base_url=server.url)
            code, out = self.decide("lane", LANE)
        self.assertEqual((code, out["reason"]), (3, "provider_error"))

    # -- deadlines (fake clock, no real waiting) -------------------------------------------

    def test_item_deadline_is_timeout(self):
        self.activate()
        seen = []

        def post(base, model, key, body, *, deadline, **kw):
            seen.append(deadline)
            return PostResult(False, "timeout", None, None, 1)

        code, out = self.decide("lane", LANE, post=post, clock=lambda: 1000.0)
        self.assertEqual((code, out["reason"]), (3, "timeout"))
        self.assertEqual(seen, [1030.0])  # 30 s per item

    def test_command_deadline_cancels_items_not_started(self):
        self.activate()
        now = {"t": 0.0}
        lock = threading.Lock()

        def clock():
            return now["t"]

        def post(base, model, key, body, *, deadline, **kw):
            with lock:
                now["t"] += 70.0  # each item eats 70 s of the 120 s command budget
            return PostResult(True, "ok", 200, answer_all(body), 1)

        code, out = self.decide("done_means", DONE, post=post, clock=clock, parallel=1)
        self.assertEqual((code, out["status"], out.get("partial")), (0, "decided", True))
        self.assertEqual([out["decision"][i]["status"] for i in "123"], ["decided", "decided", "unavailable"])
        self.assertEqual(out["decision"]["3"]["reason"], "cancelled")

    def test_429_retries_are_capped_per_item_not_per_request(self):
        from ai_skill_jev import decide as dec

        runner = dec.Runner("lane", active_metadata(), SENTINEL_KEY)
        body = runner.prepare(LANE)[0].bodies[0]
        seen = []

        def post(base, model, key, b, *, retry_429, **kw):
            seen.append(retry_429)
            return PostResult(True, "ok", 200, answer_all(b), 1 + retry_429)  # every retry used

        runner.post = post
        runner.run_item(d.Item("x", bodies=[body, body, body]), command_end=float("inf"))
        self.assertEqual(seen, [2, 0, 0])

    def test_a_stuck_request_is_abandoned_after_the_command_deadline(self):
        from ai_skill_jev import decide as dec

        release = threading.Event()
        self.addCleanup(release.set)

        def post(*a, **kw):
            release.wait(30)  # a request that never returns on its own
            return PostResult(False, "timeout", None, None, 1)

        runner = dec.Runner("lane", active_metadata(), SENTINEL_KEY, post=post, command_deadline=0.2)
        with mock.patch.object(dec, "WAIT_GRACE", 0.1):
            code, out = runner.run(LANE)
        self.assertEqual((code, out["status"], out["reason"]), (3, "unavailable", "timeout"))
        self.assertTrue(runner.cancel.is_set())

    def test_a_stuck_request_does_not_keep_the_process_alive(self):
        """The real exit path: no pool exit hook may join a worker stuck in a read."""
        import subprocess
        import sys
        import time
        from pathlib import Path

        script = (
            "import time\n"
            "from unittest import mock\n"
            "from ai_skill_jev import decide as dec\n"
            "from tests.support import SENTINEL_KEY, active_metadata\n"
            "def post(*a, **kw):\n"
            "    time.sleep(60)\n"
            "runner = dec.Runner('lane', active_metadata(), SENTINEL_KEY, post=post, command_deadline=0.2)\n"
            "with mock.patch.object(dec, 'WAIT_GRACE', 0.1):\n"
            "    code, out = runner.run(" + repr(LANE) + ")\n"
            "print(code, out['reason'])\n")
        cli_dir = Path(__file__).resolve().parents[1]
        env = dict(__import__("os").environ, PYTHONPATH=str(cli_dir / "src") + __import__("os").pathsep + str(cli_dir))
        began = time.monotonic()
        proc = subprocess.run([sys.executable, "-c", script], cwd=str(cli_dir), env=env, capture_output=True,
                              text=True, timeout=30)
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "3 timeout"), proc.stderr)
        self.assertLess(time.monotonic() - began, 15)  # not the 60 s the stuck request would take

    def test_exception_in_a_worker_is_raised(self):
        from ai_skill_jev import decide as dec

        runner = dec.Runner("lane", active_metadata(), SENTINEL_KEY, post=mock.Mock(side_effect=RuntimeError("x")))
        with self.assertRaises(RuntimeError):
            runner.run(LANE)

    # -- budget ----------------------------------------------------------------------------

    def test_exhausted_budget_keeps_results_decided_without_a_request(self):
        run_dir = self.tmp / "run"
        run_dir.mkdir()

        def spend(data):
            for name in bud.DECIDE_COUNTERS:
                data[name]["used"] = data[name]["limit"]

        bud.with_budget(str(run_dir), spend)
        self.activate()
        raw = {"checks": [{"id": "red", "command": "npm test", "expected": "ok", "exit_code": 1, "output_tail": "x"},
                          {"id": "green", "command": "npm test", "expected": "ok", "exit_code": 0,
                           "output_tail": "ok"}]}
        code, out = self.decide("check_output", raw, "--ai-run-dir", str(run_dir),
                                post=mock.Mock(side_effect=AssertionError("no budget, no request")))
        self.assertEqual((code, out["status"], out.get("partial")), (0, "decided", True))
        self.assertEqual(out["decision"]["red"], {"status": "decided", "value": {"check": "fail"}, "reason": None})
        self.assertEqual((out["decision"]["green"]["status"], out["decision"]["green"]["reason"]),
                         ("unavailable", "budget"))

    def test_run_budget_is_reserved_and_refunded(self):
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        self.activate()
        post = mock.Mock(return_value=PostResult(False, "auth_error", 401, None, 1))
        code, _ = self.decide("done_means", DONE, "--ai-run-dir", str(run_dir), post=post, parallel=1)
        self.assertEqual(code, 3)
        data = bud.read(str(run_dir))
        self.assertEqual(data["decide"]["used"], 1)  # 3 reserved, 2 never sent and given back

    def test_exhausted_budget_sends_nothing(self):
        run_dir = self.tmp / "run"
        run_dir.mkdir()
        bud.with_budget(str(run_dir), lambda data: data["decide"].update(used=400))
        self.activate()
        post = mock.Mock(side_effect=AssertionError)
        code, out = self.decide("lane", LANE, "--ai-run-dir", str(run_dir), post=post)
        self.assertEqual((code, out["reason"]), (3, "budget"))
        with ProviderServer(responder=lambda body, n: (200, answer_all(body, noul=0.95), None)) as server:
            self.activate(base_url=server.url)
            raw = {"checks": [{"id": "t", "command": "npm test", "expected": "ok", "exit_code": 0,
                               "output_tail": "ok"}]}
            code, out = self.decide("check_output", raw, "--ai-run-dir", str(run_dir))
        # check_output is a final check: it reaches decide_reserved when decide is empty.
        self.assertEqual((code, out["status"]), (0, "decided"))
        self.assertEqual(bud.read(str(run_dir))["decide_reserved"]["used"], 1)

    # -- doctor ----------------------------------------------------------------------------

    def test_doctor_fields(self):
        report = self.doctor()
        self.assertEqual((report["decide"], report["decide_reason"], report["kinds"]), ("off", "no_key", []))
        self.activate()
        report = self.doctor()
        # No engine here: jg is off, but decide needs only the key.
        self.assertEqual((report["state"], report["decide"], report["decide_reason"]), ("off", "ready", None))
        self.assertEqual(report["kinds"], [])
        self.assertTrue(any("ai decide" in h for h in report["hints"]))

    def test_doctor_kinds_follow_the_stored_profile(self):
        table = {("typesafe", "api.typesafe.ai", "jev-1.14.0"): {"lane": d.template_hash("lane"), "jobs": "old"}}
        with mock.patch.object(d, "EVALUATED_KINDS", table):
            self.activate()
            self.assertEqual(self.doctor()["kinds"], ["lane"])
            self.activate(model="jev-1.13.0")
            self.assertEqual(self.doctor()["kinds"], [])
            self.activate(provider="custom")
            self.assertEqual(self.doctor()["kinds"], [])


if __name__ == "__main__":
    unittest.main()
