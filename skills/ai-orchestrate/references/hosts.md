# Hosts

The coordinator and every worker follow this file for any operation that is not plain shell, git, or reading a file. Bind the host before the first such operation.

## Skill root

The skill root is the directory that contains the SKILL.md you are following. Sibling skill `<name>` is `<this-skill-root>/../<name>/SKILL.md`. `sessions.py` is `<ai-orchestrate-skill-root>/scripts/sessions.py`. Never hardcode `.claude/skills` or `.agents/skills` for these three skills. If the sibling file is missing, stop and say that skill is not installed for this host. Do not search the network and do not install it.

## Bind

Only the coordinator, the session the user invoked, runs this probe. A session whose prompt names a worker brief (`<run-dir>/jobs/<job-id>/brief.md`) is a worker. A worker (subagent or sub-session) does not probe: it takes `Host:` and `Worker runtime:` from its brief. A brief with no `Host:` line means `generic`. A worker never runs `ask`; a question that blocks it is `BLOCKER / QUESTION`, as in [worker-brief.md](worker-brief.md).

First match wins. Record `host: <id>` in `run.md`, or in the brief Constraints when no `run.md` exists yet. `--host <id>` on the user command wins over the probe. An unknown `--host` value is a stop. If `--host` names an id whose requirement below is not met, stop and report the mismatch; do not fall through. Requirements: `claude-code` needs everything in rule 1; `opencode` needs the `skill` tool in rule 2; `omp` needs `skill://` skill reads in rule 3; `cursor` needs the `Task` tool in rule 4; `codex` and `generic` need no tool and are always accepted.

1. `claude-code` — this session can call tools named `Skill` and `AskUserQuestion`, and a subagent tool named `Agent` (older Claude Code builds name it `Task`).
2. `opencode` — this session can call a tool named exactly `skill` (lowercase) that loads a skill by name, and it cannot call `Skill`.
3. `omp` — oh-my-pi: this session cannot call `Skill` or a tool named `skill`, and it reads skills through `skill://<name>` URLs with its `read` tool (the system prompt lists skills that way, or the user invoked `/skill:<name>`). Its tools are lowercase (`task`, `ask`, `wait`); a lowercase `task` alone does not bind `cursor`.
4. `cursor` — this session can call a tool named `Task` to launch a subagent, and it cannot call `Skill` or a tool named `skill`.
5. `codex` — none of those tools exist, and this session was started as Codex (the loaded skill wrapper is a Codex skill block, or the user invoked `$ai-main`, `$ai-auto`, or `$ai-orchestrate`).
6. `generic` — anything else.

Do not bind `claude-code` because `claude` is on PATH. Do not bind `codex` because `codex` is on PATH. A PATH probe selects a worker runtime, not the host.

## Worker runtime

The default worker runtime is the host. A different runtime is allowed only when `--cross-runtime` is set or the request names that runtime, using [cross-runtime.md](cross-runtime.md). A Codex host's own `codex exec` workers are this host profile, not cross-runtime. A Claude Code host's own `claude --bg` workers are this host profile, not cross-runtime.

## Operations

`unavailable` means: run the job `inline` when the unit table allows inline; otherwise mark it `blocked` with class `runtime_missing` before dispatch. Never invent a tool name or field name that is not in this file and not in the tool schema this session already shows you.

Which row to read: `ask`, `handoff`, and `spawn-subagent` use the host's row. `spawn-sub-session`, `wait`, `talk`, `stop`, and `usage` use the row of that job's worker runtime, the `runtime` field of that job in `run.md`. An inline or subagent job's runtime is the host. A sub-session's runtime is the host, or `codex` / `orca` when it runs cross-runtime. A Claude Code host with a Codex worker uses the `codex` rows for that job, not `SendMessage` or `claude stop`. An Orca worker uses [cross-runtime.md](cross-runtime.md), not this file.

### ask

One question, recommended option first. Same content rules as the skill that called you.

- `claude-code`: `AskUserQuestion`.
- `omp`: the `ask` tool, filling only fields its schema in this session shows. Without that tool (a non-interactive run), do what `generic` does.
- `codex`, `cursor`, `opencode`, `generic`: one user-visible message, other options labeled, then wait for the reply. Do not call `AskUserQuestion`.

### handoff

Load sibling skill `<name>` and continue with `<args>` as the request, in this same session. Do not start a new top-level session for a handoff. `<args>` always carries `--host <bound id>` (add it when absent), so the sibling skill keeps this binding instead of probing again.

- `claude-code`: `Skill(skill: "<name>", args: "<args>")`.
- `opencode`: the `skill` tool, with `<name>` in the one input field that tool's schema in this session describes as the skill's name or id; then continue with `<args>` as the task text. If the schema has no such field, or more than one candidate, do what `codex` does below.
- `omp`: `read` `skill://<name>` and follow it; `<args>` is the request text. If that read fails, do what `codex` does below.
- `codex`, `cursor`, `generic`: read `<this-skill-root>/../<name>/SKILL.md` and follow it. `<args>` is the request text.

