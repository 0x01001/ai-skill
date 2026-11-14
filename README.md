# ai-skill

Claude Code skills that take a task from request to verified result: `ai-main` routes it, `ai-orchestrate` runs a job graph across subagents and worktree sub-sessions with an independent arbiter, `ai-auto` locks scope once and runs end to end.

| Skill | Role |
| --- | --- |
| [`ai-main`](skills/ai-main/SKILL.md) | Entry point. Sizes the request and picks the lightest lane that can finish it. |
| [`ai-orchestrate`](skills/ai-orchestrate/SKILL.md) | Runs a job graph across inline work, subagents, Workflow fan-out and background sub-sessions in isolated worktrees, verifies every diff, and closes with an independent arbiter. |
| [`ai-auto`](skills/ai-auto/SKILL.md) | Locks scope once (one up-front conversation), collects every prerequisite, then runs end to end through `ai-orchestrate` without check-ins. |

```text
/ai-main <task>
   ├─ Direct          one deliverable, scope locked, fits this turn
   ├─ ai-orchestrate  scope locked, several deliverables / staged fan-out / arbiter
   └─ ai-auto         "ship it" end to end, scope or prerequisites not locked yet
                         Lock → Prepare → Execute (via ai-orchestrate)
```

The lane is re-evaluated at milestones: direct work that grows is moved into an orchestrated run, a fuzzy contract goes back to ai-auto's Lock gate.

## Install

One command installs everything:

