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

Pass haiku, sonnet, opus, or fable only when the worker runtime is claude-code. Every other runtime keeps the tier and the effort word and does not pass those aliases.

## Job to tier and starting effort

| Job                                                                                                                | Tier                                                                            | Effort   |
| ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------- | -------- |
| R0 (scout, log reading, diff summary), or a purely mechanical R1 (rename, pattern edit)                            | `t1`                                                                            | `low`    |
| R1 implement or fix with a clear scope and a known cause and approach                                              | `t2`                                                                            | `medium` |
| R2 implement (money, auth, data integrity, concurrency, a shared API or schema), or a debug whose cause is unknown | `t3`                                                                            | `medium` |
| Large migration, architecture redesign, deep research                                                              | `t3`                                                                            | `high`   |
| Review, arbiter, advisor                                                                                           | One tier above the run's strongest writer, at least `t3`, capped at the ceiling | `high`   |

R3 marks an approval gate, not a tier: an R3 job takes the row its work fits.

Tier and effort may be suggested by `ai decide jobs` ([jev.md](jev.md)); its risk is never below what the hard rules set (sensitive areas, external effects), and Rules 2–3 still apply to its suggestions.

## Rules

1. **Executable check first.** The starting effort holds only for a job whose check is a command; a job that can only be judged by reading starts at `high`. A read-only job counts as checked by command when its report cites `file:line` or commands the arbiter can re-run, so a `t1` scout starts at `low`.
2. **Pin every dispatch** through [hosts.md](hosts.md). A claude-code worker passes model and effort explicitly. Codex and Orca workers resolve the model as [cross-runtime.md](cross-runtime.md) "Resolve the model" says. A Cursor, OpenCode, or omp worker whose tool schema has no model or effort field passes neither and records host-configured model, not matched to tier; it runs only when the contract has no model pin and the run's effective ceiling (Rule 3: the lower of the ai model ceiling and any contract cap) is t4; otherwise the job is blocked with that schema and the ceiling as evidence, because the unverified model may exceed the pin or ceiling. A contract pin that is one of those four aliases blocks the job unless the worker runtime is claude-code.
3. **Ceiling.** At the start of each run or resume, read `ai model` and record the resolved ceiling in `run.md`. On a host other than claude-code, still run ai model and record its output. Do not map that alias onto the host model. A t1–t4 contract cap still limits which job row you may pick, but you do not pass the alias. Effort on a Codex worker tops out at high unless the contract pins xhigh. There is no xhigh on any other non-Claude runtime; high is the top. Map its alias with the table above; use the default `t4` when the command is not installed or the installed CLI predates it. If a supported command fails or returns an unknown alias, stop and report the setting error. The run uses the lower of that tier and any contract cap; effort tops out at `high`, except on a Codex worker whose contract pins `xhigh`: that job's effort ceiling is `xhigh`, so the clamp below keeps the pin. If the account rejects `t4` and no pin needs it, reduce the ceiling to `t3` or the current lower cap.
   Before every dispatch, including retries, clamp the proposed tier and effort to the ceiling. Skip a recovery rung if it cannot raise the tier or effort as intended. Pins above the ceiling or unavailable on the selected runtime make the job `blocked`, never quietly downgraded. Recompute and record the arbiter's tier whenever the ceiling changes, including on resume. The arbiter can share the writer's tier at the ceiling; its context must still be fresh. If the contract requires a stronger reviewer, block when that requirement cannot be met within the ceiling.
4. **Risk from project rules.** They set a job's risk when they name sensitive areas or pin a model for one; otherwise rank the job by the impact of its change.
