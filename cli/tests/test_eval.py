"""Grading of the labelled-sample runner (plan 8.1), offline: `run` is a stub, no key, no network."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_skill_jev import eval as ev
from ai_skill_jev.provider import PostResult

from tests.support import SENTINEL_KEY, active_metadata, answer_all


def out(status, decision=None, reason=None):
    return {"status": status, "decision": decision, "reason": reason}


def samples(n, expect, tags=()):
    return [{"id": f"s{i}", "input": {"i": i}, "expect": expect, "tags": list(tags)} for i in range(n)]


class ScoreTests(unittest.TestCase):
    def test_pass_with_abstains(self):
        rows = samples(28, {"lane": "direct"}) + samples(2, "abstain", ["incomplete"])
        answers = iter([out("decided", {"lane": "direct"})] * 28 + [out("abstain", None, "truncated")] * 2)
        report = ev.score("lane", rows, lambda raw: next(answers))
        self.assertTrue(report["passed"], report["failures"])
        self.assertEqual((report["decided"], report["wrong"]), (28, 0))

    def test_one_lighter_lane_fails(self):
        rows = samples(30, {"lane": "orchestrate"})
        answers = iter([out("decided", {"lane": "direct"})] + [out("decided", {"lane": "orchestrate"})] * 29)
        report = ev.score("lane", rows, lambda raw: next(answers))
        self.assertEqual((report["unsafe"], report["passed"]), (1, False))

    def test_heavier_lane_counts_as_wrong_only(self):
        rows = samples(30, {"lane": "direct"})
        answers = iter([out("decided", {"lane": "auto"})] + [out("decided", {"lane": "direct"})] * 29)
        report = ev.score("lane", rows, lambda raw: next(answers))
        self.assertEqual((report["wrong"], report["unsafe"]), (1, 0))
        self.assertTrue(report["passed"])  # 1/30 = 3.3 % <= 5 %

    def test_too_few_samples_and_unavailable(self):
        report = ev.score("lane", samples(3, {"lane": "direct"}), lambda raw: out("unavailable", None, "timeout"))
        self.assertFalse(report["passed"])
        self.assertEqual(len(report["failures"]), 2)

    def test_bad_input_is_a_failure_not_an_abstain(self):
        report = ev.score("lane", samples(30, {"lane": "direct"}), lambda raw: out("bad_input", None, "bad_input"))
        self.assertEqual((report["invalid"], report["passed"]), (30, False))
        self.assertEqual({r["grade"] for r in report["rows"]}, {"invalid"})

    def test_incomplete_decided_against_label(self):
        rows = samples(30, "abstain", ["incomplete"])
        report = ev.score("reroute", rows, lambda raw: out("decided", {"shrunk_to_one": False}))
        self.assertEqual(report["incomplete_decided_wrong"], 30)

    def test_multi_item_subset_compare_and_unsafe(self):
        rows = [{"id": "g", "input": {}, "expect": {"issue#body": {"flagged": True}, "issue#title": "abstain"}}]
        decision = {"issue#body": {"status": "decided", "value": {"flagged": False, "categories": []}},
                    "issue#title": {"status": "abstain", "value": None, "reason": "low_confidence"}}
        report = ev.score("guard", rows, lambda raw: out("decided", decision))
        self.assertEqual((report["cases"], report["unsafe"]), (2, 1))

    def test_multi_item_sample_without_item_labels_is_invalid(self):
        calls = []
        for expect in ("abstain", {}, ["issue#body"]):
            report = ev.score("guard", samples(30, expect), lambda raw: calls.append(raw) or out("bad_input"))
            self.assertEqual((report["cases"], report["invalid"], report["passed"]), (30, 30, False), expect)
            self.assertEqual({r["grade"] for r in report["rows"]}, {"invalid"})
        self.assertEqual(calls, [])  # not sent: there is nothing to grade the answer against

    def test_label_for_a_missing_item_id_is_invalid_not_unsafe(self):
        rows = [{"id": "g", "input": {}, "expect": {"issue#bodyy": {"flagged": True}, "issue#body": {"flagged": True}}}]
        decision = {"issue#body": {"status": "decided", "value": {"flagged": True, "categories": []}}}
        report = ev.score("guard", rows, lambda raw: out("decided", decision))
        grades = {r["id"]: r["grade"] for r in report["rows"]}
        self.assertEqual(grades, {"g/issue#bodyy": "invalid", "g/issue#body": "ok"})
        self.assertEqual((report["unsafe"], report["wrong"], report["invalid"]), (0, 0, 1))
        self.assertIn("issue#bodyy", report["rows"][0]["reason"])
        # a whole-command failure still applies to every label, as before
        report = ev.score("guard", rows, lambda raw: out("unavailable", None, "timeout"))
        self.assertEqual((report["unavailable"], report["invalid"]), (2, 0))

    def test_scope_flip_is_unsafe_both_ways(self):
        self.assertTrue(ev._scope_flip({"scope": "in_scope"}, {"scope": "out_of_scope"}))
        self.assertTrue(ev._scope_flip({"scope": "out_of_scope"}, {"scope": "in_scope"}))
        self.assertFalse(ev._scope_flip({"scope": "in_scope"}, {"scope": "in_scope"}))

    def test_model_dir_is_a_safe_name(self):
        self.assertEqual(ev.model_dir("typesafe/jev-1.13"), "typesafe_jev-1.13")


class GuardSampleTests(unittest.TestCase):
    """guard reads its input from `<dir>/untrusted/`: the runner must give it that dir."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ai-eval-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        (self.dir / "untrusted").mkdir()
        (self.dir / "untrusted" / "issue.json").write_text(json.dumps({"title": "Bug", "body": "Login fails"}),
                                                           encoding="utf-8")

    def test_guard_sample_reaches_the_provider(self):
        sent = []

        def post(base, model, key, body, **kw):
            sent.append(body)
            return PostResult(True, "ok", 200, answer_all(body), 1)

        run = ev.sample_runner("guard", active_metadata(), SENTINEL_KEY, str(self.dir), post=post)
        out = run([{"source": "issue", "path": "issue.json"}])
        self.assertNotEqual(out["status"], "bad_input", out["detail"])
        self.assertEqual(len(sent), 2)  # title and body
        self.assertEqual(os.listdir(self.dir), ["untrusted"])  # no budget file next to the samples

    def test_main_defaults_the_input_dir_to_the_samples_dir(self):
        samples = self.dir / "guard.jsonl"
        samples.write_text(json.dumps({"id": "a", "input": [], "expect": "abstain"}) + "\n", encoding="utf-8")
        seen = {}

        def fake_runner(kind, meta, key, input_dir, **kw):
            seen["input_dir"] = input_dir
            return lambda raw: {"status": "abstain", "decision": None, "reason": "low_confidence"}

        meta = active_metadata()
        with mock.patch.dict(os.environ, {"CI": ""}), \
                mock.patch.object(ev, "gate", return_value=(meta, SENTINEL_KEY, None)), \
                mock.patch.object(ev, "sample_runner", fake_runner), \
                mock.patch("sys.stdout"):
            ev.main(["guard", "--samples", str(samples), "--results", str(self.dir / "results")])
            self.assertEqual(seen["input_dir"], str(self.dir.resolve()))
            other = self.dir / "fixtures"
            ev.main(["guard", "--samples", str(samples), "--results", str(self.dir / "results"),
                     "--input-dir", str(other)])
            self.assertEqual(seen["input_dir"], str(other.resolve()))


if __name__ == "__main__":
    unittest.main()
