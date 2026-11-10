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

```bash
# all 3 skills into the current project
npx skills add 0x01001/ai-skill -a claude-code -y

# Windows without Developer Mode (no symlinks)
npx skills add 0x01001/ai-skill -a claude-code -y --copy

# global (~/.claude/skills), available in every project
npx skills add 0x01001/ai-skill -a claude-code -y -g
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

## Requirements

- [Claude Code](https://docs.claude.com/en/docs/claude-code) with background sessions (`claude --bg`, `claude agents`); commands verified on 2.1.318–2.1.322.
- `git` (worktrees) and, for issues/PRs/labels, [`gh`](https://cli.github.com/) authenticated.
- `python3` for [`scripts/sessions.py`](skills/ai-orchestrate/scripts/sessions.py) (`status`, `last`, `usage`, `wait` on sub-sessions).
- Optional: Codex or Orca CLI for `--cross-runtime`.

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
        └── cross-runtime.md  Codex / Orca workers (opt-in)
```

## License

[MIT](LICENSE)