### spawn-subagent

A fragment in a fresh context, result needed this turn. Read-only when the job is R0.

- `claude-code`: `Agent` (or `Task` on builds that name it so) with `model` and `effort` from [models.md](models.md) (`haiku`, `sonnet`, `opus`, `fable`; `low`, `medium`, `high`). `isolation: "worktree"` only when [recovery.md](recovery.md) rung 4 says so.
- `cursor`: one `Task` tool call. The prompt is the brief path plus the one-line goal. Foreground: do not set a background flag. If the visible Task schema has a readonly, model, or effort field, set readonly for R0 and pass the effort word only when that schema's enum contains it. Do not pass `haiku`, `sonnet`, `opus`, or `fable`. If the schema has no such field, omit it and record `host-configured model, not matched to tier`.
- `opencode`: if this session's tool list has a subagent or task tool, call that tool with the brief path as the prompt and no Claude model alias. Model and effort follow the same rule as `cursor` above: set them only when that tool's schema has such fields; otherwise record `host-configured model, not matched to tier`. If no such tool exists, this operation is `unavailable`.
- `omp`: one `task` call with one item. The task text is the brief path plus the one-line goal; fill every field the schema marks required (such as `context` and `solutionSpace`) from the brief. Do not set `isolated`. Do not pass `model` (it takes an exact `provider/model-id` only when the operator allows per-call models) or a Claude alias; record `host-configured model, not matched to tier`. Pass `effort` only when the schema has it, mapping `low`, `medium`, `high` to `lo`, `med`, `hi`. R0 stays read-only through its brief.
- `codex`: `unavailable`. Use spawn-sub-session, also for a read-only job that has no branch, such as the arbiter of a run that produced no branch or PR. Do not call `spawn_agent`.
- `generic`: `unavailable`.

### spawn-sub-session

One deliverable with its own branch or PR, or a read-only job (the arbiter included) on a host whose `spawn-subagent` is `unavailable`. The worktree already exists, detached at the commit it reads for a read-only job: [sub-sessions.md](sub-sessions.md) Launch steps 1 and 2, using `git worktree add`, unless the worktree-helper probe below passes.

- `claude-code`: the launch command in [sub-sessions.md](sub-sessions.md). Permission mode, `--add-dir`, and the name pattern stay as written there.
- `codex`: experimental. The Codex CLI command block in [cross-runtime.md](cross-runtime.md), including that file's model and effort rules. `--cross-runtime` is not required when the host is `codex`. A read-only job, the arbiter included, runs with `-C` on its detached worktree at the commit it reads ([sub-sessions.md](sub-sessions.md) Launch step 1), never the main checkout, with `-s read-only`. Record the session id the command prints and the pid of the process you started. If the command fails because the sandbox refuses the worktree path, `~/.codex`, or the network, mark the job `blocked` with class `runtime_missing` and the command output as evidence. Do not retry with a weaker sandbox flag. This row serves two cases: a `codex` host's own workers, and Codex workers that a `claude-code` host launches with `--cross-runtime`. A limit that applies only to a `codex` host is written as `On a codex host:` inside this row, never by turning the whole row `unavailable`.
- `cursor`: one `Task` tool call. The prompt says the worktree path is the only writable root and names the brief. If the visible schema has a cwd or path field, set it to that worktree. If it has an isolation or worktree flag, set it. Record the agent id the tool returns. Do not pass a Claude model alias or `--permission-mode`.
- `omp`: one `task` call, as in spawn-subagent. Do not set `isolated`: omp's isolation makes its own checkout and applies the patch back to the parent checkout, bypassing the job's worktree and branch. The prompt says the worktree path is the only writable root and that every command runs inside it (`cd <worktree>` or `git -C <worktree>`), and names the brief. Record the agent id the tool returns (`agent://<id>`).
- `opencode`: `unavailable`.
- `generic`: `unavailable`.

### wait

- `claude-code`: `SendMessage(to: "<name>", notify_when_idle: true)` with no message. Fallback: `python3 <ai-orchestrate-skill-root>/scripts/sessions.py wait <ids...>`, with the same `--all` and `--timeout` rules as [sub-sessions.md](sub-sessions.md). Never `<repo-root>/.claude/skills/...`.
- `codex`: the `codex exec` you started is the wait. If this session's shell tool can run a process in the background and notifies on exit, use that and do not poll. Otherwise run it in the foreground; if the shell tool times out before the process exits, run `stop` for that process first, then mark the job `blocked` with class `runtime_missing` and the partial output as evidence. Keep its worktree as evidence: mark it `evidence-kept` in `run.md`, and Clean up leaves it in place. Do not call `sessions.py`. The report is the newest `-o` file `run.md` records for a read-only job (`last.md`, or `last-r<n>.md` after a resume), otherwise `report.md`.
- `cursor`: a foreground Task has already waited; its tool result is the worker's final message. A background Task is used only when the visible Task schema has a background field; its result is whatever the completion notice delivers. Do not read a guessed file path for the result. Do not poll in a loop. Do not call `sessions.py`.
- `omp`: a blocking `task` call has already waited. A background one (async enabled) delivers its result on completion; to block for it, use the `wait` tool when this session has it. The full output is `read agent://<id>` when the result shows only a preview. Do not poll. Do not call `sessions.py`.
- `opencode`, `generic`: there is no handle to wait on.

