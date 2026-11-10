# Models: tier, effort, ceiling

The only place these skills name a model; everywhere else says a tier.

## Tier to model

Aliases for Claude Code units (`Agent`, Workflow `agent()`, `claude --bg --model`). Row order is the escalation order.

| Tier | Model |
| --- | --- |
| `t1` | `haiku` |
| `t2` | `sonnet` |
| `t3` | `opus` |
| `t4` | `fable` |

## Job to tier and starting effort

| Job | Tier | Effort |
| --- | --- | --- |
| R0 (scout, log reading, diff summary), or a purely mechanical R1 (rename, pattern edit) | `t1` | `low` |
| R1 implement or fix with a clear scope and a known cause and approach | `t2` | `medium` |
| R2 implement (money, auth, data integrity, concurrency, a shared API or schema), or a debug whose cause is unknown | `t3` | `medium` |
| Large migration, architecture redesign, deep research | `t4` | `high` |
| Review, arbiter, advisor | One tier above the run's strongest writer, at least `t3`, capped at the ceiling | `high` |

R3 marks an approval gate, not a tier: an R3 job takes the row its work fits.

## Rules

1. **Executable check first.** The starting effort holds only for a job whose check is a command; a job that can only be judged by reading starts at `high`. A read-only job counts as checked by command when its report cites `file:line` or commands the arbiter can re-run, so a `t1` scout starts at `low`.
2. **Pin every dispatch.** Claude Code units pass model and effort explicitly: `Agent(model, effort)`, Workflow `agent()` opts, `claude --bg --model <alias> --effort <level>`. A job on another runtime keeps its tier and effort and resolves its model in [cross-runtime.md](cross-runtime.md).
3. **Ceiling.** The run's ceiling is `t4`, or the contract's cap when that is lower. When the account rejects `fable` and no pin needs it, the ceiling is `t3`, so no climb re-runs the same model one tier up. A pin or cap binds writers and reviewers alike: a pinned model or runtime that is unavailable makes the job `blocked`, never quietly downgraded. An arbiter tier computed above the ceiling becomes the ceiling, even when that equals the writer's tier or sits below `t3`; its fresh context is then what keeps it independent.
4. **Risk from project rules.** They set a job's risk when they name sensitive areas or pin a model for one; otherwise rank the job by the impact of its change.