1. the 3 skills plus [`jevgrep`](https://github.com/dzhng/jevgrep) from its upstream repo (`npx skills add` takes one source and has no dependency field, so the installer runs it twice and passes your flags to both runs);
2. the `ai` CLI and its `jg` shim, with pipx, from the same package copy (Python >= 3.9; pipx is installed with `python -m pip install --user pipx` when missing, then `pipx ensurepath`; an `ai` or `jg` that another tool put in the pipx bin dir is never overwritten: the installer skips the CLI and says what to remove);
3. the `jg` engine: `npm install --global @dzhng/jevgrep@latest` (Node >= 22).

Then open a new terminal and run `ai auth` to enter your Jev key. That is the only step left; skip it and Jev stays off.

```bash
# global (~/.claude/skills), available in every project
npx -y github:0x01001/ai-skill -a claude-code -y -g

# current project only (the ai CLI and jg are still installed per user)
npx -y github:0x01001/ai-skill -a claude-code -y

# Windows without Developer Mode (no symlinks): add --copy
npx -y github:0x01001/ai-skill -a claude-code -y -g --copy

# without anything Jev (no jevgrep, no ai CLI, no jg)
npx -y github:0x01001/ai-skill -a claude-code -y -g --no-jev
```

Steps 2 and 3 are best effort: if Python, pipx, Node >= 22 or a network install is missing, the installer prints what failed and the command to finish it by hand, and the skills still install (exit code reflects the skills only).

Update the skills with `npx skills update` (add `-g` for a global install). Each skill updates from its own source: the `ai-*` skills from this repo, `jevgrep` from the latest `dzhng/jevgrep`. Rerun the installer to update the `ai` CLI and `jg` too.

`jevgrep` is the upstream skill unchanged. With the `ai` CLI installed and `ai auth` done (see [Optional: Jev discovery](#optional-jev-discovery)), its `jg` calls go through the ai-skill shim and use that key, so no `jg auth` is needed. With the CLI but no key (before `ai auth` or after `ai logout`), the shim does not run `jg` at all: every call falls back with `reason=no_key` and the normal workflow continues. Without the CLI it behaves as upstream: it installs `jg@latest`, asks you to run `jg auth`, and keeps its own plaintext credentials outside ai-skill's control. The `ai-*` skills do not need it. Remove it with `npx skills remove jevgrep` (add `-g` for global).

Equivalent manual commands:

```bash
npx skills add 0x01001/ai-skill --skill '*' -a claude-code -y -g
npx skills add dzhng/jevgrep --skill jevgrep -a claude-code -y -g
pipx install "git+https://github.com/0x01001/ai-skill#subdirectory=cli" && pipx ensurepath
npm install --global @dzhng/jevgrep@latest
```

`ai-orchestrate` ships its own `.gitignore` (`__pycache__/`), so Python cache from `scripts/sessions.py` stays out of git in the target project.

## Usage

```text
/ai-main <task | issue URL | issue list> [--cross-runtime] [--resume <run-dir>] [--yes] [--yagni]
/ai-orchestrate <task | plan path | issue list> [--cross-runtime] [--resume <run-dir>] [--yes]
/ai-auto <request | issue URL> [--cross-runtime] [--resume <run-dir>] [--yagni]
```

`ai-main` is invoked only by the user (`disable-model-invocation: true`); the other two can also be routed to by `ai-main` or picked by the model.

| Flag | Effect |
| --- | --- |
| `--cross-runtime` | Allow workers on other runtimes (Codex, Orca). Off by default; see [cross-runtime.md](skills/ai-orchestrate/references/cross-runtime.md). |
| `--resume <run-dir>` | Continue a previous run from its `run.md` / `brief.md`. |
| `--yes` | Approve only the effects the contract already describes. Never replaces confirming new external effects. |
| `--yagni` | In ai-auto, cut everything the stated outcome does not need and list what was cut. |

Examples:

```text
/ai-main fix the typo in the login error message
/ai-main https://github.com/<owner>/<repo>/issues/42 https://github.com/<owner>/<repo>/issues/43
/ai-main build the CSV export feature end to end, open a PR when done
/ai-main --resume D:/repo/ai/running/ai-auto-261110-0930
```

## How a run works

- **Contract first.** ai-auto writes `brief.md` (Objective, Done means with verification, Out of scope, Constraints, Delivery, Authorized effects) and records the user's Confirm in `confirmed.md` with the brief's hash. An unconfirmed or edited brief authorizes nothing.
- **Job graph.** ai-orchestrate records one row per job in `run.md`: ownership, deps, checks, risk (R0–R3), tier and effort. Concurrent jobs never share write ownership.
- **Right unit per job.** Inline, subagent, Workflow, or a `claude --bg` sub-session in its own worktree (one per branch/PR). Models are named only in [models.md](skills/ai-orchestrate/references/models.md); everything else uses tiers.
- **Trust the diff, not the report.** Each result is checked against its commits, ownership and re-run checks; failures are classified and recovered per [recovery.md](skills/ai-orchestrate/references/recovery.md).
- **Independent arbiter.** Writer branches are merged into a local integration branch, checks re-run, then a fresh-context arbiter verifies the run against the contract (up to 3 rounds).
- **Approval gates.** Push, PR, issue/label writes, deploy, delete, spending and messages need explicit approval unless the confirmed contract names them.
- **Clean up.** Sessions, dev servers and worktrees the run started are removed; branches and PRs stay for review unless the contract says merge.

Run state lives in `<repo-root>/ai/running/ai-auto-<YYMMDD-HHMM>/` or `ai-orchestrate-<YYMMDD-HHMM>/`. It is working state; consider adding `ai/running/` to the target project's `.gitignore`.

## Optional: Jev discovery

[Jev](https://docs.typesafe.ai) (TypeSafe) can replace broad scout sweeps with one ranked search (`jg`). It is off until you turn it on; without it, or before `ai auth`, every skill runs as described above with no extra prompt or dependency.

The [installer](#install) already put the `ai` CLI and `jg` in place (unless `--no-jev`), so turning Jev on is one command in a new terminal:

```bash
ai auth      # pick provider (arrow keys), enter the key (hidden), validate
ai doctor    # state, provider, secret store, which jg is on PATH (--json for scripts)
ai logout    # disable and delete the key; jg stays on the shim, which then refuses to run
```

From a checkout instead: `pipx install --editable ./cli`, then `npm install --global @dzhng/jevgrep@latest` (any `jg` version works; upstream jevgrep keeps it current).

- **Key.** Stored in the OS secret store (Windows Credential Manager, macOS Keychain, Secret Service/KWallet), bound to its provider, endpoint and model. `jev.json` holds metadata only. For each search the shim writes a temporary credentials file in a private dir, deletes it when `jg` exits, and drops any output that contains the key. Never paste the key into chat; agents never run `ai auth`.
- **What is sent.** Entering a key turns Jev on; there is no separate consent step. Jev receives source excerpts from the repos where the agent runs discovery, minus `.env*`, keys, credentials, `*.log`, `ai/running/` and dependency dirs (a heuristic, not a guarantee). That covers every repo where the skills or `jevgrep` are installed (a global install means every repo), so choose the install scope (global or per project) to choose where Jev may run.
- **One auth for both.** The package installs two commands, `ai` and a `jg` shim. `ai auth` puts the pipx bin dir before npm's in your user PATH (Windows: `HKCU\Environment`) so `jg` resolves to the shim. `ai logout` keeps that order on purpose: `jg` stays on the shim, so a `jg auth` login of the npm `jg` cannot send source once Jev is off. `pipx uninstall ai-skill-jev` removes the shim and `jg` falls through to npm's again. If the npm `jg` sits in the machine PATH, `ai doctor` reports `shadowed` and says what to move. On macOS/Linux, put the pipx bin dir first yourself.
- **Shim.** `jg` needs a Jev key. Before `ai auth`, or after `ai logout`, the shim never runs the real `jg`: every command exits 3 with `ai-skill jev: unavailable (reason=no_key)` and callers keep the normal workflow. `jg auth` is always blocked. With a key: searches need only the key (any installed `jg` version works; a new upstream release never turns Jev off), get `--max-requests 100` (a higher value is lowered) and the exclusions, must search inside the git repo of the working directory, cannot use `--include-sensitive`/`--hidden`/`--no-ignore`/`--include-dependencies`, stop after 90 s, and fall back with exit 3 and `ai-skill jev: unavailable (reason=...)`; a search that fails with no output is a fallback too (`auth_error` when the provider rejects the key: run `ai auth` again; otherwise `provider_error`). Runs started by `ai-orchestrate`/`ai-auto` are capped at 3 searches; workers never call it. The `ai-*` skills call `jg` only after `ai doctor --json` reports `"jg": "shim"`, and never use `jg auth` credentials. Policy: [jev.md](skills/ai-orchestrate/references/jev.md).
- **Limits.** Token savings and quality are not measured yet (plan P0.5/P4); the budget numbers are starting values. Calls through the upstream `jevgrep` skill count only against the per-search cap.

Tests (no network, no real key, store or PATH): `cd cli && PYTHONPATH=src python -m unittest discover -s tests -t .`; installer (fake commands, nothing installed): `npm test`.

## Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code) with background sessions (`claude --bg`, `claude agents`); commands verified on 2.1.318–2.1.322.
- `git` (worktrees) and, for issues/PRs/labels, [`gh`](https://cli.github.com/) authenticated.
- `python3` for [`scripts/sessions.py`](skills/ai-orchestrate/scripts/sessions.py) (`status`, `last`, `usage`, `wait` on sub-sessions).
- Optional: Codex or Orca CLI for `--cross-runtime`.
- Optional, for Jev discovery: Python >= 3.9 and Node >= 22. The installer adds `pipx` (if missing), the `ai` CLI and `@dzhng/jevgrep` (any version).

## Repository layout

```text
skills/
├── ai-main/SKILL.md
├── ai-auto/SKILL.md
└── ai-orchestrate/
    ├── SKILL.md
    ├── .gitignore
    ├── scripts/sessions.py
    └── references/
        ├── models.md         tier → model, effort, ceiling
        ├── sub-sessions.md   launch, watch, talk, clean up
        ├── worker-brief.md   template for every worker brief
        ├── recovery.md       failure classes and escalation
        ├── cross-runtime.md  Codex / Orca workers (opt-in)
        └── jev.md            optional Jev discovery: gate, budget, fallback
cli/                          optional `ai` CLI + `jg` shim (Python, pipx)
├── pyproject.toml
├── src/ai_skill_jev/         cli, credentials, provider, jg_runner, jg_shim, pathenv, winsec
└── tests/
bin/install.mjs, lib.mjs      one-command installer (skills, upstream jevgrep, ai CLI, jg)
test/install.test.mjs         installer tests (npm test)
```

## License

[MIT](LICENSE)
