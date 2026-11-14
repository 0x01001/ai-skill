"""Run budget counters for `ai decide` (plan 3.6): decide, decide_reserved, retries, refunds."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from ai_skill_jev import budget as bud


class DecideBudgetTests(unittest.TestCase):
    def setUp(self):
        self.run_dir = tempfile.mkdtemp(prefix="ai-budget-")
        self.addCleanup(shutil.rmtree, self.run_dir, True)

    def set_used(self, **used):
        def update(data):
            for name, value in used.items():
                data[name]["used"] = value

        bud.with_budget(self.run_dir, update)

    def test_fresh_file_has_three_counters(self):
        data = bud.read(self.run_dir)
        self.assertEqual({k: data[k]["limit"] for k in bud.LIMITS},
                         {"search": 12, "decide": 400, "decide_reserved": 100})

    def test_items_are_whole_or_nothing_in_order(self):
        self.set_used(decide=396)
        taken = bud.reserve_decide(self.run_dir, [2, 3, 2], safe=False)
        self.assertEqual(taken, [{"decide": 2}, None, {"decide": 2}])
        self.assertEqual(bud.read(self.run_dir)["decide"]["used"], 400)

    def test_only_safe_kinds_reach_the_reserve(self):
        self.set_used(decide=399)
        self.assertEqual(bud.reserve_decide(self.run_dir, [3], safe=False), [None])
        self.assertEqual(bud.reserve_decide(self.run_dir, [3], safe=True), [{"decide": 1, "decide_reserved": 2}])
        data = bud.read(self.run_dir)
        self.assertEqual((data["decide"]["used"], data["decide_reserved"]["used"]), (400, 2))

    def test_retry_takes_one_unit_and_fails_when_empty(self):
        self.set_used(decide=399)
        self.assertTrue(bud.take_retry(self.run_dir, safe=False))
        self.assertFalse(bud.take_retry(self.run_dir, safe=False))
        self.assertTrue(bud.take_retry(self.run_dir, safe=True))

    def test_finish_refunds_and_adds_usage(self):
        bud.reserve_decide(self.run_dir, [3], safe=True)
        bud.finish_decide(self.run_dir, [{"decide": 2}], input_tokens=120, ms=50, abstain=1)
        bud.finish_decide(self.run_dir, [], input_tokens=30, ms=5, abstain=0)
        data = bud.read(self.run_dir)
        self.assertEqual(data["decide"]["used"], 1)
        self.assertEqual(data["usage"], {"decide_input_tokens": 150, "decide_ms": 55, "abstain": 1})

    def test_two_processes_never_lose_an_update(self):
        import subprocess
        import sys

        code = ("import sys; from ai_skill_jev import budget as b\n"
                "for _ in range(25): b.reserve_decide(sys.argv[1], [1], safe=False)\n")
        env = dict(__import__("os").environ, PYTHONPATH=str(Path(bud.__file__).resolve().parents[1]))
        procs = [subprocess.Popen([sys.executable, "-c", code, self.run_dir], env=env) for _ in range(2)]
        self.assertEqual([p.wait(timeout=60) for p in procs], [0, 0])
        self.assertEqual(bud.read(self.run_dir)["decide"]["used"], 50)

    def test_twelve_searches_of_35_leads_stay_under_240(self):
        for _ in range(12):
            bud.reserve_decide(self.run_dir, [1] * 20, safe=False)  # leads sends at most 20 of 35
        data = bud.read(self.run_dir)
        self.assertEqual((data["decide"]["used"], data["decide_reserved"]["used"]), (240, 0))

    def test_chunked_item_is_whole_or_nothing(self):
        self.set_used(decide=400, decide_reserved=98)
        self.assertEqual(bud.reserve_decide(self.run_dir, [3], safe=True), [None])
        self.assertEqual(bud.read(self.run_dir)["decide_reserved"]["used"], 98)

    def test_old_search_only_file_is_upgraded(self):
        path = Path(self.run_dir) / bud.BUDGET_FILE
        path.write_text(json.dumps({"used": 5, "calls": [{}] * 5}), encoding="utf-8")
        data = bud.read(self.run_dir)
        self.assertEqual((data["search"]["used"], data["decide"]["used"]), (5, 0))

    def test_unreadable_file_is_a_budget_error(self):
        (Path(self.run_dir) / bud.BUDGET_FILE).write_text("{not json", encoding="utf-8")
        with self.assertRaises(bud.BudgetError):
            bud.reserve_decide(self.run_dir, [1], safe=True)
        self.assertFalse(bud.take_retry(self.run_dir, safe=True))

    def test_write_failure_is_a_budget_error(self):
        from unittest import mock

        with mock.patch.object(bud, "atomic_write_json", side_effect=PermissionError(13, "in use")):
            with self.assertRaises(bud.BudgetError):
                bud.reserve_decide(self.run_dir, [1], safe=True)
            self.assertFalse(bud.take_retry(self.run_dir, safe=True))
        self.assertEqual(bud.read(self.run_dir)["decide"]["used"], 0)

    def test_held_lock_times_out_as_budget_error(self):
        from unittest import mock

        holder = bud._acquire_lock(Path(self.run_dir) / bud.BUDGET_LOCK)
        try:
            with mock.patch.object(bud, "LOCK_WAIT", 0.2), self.assertRaises(bud.BudgetError):
                bud.reserve_decide(self.run_dir, [1], safe=True)
        finally:
            bud._release_lock(holder)
        self.assertEqual(bud.reserve_decide(self.run_dir, [1], safe=True), [{"decide": 1}])

    def test_missing_run_dir(self):
        with self.assertRaises(bud.BudgetError):
            bud.reserve_decide(str(Path(self.run_dir) / "nope"), [1], safe=True)


if __name__ == "__main__":
    unittest.main()