### talk

- `claude-code`: `SendMessage` to the session name. `ListAgents` lists names, including this session's name for the brief. Delivery stays inside the permission-class rule in [sub-sessions.md](sub-sessions.md).
- `codex`: `codex exec resume -o "<run-dir>/jobs/<job-id>/last-r<n>.md" <session-id> "<follow-up>"` only after that exec has exited, `<n>` the resume count for that job; record that file in `run.md` as the job's newest report. Do not resume a running exec. A question that blocks the worker is `BLOCKER / QUESTION` in `report.md`; the coordinator reads it after wait. Do not invent a live message tool.
- `cursor`: if the Task schema in this session has a resume field, pass the recorded agent id and the follow-up. If it has no resume field, a follow-up is a new Task with the brief plus the evidence, recorded as a new attempt. A blocking question is `BLOCKER / QUESTION` in `report.md`.
- `omp`: `write agent://<id>` with the follow-up only when the `task` tool description in this session says peer messaging is enabled. Otherwise a follow-up is a new `task` with the brief plus the evidence, recorded as a new attempt. A blocking question is `BLOCKER / QUESTION` in `report.md`.
- `opencode`, `generic`: a blocking question is `BLOCKER / QUESTION` in the worker's final message. No live message tool.

### stop

Stop ends the worker process only. It never removes a worktree. Worktree removal happens only at the run's Clean up step ([sub-sessions.md](sub-sessions.md) Clean up), never inside `stop`, and never for a job marked `blocked` whose worktree is kept as evidence.

- `claude-code`: `claude stop <id>` keeps the conversation; `claude rm <id>` deletes it.
- `codex`: stop the process you started. Do not run `claude stop`.
- `cursor`: no CLI stop. At Clean up, `git worktree remove` a worktree this run created only when `git worktree list` shows it and it has no uncommitted changes. Never remove a worktree you cannot see in `git worktree list`.
- `omp`: `write proc://<id>/kill` for a background job. Worktree removal follows the `cursor` rule above.
- `opencode`, `generic`: nothing to stop.

### usage

Tokens only, never a price, same rule as ai-orchestrate section 7.

- `claude-code`: `sessions.py usage <id|name>` for a sub-session; the `Agent` result's usage for a subagent.
- `codex`: the `tokens used` line that command prints, otherwise `n/a`.
- `cursor`, `omp`, `opencode`, `generic`: `n/a` unless the tool result includes a token count.

## Workflow unit

Only `claude-code` loads the `workflow-authoring` skill and uses Workflow `agent()` with `resumeFromRunId`. Every other host has no Workflow unit. Three or more subagent-sized jobs become parallel `spawn-subagent` calls when that operation is available. When it is `unavailable`, run each job `inline` if the unit table allows inline; otherwise mark that job `blocked` with `runtime_missing`. Do not load `workflow-authoring` on any other host.

## Project rules

Read the rules this host already loaded. Also read `AGENTS.md` at the repo root when that file exists. On `claude-code`, `CLAUDE.md` and `.claude/rules/` are part of those rules. On every host, those project rules win over this skill and over the brief.

## Worktree helper

Optional. Probe `<repo-root>/.agents/skills/worktree/scripts/worktree.cjs`, then `<repo-root>/.claude/skills/worktree/scripts/worktree.cjs`. The first that exists may be used, and only under the dry-run rule already in [sub-sessions.md](sub-sessions.md) Launch. If neither exists, `git worktree add` only.

## Invocation

Same flags on every host: `--cross-runtime`, `--resume <run-dir>`, `--yes`, `--yagni`, `--host <id>`.

- `claude-code`: `/ai-main`, `/ai-auto`, `/ai-orchestrate`
- `codex`: `$ai-main`, `$ai-auto`, `$ai-orchestrate`
- `cursor`: `/ai-main`, `/ai-auto`, `/ai-orchestrate`
- `opencode`: `/ai-main`, `/ai-auto`, `/ai-orchestrate`
- `omp`: `/skill:ai-main`, `/skill:ai-auto`, `/skill:ai-orchestrate`
- `generic`: that host's own command for the skill name

`ai-main` is user-invoked. `disable-model-invocation: true` stays in its frontmatter. Codex reads `agents/openai.yaml` in that skill instead of that field. A host that honours neither may load `ai-main` on its own; on such a host, if the user did not name `ai-main`, sizing a task is not a reason to stay in it: answer the request directly.
