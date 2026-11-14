"""Request shapes, response schema and thresholds of System One (plan 3.2 steps 4/6, 3.3)."""

import unittest

from ai_skill_jev import systemone as so


def noul_body(*ids):
    return so.build_body({"x": 1}, [so.Noul(i, "q?", "yes", "no") for i in ids])


class BuildTests(unittest.TestCase):
    def test_choice_is_asked_twice_with_reversed_options(self):
        body = so.build_body({}, [so.Choice("c", "pick", (("a", "A"), ("b", "B"), ("c", "C")))])
        self.assertEqual(list(body["questions"]), ["c", "c__rev"])
        self.assertEqual(list(body["questions"]["c"]["criteria"]), ["a", "b", "c"])
        self.assertEqual(list(body["questions"]["c__rev"]["criteria"]), ["c", "b", "a"])

    def test_every_question_says_the_state_is_data(self):
        body = so.build_body({}, [so.Noul("n", "q?", "t", "f"), so.Score("s", "how?", ("0", "1"))])
        for q in body["questions"].values():
            self.assertTrue(q["instructions"].startswith(so.DATA_NOTE))
        self.assertEqual(body["questions"]["n"]["criteria"], {"true": "t", "false": "f"})
        self.assertEqual(body["questions"]["s"]["criteria"], ["0", "1"])

    def test_option_and_level_bounds(self):
        with self.assertRaises(ValueError):
            so.Choice("c", "x", (("a", "A"),))
        with self.assertRaises(ValueError):
            so.Score("s", "x", tuple(str(i) for i in range(11)))

    def test_fits_limits_request_and_question_size(self):
        self.assertTrue(so.fits(noul_body("a")))
        big = so.build_body({"t": "x" * (so.MAX_REQUEST_TOKENS * 4)}, [so.Noul("a", "q", "t", "f")])
        self.assertFalse(so.fits(big))
        long_q = so.build_body({}, [so.Noul("a", "q" * (so.MAX_QUESTION_TOKENS * 4), "t", "f")])
        self.assertFalse(so.fits(long_q))


class ParseTests(unittest.TestCase):
    def test_noul_ok_and_out_of_range(self):
        body = noul_body("a")
        self.assertEqual(so.parse_answers({"answers": {"a": {"type": "noul", "noul": 0.9}}}, body), {"a": {"noul": 0.9}})
        for bad in (1.2, -0.1, True, "0.9", float("nan"), None):
            with self.assertRaises(so.SchemaError):
                so.parse_answers({"answers": {"a": {"type": "noul", "noul": bad}}}, body)

    def test_missing_id_or_wrong_type(self):
        body = noul_body("a", "b")
        with self.assertRaises(so.SchemaError):
            so.parse_answers({"answers": {"a": {"type": "noul", "noul": 0.5}}}, body)
        with self.assertRaises(so.SchemaError):
            so.parse_answers({"answers": {"a": {"type": "choice", "noul": 0.5}, "b": {"type": "noul", "noul": 1}}}, body)
        with self.assertRaises(so.SchemaError):
            so.parse_answers([], body)

    def test_choice_needs_a_listed_option_and_confidence(self):
        body = so.build_body({}, [so.Choice("c", "x", (("a", "A"), ("b", "B")))])
        good = {"type": "choice", "choice": "a", "confidence": 0.8, "probabilities": {"a": 0.8, "b": 0.2}}
        parsed = so.parse_answers({"answers": {"c": good, "c__rev": good}}, body)
        self.assertEqual(parsed["c"]["choice"], "a")
        for bad in (dict(good, choice="z"), {k: v for k, v in good.items() if k != "confidence"},
                    dict(good, probabilities={"zz": 0.5})):
            with self.assertRaises(so.SchemaError):
                so.parse_answers({"answers": {"c": bad, "c__rev": good}}, body)

    def test_score_range(self):
        body = so.build_body({}, [so.Score("s", "x", ("0", "1", "2"))])
        ok = {"type": "score", "score": 2.0, "confidence": 0.7, "probabilities": {"2": 1.0}}
        self.assertEqual(so.parse_answers({"answers": {"s": ok}}, body)["s"]["score"], 2.0)
        with self.assertRaises(so.SchemaError):
            so.parse_answers({"answers": {"s": dict(ok, score=3)}}, body)

    def test_usage_tokens(self):
        self.assertEqual(so.usage_tokens({"usage": {"input_tokens": 12}}), 12)
        self.assertEqual(so.usage_tokens({"usage": {"input_tokens": "12"}}), 0)
        self.assertEqual(so.usage_tokens({}), 0)


class JudgeTests(unittest.TestCase):
    def test_noul_thresholds(self):
        self.assertEqual(so.judge_noul({"a": {"noul": 0.80}}, "a").value, True)
        self.assertEqual(so.judge_noul({"a": {"noul": 0.20}}, "a").value, False)
        mid = so.judge_noul({"a": {"noul": 0.5}}, "a")
        self.assertEqual((mid.status, mid.reason, mid.raw), ("abstain", "low_confidence", 0.5))

    def choice(self, first, second, c1=0.9, c2=0.9):
        return {"c": {"choice": first, "confidence": c1, "probabilities": {}},
                "c__rev": {"choice": second, "confidence": c2, "probabilities": {}}}

    def test_choice_needs_both_passes_and_confidence(self):
        self.assertTrue(so.judge_choice(self.choice("a", "a"), "c").decided)
        self.assertEqual(so.judge_choice(self.choice("a", "b"), "c").reason, "disagree")
        low = so.judge_choice(self.choice("a", "a", 0.9, 0.69), "c")
        self.assertEqual((low.status, low.reason), ("abstain", "low_confidence"))

    def test_score_split_and_confidence(self):
        ok = {"s": {"score": 2.4, "confidence": 0.7, "probabilities": {"2": 0.6, "3": 0.4}}}
        self.assertEqual(so.judge_score(ok, "s").value, 2.4)
        split = {"s": {"score": 1.5, "confidence": 0.9, "probabilities": {"0": 0.5, "3": 0.5}}}
        self.assertEqual(so.judge_score(split, "s").status, "abstain")
        low = {"s": {"score": 3, "confidence": 0.59, "probabilities": {"3": 1.0}}}
        self.assertEqual(so.judge_score(low, "s").status, "abstain")
        self.assertFalse(so.is_split({"1": 0.5, "2": 0.5}))


if __name__ == "__main__":
    unittest.main()
