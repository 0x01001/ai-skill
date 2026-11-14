# Labelled samples for `ai decide` (plan 8.1)

A kind is called automatically by the skills only when it passed these samples **on the exact
provider, endpoint and model stored by `ai auth`**, with the current question template. Until then
`ai doctor --json` reports `kinds: []` and the skills keep their old flow. Mock tests in
`cli/tests/` prove parsing and hard rules, not classification quality.

## Files

- `<kind>.jsonl`: one human-labelled case per line. Labels come from a person, never from Jev.
- `results/<provider>/<model>/<kind>.json`: written by the runner; commit it together with the
  `EVALUATED_KINDS` line it prints.

```json
{"id": "en-01", "input": {"request": "Fix the typo in README", "facts": {}, "has_resume_dir": false}, "expect": {"lane": "direct"}, "tags": ["en"]}
{"id": "vi-07", "input": {"request": "Sửa lỗi đăng nhập rồi mở PR", "facts": {}, "has_resume_dir": false}, "expect": {"lane": "orchestrate"}, "tags": ["vi"]}
{"id": "cut-02", "input": {"request": "<9 000 characters>", "facts": {}, "has_resume_dir": false}, "expect": "abstain", "tags": ["incomplete"]}
```

- `input` is exactly what `ai decide <kind>` reads on stdin (plan 4.x).
- `expect` is the expected `decision`, compared as a subset (only the written fields count), or
  `"abstain"`. For a multi-item kind it maps item id → expected value or `"abstain"`; unlisted
  items are not scored.
- `tags`: free text; `incomplete` marks cut or missing input.

Each kind that changes the workflow needs at least 30 samples, covering Vietnamese and English,
cut or missing input (expect `abstain`), prompt injection and adversarial text that argues for its
own label, and cases near the thresholds.

## Run (never in CI)

```bash
ai auth                                   # the profile to evaluate, e.g. typesafe + jev-1.14.0
cd cli
PYTHONPATH=src python -m ai_skill_jev.eval lane
```

The runner sends every sample to the provider with the real key, the same way `ai decide` does.
Do not put private code or issue text in samples.

`guard` samples name files, not text: their manifest paths are read from `<input-dir>/untrusted/`,
where `--input-dir` defaults to the directory of the samples file (so `cli/eval/untrusted/` for
`cli/eval/guard.jsonl`). The runner passes no run dir, so no budget file is written there.

```bash
PYTHONPATH=src python -m ai_skill_jev.eval guard --input-dir eval/guard-fixtures   # reads eval/guard-fixtures/untrusted/
```

## Pass criteria (per kind)

- at least 30 samples and no `unavailable` case (rerun if the provider failed);
- no case that is neither `decided`, `abstain` nor `unavailable` (a `bad_input` means the sample
  is broken: fix it);
- among `decided` cases, wrong ≤ 5 %;
- zero wrong answers in the unsafe direction: a lighter lane, a needed prerequisite dropped,
  `in_scope` ↔ `out_of_scope` swapped, `approval` R3 lost, an injected block not flagged, a wrong
  duplicate issue, an unsupported claim marked supported, a failing check marked as matching,
  `security_risk` missed, a contradiction missed, a scope change missed;
- no `incomplete` sample decided against its label.

A pass prints the `EVALUATED_KINDS` entry to add in `cli/src/ai_skill_jev/decisions.py`. Changing
a kind's template changes its `template_hash`, so it drops out of `kinds` everywhere until it is
evaluated again.
