"""Live checks against the real `/systemone` with a key from a local `.env` (never in CI).

The key is read from `TYPESAFE_API_KEY` (or `JEV_API_KEY`) in `<repo>/.env` or `<repo>/cli/.env`.
The suite only runs with `AI_SKILL_LIVE=1`, so a plain `python -m unittest` never spends money or
sends anything. Requests carry synthetic text only, no repository content. The key is never
printed: assertion messages use a fingerprint.

    cd cli
    AI_SKILL_LIVE=1 PYTHONPATH=src python -m unittest tests.test_live_systemone -v      # bash
    $env:AI_SKILL_LIVE=1; $env:PYTHONPATH="src"; python -m unittest tests.test_live_systemone -v  # PowerShell

Optional `.env` entries: `TYPESAFE_BASE_URL` (default the typesafe preset) and `TYPESAFE_MODEL`.
"""

import hashlib
import os
import unittest
from pathlib import Path

from ai_skill_jev import decisions as d
from ai_skill_jev import provider
from ai_skill_jev.decide import RETRY_429
from ai_skill_jev.systemone import Noul, build_body, judge_noul, parse_answers

CLI_DIR = Path(__file__).resolve().parents[1]
ENV_FILES = (CLI_DIR.parent / ".env", CLI_DIR / ".env")
KEY_NAMES = ("TYPESAFE_API_KEY", "JEV_API_KEY")


def read_dotenv(path: Path) -> dict:
    """Minimal KEY=VALUE parser: comments, blank lines, `export ` prefix, single or double quotes."""
    values = {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def load_settings() -> dict:
    merged = {}
    for path in ENV_FILES:  # cli/.env wins over the repo root
        merged.update(read_dotenv(path))
    return merged


SETTINGS = load_settings()
KEY = next((SETTINGS[n] for n in KEY_NAMES if SETTINGS.get(n)), "")
PRESET = provider.PRESETS["typesafe"]
BASE_URL = SETTINGS.get("TYPESAFE_BASE_URL") or PRESET["base_url"]
MODEL = SETTINGS.get("TYPESAFE_MODEL") or PRESET["model"]


def fingerprint(key: str) -> str:
    return "sha256:" + hashlib.sha256(key.encode()).hexdigest()[:8]


SKIP_REASON = (
    "set AI_SKILL_LIVE=1 to call the real API" if os.environ.get("AI_SKILL_LIVE") != "1" else
    "never runs in CI" if os.environ.get("CI") else
    "no TYPESAFE_API_KEY / JEV_API_KEY in .env or cli/.env" if not KEY else None)


class DotenvParseTests(unittest.TestCase):
    """The parser itself runs offline, on a temp file (never the user's real `.env`)."""

    def test_parse(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# c\n\nexport TYPESAFE_API_KEY='tsk-a b'\nJEV_API_KEY=x # note\nBAD LINE\n"
                            "TYPESAFE_MODEL=\"jev-1.14.0\"\n", encoding="utf-8")
            self.assertEqual(read_dotenv(path), {"TYPESAFE_API_KEY": "tsk-a b", "JEV_API_KEY": "x",
                                                 "TYPESAFE_MODEL": "jev-1.14.0"})
            self.assertEqual(read_dotenv(Path(tmp) / "missing"), {})


@unittest.skipIf(SKIP_REASON, SKIP_REASON or "")
class LiveSystemOneTests(unittest.TestCase):
    maxDiff = None

    def post(self, body):
        res = provider.post_systemone(BASE_URL, MODEL, KEY, body, retry_429=RETRY_429)
        self.assertTrue(res.ok, f"{res.reason} http={res.http_status} key={fingerprint(KEY)} "
                                f"base={BASE_URL} model={MODEL}")
        self.assertFalse(provider.contains_key(repr(res.payload).encode(), KEY))
        return res.payload

    def test_key_is_accepted(self):
        res = provider.validate_credentials(BASE_URL, MODEL, KEY)
        self.assertTrue(res.ok, f"{res.reason} key={fingerprint(KEY)} base={BASE_URL} model={MODEL}")

    def test_noul_shape_and_obvious_answers(self):
        body = build_body({"text": "The sky is blue. Water is wet."}, [
            Noul("sky_blue", "Does the text say the sky is blue?", "It says so", "It does not"),
            Noul("sky_green", "Does the text say the sky is green?", "It says so", "It does not"),
        ])
        answers = parse_answers(self.post(body), body)  # raises SchemaError on a shape change
        self.assertTrue(judge_noul(answers, "sky_blue").value)
        self.assertFalse(judge_noul(answers, "sky_green").value)

    def test_every_kind_template_parses(self):
        """One synthetic call per single-request kind: the real response must fit our schema."""
        samples = {
            "lane": {"request": "Fix the typo 'teh' in README.md", "facts": {}, "has_resume_dir": False},
            "reroute": {"lane": "orchestrate", "summary": "Only one small job is left."},
            "done_means": {"items": ["`npm test` exits 0"]},
            "failure": {"job_id": "j1", "status": "failed", "error_tail": "ECONNRESET while fetching",
                        "checks_failed": []},
        }
        for name, raw in samples.items():
            with self.subTest(kind=name):
                (item,) = d.KINDS[name].prepare(raw, d.Context(key=KEY))
                answers = [parse_answers(self.post(body), body) for body in item.bodies]
                out = d.KINDS[name].combine(item, answers)
                self.assertIn(out["status"], ("decided", "abstain"))

    def test_score_question_parses(self):
        """`leads` is the only kind with a Score question; its shape must still match."""
        raw = {"question": "Where is the retry delay computed?", "policy": "tests",
               "leads": [{"path": "src/retry.py", "range": "1-3", "jg_rank": 0,
                          "excerpt": "def retry_delay(attempt):\n    return min(30, 2 ** attempt)"}]}
        (item,) = d.KINDS["leads"].prepare(raw, d.Context(key=KEY))
        answers = [parse_answers(self.post(body), body) for body in item.bodies]
        self.assertIn(d.KINDS["leads"].combine(item, answers)["value"]["lead"], ("accept", "unsure", "reject"))

    def test_ai_decide_end_to_end(self):
        """The whole `ai decide` path (no run dir, so no budget file): one JSON result, key never in it."""
        import json

        from ai_skill_jev.decide import Runner

        meta = {"provider": "typesafe", "base_url": BASE_URL, "model": MODEL,
                "endpoint_host": BASE_URL.split("/")[2]}
        code, out = Runner("done_means", meta, KEY).run({"items": ["`npm test` exits 0", "README has no typo"]})
        self.assertFalse(provider.contains_key(json.dumps(out).encode(), KEY))
        self.assertIn((code, out["status"]), ((0, "decided"), (2, "abstain")), out.get("reason"))
        self.assertGreaterEqual(out["requests"], 1)
        self.assertFalse(out["evaluated"])  # no kind is evaluated yet (EVALUATED_KINDS is empty)


if __name__ == "__main__":
    unittest.main()
