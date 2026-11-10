# Cross-runtime workers (opt-in only)

Enter only with `--cross-runtime` or when the request names a runtime ("as Codex sessions", "Orca workers"). Probe each candidate live before routing to it; a failed probe makes the job `blocked` with the probe output as evidence, never a silent reroute.

## Resolve the model

Model ids and flags never carry across runtimes, and the aliases in [models.md](models.md) are Claude Code's. A job here keeps its tier and starting effort from [models.md](models.md) and resolves the rest from the target runtime at probe time, before dispatch:

- **Model:** the one the contract names for that runtime; otherwise the model the runtime is configured to use, recorded in `run.md` as "runtime-configured model, not matched to tier".
- **Effort:** the nearest level on that runtime's own scale, from its `--help` or, when that lists none, its config docs.
- Pass both explicitly in the launch command and record them in `run.md`.
- Check the contract's pins and cap against the resolved model. A model you cannot read, or a pin or cap you cannot match to it, makes the job `blocked` with the probe output.

## Codex CLI

- Probe: `codex --version` and `codex login status`; the configured default model and effort are `model` and `model_reasoning_effort` in `~/.codex/config.toml`. Codex starts a run with any `model_reasoning_effort` value, unknown ones included, so pick one of `minimal`, `low`, `medium`, `high`, `xhigh`. An unknown model fails only at the first request, so a model the contract pins gets a one-line probe first (`codex exec -s read-only -m "<model>" "Reply ok." < /dev/null`); a non-zero exit makes the job `blocked` with that output.
- Run in a worktree you created, in the background. A read-only job runs in the repo root with `-s read-only` and no `--add-dir`; that sandbox blocks every write, so its brief's Report section drops the `report.md` line and keeps "end with this as your final message", and the `last.md` that `-o` saves is the job's report:

  ```bash
  codex exec -C "<worktree>" --add-dir "<run-dir>" -s workspace-write \
    -m "<model>" -c model_reasoning_effort=<level> \
    -o "<run-dir>/jobs/<job-id>/last.md" \
    "Read <run-dir>/jobs/<job-id>/brief.md and carry it out; it is your whole task."
  ```

- Follow-up: `codex exec resume <session-id> "<follow-up>"`.
- For a second-opinion diagnosis or review rather than a job, the `codex:rescue` agent is the lighter route.

## Orca workers

Load the `orchestration` skill and follow it: Orca owns the worker's workspace, task DAG and ask/reply channel; record the model and effort it reports for each worker in `run.md`. Record each Orca task id in `run.md` like a session id.
