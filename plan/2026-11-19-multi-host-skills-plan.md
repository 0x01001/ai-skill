# Plan: skills chạy được trên host khác Claude Code

## Context

`skills/` hôm nay là skill Claude Code: `Skill()`, `Agent`, `AskUserQuestion`, `SendMessage`, `ListAgents`, `claude --bg`, đường dẫn `.claude/skills/ai-orchestrate/scripts/sessions.py`. `npx skills` cài cùng thư mục `SKILL.md` cho nhiều agent (canonical `.agents/skills/`, symlink `.claude/skills/`), nhưng body không chạy được khi host không có những tool đó.

Kết quả cần có: một bản `ai-main`, `ai-auto`, `ai-orchestrate`. Host đọc skill tự bind theo tool nó thật sự có, rồi hỏi người dùng, chuyển skill, và spawn worker theo profile. Khi host là Claude Code, lệnh worker và gate không đổi. Host không có isolated worker vẫn làm được lane Direct và việc inline; job cần session riêng thì `blocked`, không bịa tool.

Không đụng `cli/src/ai_skill_jev/provider.py`. Provider Jev (`typesafe`, `vercel`, `openrouter`, `opencode`, `custom`) đã tách khỏi host đang đọc skill.

## Approach

Một file profile, không fork skill theo agent. Mọi tên tool và lệnh spawn nằm trong `skills/ai-orchestrate/references/hosts.md`. Ba `SKILL.md` chỉ gọi tên operation. `sub-sessions.md` giữ nguyên cơ chế Claude Code và được hosts.md trỏ tới, không copy lệnh.

Host đóng, đúng sáu id: `claude-code`, `opencode`, `omp`, `cursor`, `codex`, `generic`. `omp` là oh-my-pi (github.com/can1357/oh-my-pi), thêm sau bản plan đầu. Gemini CLI, GitHub Copilot, Windsurf và mọi agent còn lại trong bảng `npx skills` dùng `generic`. Không thêm profile nào khác trong change này.

### 1. Viết `skills/ai-orchestrate/references/hosts.md`

Tạo file mới, nội dung dưới đây là toàn bộ file. Copy nguyên văn khối này vào file: không diễn giải, không rút gọn, không đổi thứ tự. Không thêm host, tool, hay flag ngoài các dòng này.

```markdown
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
```

### 2. Frontmatter và file Codex cho `ai-main`

Trong cả ba `SKILL.md`, thêm một dòng frontmatter ngay sau `argument-hint`, không đổi `name`, `description`, hay `disable-model-invocation` (`argument-hint` chỉ thêm `--host`, xem dưới):

```yaml
compatibility: "git required. Host profiles: Claude Code, Codex, Cursor, OpenCode, oh-my-pi (omp); any other Agent Skills host runs the generic profile (direct and inline only). Claude background workers need the claude CLI and Python 3. Optional: gh, the ai CLI."
```

Giá trị phải nằm trong nháy kép: nó chứa `: ` (`Host profiles: …`, `Optional: …`), để trần thì YAML không hợp lệ. Nội dung không chứa dấu `"`.

`argument-hint` của cả ba skill thêm `[--host <id>]` vào cuối chuỗi, trước dấu `"` đóng; phần còn lại của chuỗi giữ nguyên. Đây là thay đổi duy nhất được phép trên `argument-hint`.

`description` giữ nguyên. Không kéo danh sách host vào `description` (giới hạn spec 1024 ký tự; field này đã đủ).

Tạo `skills/ai-main/agents/openai.yaml`, đúng hai dòng:

```yaml
policy:
  allow_implicit_invocation: false
```

Không thêm key khác. File này chỉ để Codex không tự gọi `ai-main`. Claude Code, Cursor, OpenCode, omp bỏ qua file lạ trong thư mục skill.

### 3. Sửa ba `SKILL.md`: gọi operation, không gọi tool host

Giữ nguyên gate, bảng lane, luật `--resume` (trừ phần host/runtime bên dưới), luật brief/confirmed hash, luật Jev, luật R3, luật cleanup (trừ ngoại lệ `evidence-kept` bên dưới). Chỉ đổi cách gọi tool, câu project-rules, và các mục ghi rõ ở đây.

`skills/ai-main/SKILL.md`:

- Câu project-rules đầu file đổi thành: `Bind the host in [hosts.md](../ai-orchestrate/references/hosts.md) before any ask, handoff, or spawn. Project rules, as that file defines them, win over this skill and every lane.`
- Mọi chỗ `Skill(skill: "ai-auto", args: "...")` và `Skill(skill: "ai-orchestrate", args: "...")` đổi thành `handoff` tới skill đó với cùng args (hosts.md). Args `--resume`, `--cross-runtime`, `--yes`, `--yagni` giữ nguyên nghĩa; `handoff` tự thêm `--host <bound id>`.
- `AskUserQuestion` đổi thành operation `ask`.
- Scout "subagent at tier `t1`" đổi thành `spawn-subagent` tier `t1` khi operation đó available; không available thì nhìn repo inline, không dispatch.

`skills/ai-auto/SKILL.md`:

- Câu project-rules đổi thành: `Bind the host in [hosts.md](../ai-orchestrate/references/hosts.md) before any ask or handoff. Project rules, as that file defines them, win over the brief.`
- `AskUserQuestion` đổi thành `ask`. Vẫn một câu một lúc, recommended answer first. Option `preview` chỉ trên `claude-code` (field riêng của `AskUserQuestion`); host khác mô tả phần cần so ngay trong message của `ask`.
- Cả hai chỗ `Skill(skill: "ai-orchestrate", args: ...)` đổi thành `handoff` với cùng args (`<run-dir>/brief.md` cộng `--cross-runtime` khi user truyền; `--resume <that dir>` khi Resume; `--host <bound id>` luôn có, do `handoff` thêm).
- Bullet Permission mode: thêm một câu `On claude-code, the permission-mode flag is the one in sub-sessions.md. On every other host, do not pass a Claude permission-mode flag; if this host prompts before edits or commands, say so now.`

`skills/ai-orchestrate/SKILL.md`:

- Câu project-rules đổi thành cùng dạng ai-main, link `references/hosts.md`.
- Bảng field job (khoảng dòng 25–34): thêm hàng `| runtime | worker runtime that runs it: the host for inline and subagent jobs; the host, codex, or orca for a sub-session ([hosts.md](references/hosts.md), Operations) |` ngay sau hàng `unit`. Câu "Record the unit, tier, …" (khoảng dòng 54) thêm `runtime` sau `unit`.
- Resume: `run.md` không có dòng `host:` là run tạo trước change này; coi host của coordinator là `claude-code`. Job đã từng dispatch (có ít nhất một attempt trong `run.md`) mà thiếu `runtime` thì suy từ lần dispatch đã ghi: unit inline, subagent, Workflow hoặc sub-session là `claude-code`; unit cross-runtime là `codex` khi job ghi Codex session id hoặc model Codex, `orca` khi job ghi Orca task id. Job đã dispatch mà không suy được thì đánh `blocked` riêng job đó, nêu lý do và hỏi user; không đoán, không dừng các job khác. Job chưa từng dispatch thì không suy lúc resume: `runtime` của nó được chọn khi job tới lượt dispatch, theo cùng luật chọn unit như run mới, rồi ghi vào `run.md`. Nếu `host:` của `run.md` (hoặc `claude-code` theo luật trên) khác host vừa bind, dừng và bảo user resume run đó trên host ghi trong `run.md`; không đổi hàng operation giữa chừng. Kiểm trạng thái job đang chạy: `sessions.py status` chỉ cho job có `runtime` là `claude-code`; runtime khác dùng `wait`/`talk` của profile đó. Job `done` mà evidence còn thì giữ, như hiện tại.
- Bullet Clean up (khoảng dòng 80): câu `Keep only a worktree git refuses to remove (uncommitted changes) and list it with why.` đổi thành `Keep a worktree git refuses to remove (uncommitted changes), and a worktree run.md marks evidence-kept for a blocked job; list each with why.` Job `blocked` mà worktree giữ làm bằng chứng (hosts.md `wait`, `stop`) ghi `evidence-kept` cạnh worktree đó trong `run.md` ngay lúc đánh `blocked`.
- Dòng 13: `AskUserQuestion` (xác nhận external effects) đổi thành `ask`. Dòng 13 và dòng 89: cả hai `Skill(skill: "ai-auto", ...)` đổi thành `handoff` với cùng args.
- Bảng unit, hàng Workflow: cột Fits nói `claude-code only; other hosts use the Workflow unit section of hosts.md`. Không bảo host khác load `workflow-authoring`.
- Hàng sub-session: bỏ `` `claude --bg` `` khỏi tên unit. Cột Fits thành `One deliverable with its own branch/PR, long-running, resumable. Mechanics: hosts.md spawn-sub-session. Claude Code commands: sub-sessions.md.`
- Hàng subagent: `spawn-subagent` (hosts.md), không gọi tên `Agent` trong `SKILL.md`.
- Arbiter (section 6): run không có branch/PR mà `spawn-subagent` là `unavailable` (host `codex`) thì arbiter là sub-session read-only trong detached worktree ở commit nó nghiệm thu.
- Bullet Wait: `wait` trong hosts.md. Xóa đường dẫn `.claude/skills/ai-orchestrate/scripts/sessions.py`.
- Bullet Relay: `talk` trong hosts.md, không viết `SendMessage` trong `SKILL.md`.
- Section 7 tokens: `usage` trong hosts.md. Giữ câu Codex `tokens used` và câu `n/a`, vì hosts.md là nguồn lệnh.

Sau bước này, grep ba `SKILL.md` không còn `Skill(skill:`, `AskUserQuestion`, `SendMessage`, `ListAgents`, `claude --bg`, `.claude/skills/ai-orchestrate`.

### 4. Reference còn lại trỏ về profile, không nhân đôi lệnh

`skills/ai-orchestrate/references/sub-sessions.md`: dòng mở đầu thành `Claude Code worker runtime only. Other hosts use hosts.md and do not run the commands in this file.` Giữ nguyên mọi lệnh `claude` bên dưới. Chỗ duy nhất `python3 <repo-root>/.claude/skills/ai-orchestrate/scripts/sessions.py` (dòng Fallback của Watch, khoảng dòng 34) đổi thành `python3 <ai-orchestrate-skill-root>/scripts/sessions.py`. Probe worktree helper đổi thành thứ tự trong hosts.md (`.agents/skills/worktree/...` rồi `.claude/skills/worktree/...`), cùng điều kiện dry-run đang viết.

`skills/ai-orchestrate/references/recovery.md`:

- Bảng class, hàng `process_crash`, `lost_handle`: cột Signs và Action hiện dùng `sessions.py status` cho mọi runtime. Đổi thành: `sessions.py status` chỉ khi `runtime` của job là `claude-code`; runtime khác xác nhận bằng hàng `wait` của profile đó trong hosts.md (process hoặc pid đã ghi, completion notice) cùng `git -C <worktree> status`. Phần re-dispatch từ worktree giữ nguyên.
- Rung 2: câu `claude --bg --resume ...` chỉ áp khi worker runtime là `claude-code`. Runtime khác nâng effort bằng `talk` hoặc, khi `talk` không resume được session đang sống, một attempt mới qua `spawn-sub-session` / `spawn-subagent`, ghi id mới vào `run.md`. Không chạy `claude --bg` trên runtime khác.
- Fallback 1: `talk` của profile, không viết `SendMessage` như lệnh chung. Câu `claude --bg --resume` giữ, gắn nhãn `claude-code` only.
- Fallback 4: `Agent` with `isolation: "worktree"` chỉ khi worker runtime là `claude-code`. Runtime khác mà `spawn-sub-session` bị từ chối bởi policy (không phải user từ chối một prompt) thì job `blocked` với class `permission_denied`, không đổi sang một tool Claude.

`skills/ai-orchestrate/references/worker-brief.md`:

- Câu `Subagent jobs put the same sections in the Agent prompt` đổi thành `spawn-subagent jobs put the same sections in that operation's prompt`.
- Dòng Rules `AGENTS.md, CLAUDE.md, .claude/rules/` đổi thành `the project rules hosts.md names`.
- Thêm vào đầu template brief hai dòng do coordinator điền: `Host: <id>` và `Worker runtime: <id>`, kèm một câu: `Do not probe the host; use these two lines. No Host: line means generic.` Đây là nguồn host của mọi worker (hosts.md Bind).
- Dòng cuối `SendMessage the coordinator` đổi thành `A question that blocks you: if your brief names a claude-code coordinator session, SendMessage that session and wait. Otherwise write it under BLOCKER / QUESTION and stop.` `SendMessage` trong file này chỉ còn trong câu điều kiện claude-code đó.

`skills/ai-orchestrate/references/models.md`:

- Dòng "Aliases for Claude Code units" giữ, thêm ngay sau bảng tier: `Pass haiku, sonnet, opus, or fable only when the worker runtime is claude-code. Every other runtime keeps the tier and the effort word and does not pass those aliases.`
- Rule 2 đổi thành: `Pin every dispatch through hosts.md. A claude-code worker passes model and effort explicitly. Codex and Orca workers resolve the model as cross-runtime.md "Resolve the model" says. A Cursor, OpenCode, or omp worker whose tool schema has no model or effort field passes neither and records host-configured model, not matched to tier; it runs only when the contract has no model pin and the run's effective ceiling (Rule 3: the lower of the ai model ceiling and any contract cap) is t4; otherwise the job is blocked with that schema and the ceiling as evidence, because the unverified model may exceed the pin or ceiling. A contract pin that is one of those four aliases blocks the job unless the worker runtime is claude-code.`
- Rule 3, câu `effort tops out at high` thêm ngoại lệ: `except on a Codex worker whose contract pins xhigh: that job's effort ceiling is xhigh, so the clamp below keeps the pin.`
- Rule 3, thêm sau câu đọc `ai model`: `On a host other than claude-code, still run ai model and record its output. Do not map that alias onto the host model. A t1–t4 contract cap still limits which job row you may pick, but you do not pass the alias. Effort on a Codex worker tops out at high unless the contract pins xhigh. There is no xhigh on any other non-Claude runtime; high is the top.`

`skills/ai-orchestrate/references/cross-runtime.md`: đoạn mở "Enter only with `--cross-runtime`" đổi thành hai luật:

- Orca, và Codex CLI khi host không phải `codex`: chỉ khi `--cross-runtime` hoặc request nêu tên runtime đó. Probe fail thì `blocked`, không đổi route im lặng. Giữ nguyên.
- Host là `codex`: block lệnh Codex CLI trong file này là `spawn-sub-session` của host, không cần `--cross-runtime`.

Job read-only, kể cả arbiter: chạy `-C` trên detached worktree ở commit nó đọc (sub-sessions.md Launch bước 1), `-s read-only`, không chạy ở repo root. Follow-up: `codex exec resume -o "<run-dir>/jobs/<job-id>/last-r<n>.md" <session-id> "<follow-up>"`; với job read-only, file đó (ghi trong `run.md`) thay `last.md` làm report.

Không sửa lệnh `codex exec` (ngoài `-C` cho job read-only và `-o` khi resume ở trên), effort `minimal|low|medium|high|xhigh`, hay đoạn Orca. Không thêm `spawn_agent`.

`skills/ai-orchestrate/scripts/sessions.py`: không đổi hành vi. Dòng docstring đầu đổi thành `Inspect and wait on Claude Code background sessions. Only the claude-code worker runtime calls this.`

### 5. Installer và README: cài được sang host kia, mặc định Claude không đổi

`bin/lib.mjs` `HELP`: trong ví dụ pass-through, đổi `-a claude-code` thành `-a claude-code, -a codex, -a cursor, -a opencode, -a pi`. Không thêm flag. Không đổi default. User không truyền `-a` thì `npx skills add` vẫn tự hỏi, như hiện tại. Không dùng `--agent '*'`.

`README.md`, sửa đúng các chỗ sau, không viết lại cả file:

- Câu mở: `Skills for Claude Code, Codex, Cursor, OpenCode, and oh-my-pi that take a task from request to verified result.` Phần sau của câu (`ai-main` routes…) giữ.
- Thêm một khối lệnh ngay sau khối install Claude, không xóa khối cũ:

```bash
# Claude Code, Codex, Cursor, and OpenCode, global
npx -y github:0x01001/ai-skill -a claude-code -a codex -a cursor -a opencode -y -g

# oh-my-pi (omp), global: npx skills has no omp id; -a pi installs to ~/.agents/skills, which omp reads
npx -y github:0x01001/ai-skill -a pi -y -g
```

`npx skills` không có agent id cho omp (vercel-labs/skills issue #1535 đóng, khuyên đọc `.agents/skills`). `-a pi` cài vào `.agents/skills` / `~/.agents/skills`, cả hai omp tự tìm.

- Usage: thêm bảng invocation đúng như mục Invocation trong hosts.md. Hai dòng cú pháp `/ai-main …` và `/ai-auto …` thêm `[--host <id>]` ở cuối. Bảng flag thêm một hàng: `| --host <id> | Force the host profile (claude-code, codex, cursor, opencode, omp, generic) instead of probing; stops if that host's required tool is missing. |`. Các flag khác giữ nguyên.
- Requirements, bullet Claude Code đổi thành: một host trong hosts.md. `claude --bg` và `python3` chỉ bắt buộc khi worker runtime là `claude-code`. `git` vẫn bắt buộc. Codex hoặc Orca vẫn optional cho `--cross-runtime`.
- Ngay dưới bảng invocation, một dòng: `Codex as host is experimental: background workers depend on codex exec running inside the Codex sandbox. Cursor, OpenCode, and oh-my-pi workers depend on the tools that host exposes; without them those hosts run direct and inline only.` Nếu smoke test Codex ở Verification fail, dòng này đổi thành `Codex as host runs direct and inline only.`
- Layout: thêm `references/hosts.md` và `ai-main/agents/openai.yaml`.

`.gitignore` đã có `.agents/skills/` và `.claude/skills/`. Không thêm dòng.

### 6. Test tĩnh

`package.json` script `test` đổi thành `node --test test/install.test.mjs test/skills-host.test.mjs`. Không dùng glob: Windows không nở `test/*.test.mjs` trong npm script.

`test/install.test.mjs` dòng comment đầu cập nhật thành lệnh đó. Không đổi assertion installer. Test help cấm chuỗi `--list` trong help; ví dụ agent mới không được chứa `--list`.

Tạo `test/skills-host.test.mjs`. `ROOT` giống install test: `resolve(dirname(fileURLToPath(import.meta.url)), "..")`. Đọc file bằng `node:fs`. Fail khi:

- `skills/ai-main/SKILL.md`, `skills/ai-auto/SKILL.md`, `skills/ai-orchestrate/SKILL.md` thiếu `compatibility:` hoặc giá trị đó dài hơn 500, hoặc `name:` không khớp tên thư mục.
- Ba file đó chứa một trong: `Skill(skill:`, `AskUserQuestion`, `SendMessage`, `ListAgents`, `claude --bg`, `.claude/skills/ai-orchestrate`.
- `skills/ai-orchestrate/references/hosts.md` thiếu một trong các heading operation `### ask`, `### handoff`, `### spawn-subagent`, `### spawn-sub-session`, `### wait`, `### talk`, `### stop`, `### usage`, hoặc thiếu id `` `claude-code` ``, `` `opencode` ``, `` `omp` ``, `` `cursor` ``, `` `codex` ``, `` `generic` ``.
- `skills/ai-main/agents/openai.yaml` không đúng hai dòng `policy:` và `  allow_implicit_invocation: false`.
- `skills/ai-orchestrate/references/sub-sessions.md` còn chuỗi `.claude/skills/ai-orchestrate`.
- `package.json` `scripts.test` không bằng lệnh ở trên.
- Frontmatter của ba `SKILL.md` (giữa hai dòng `---` đầu file): mỗi key `name`, `description`, `compatibility` không xuất hiện đúng một lần; hoặc một giá trị không bắt đầu bằng `"` mà chứa `: ` (YAML sẽ hỏng); hoặc giá trị `compatibility` không bắt đầu và kết thúc bằng `"`, hay chứa thêm `"` bên trong. Repo không có YAML parser trong dependency, nên kiểm bằng luật này, không thêm dependency.
- `skills/ai-orchestrate/SKILL.md` thiếu hàng `| runtime |` trong bảng field job.
- `argument-hint` của một trong ba `SKILL.md` thiếu `--host`.
- Một link markdown tương đối `](<path>)` trong ba `SKILL.md` hoặc trong `hosts.md` trỏ tới file không tồn tại (bỏ link `http(s)://`, cắt phần `#anchor` trước khi kiểm). Hiện mọi link trong ba `SKILL.md` đều trỏ tới file có thật, nên luật này không làm đỏ code cũ.
- `skills/ai-orchestrate/references/worker-brief.md` thiếu `Host:` hoặc `Worker runtime:`.

Không gọi mạng, không gọi `claude` hay `codex`.

## Critical files & anchors

- `skills/ai-orchestrate/SKILL.md` bảng unit (khoảng dòng 46–52) và bullet Wait (khoảng dòng 66): chỗ duy nhất `SKILL.md` còn lệnh Claude sau khi đọc sơ. Phải rời hết sang hosts.md.
- `skills/ai-orchestrate/references/sub-sessions.md` Launch và Watch: nguồn lệnh `claude --bg` / `sessions.py`. Giữ lệnh, đổi path script và câu mở.
- `skills/ai-orchestrate/references/cross-runtime.md` block `codex exec`: nguồn duy nhất của worker Codex. Host `codex` dùng lại, không copy.
- `skills/ai-main/SKILL.md` các lời gọi `Skill(skill:)`: handoff giữa ba skill. Args không được đổi nghĩa.
- `bin/lib.mjs` `HELP` và `test/install.test.mjs` test `--help`: help không được chứa `--list`.

## Verification

Từ thư mục gốc repo (`/Users/vnext/project/ai-skill`):

```bash
npm test
```

Kỳ vọng: mọi test trong `test/install.test.mjs` vẫn pass, và `test/skills-host.test.mjs` pass. Không cần `claude`, `codex`, mạng, hay key.

Đọc lại `skills/ai-main/SKILL.md`: có link `hosts.md`, không có `Skill(skill:`. Đó là bằng chứng lane handoff không còn khóa Claude trong file entry.

`npm test` xanh chỉ chứng minh file đúng hình dạng (chuỗi bị cấm, heading, frontmatter, link), không chứng minh skill chạy được trên host khác.

Trước khi merge, smoke test tay một lần trên Codex, host duy nhất có đường worker thật: từ một session Codex, `git worktree add` một worktree tạm, rồi chạy block `codex exec` của cross-runtime.md trong worktree đó với một brief ngắn: tạo `smoke.txt` chứa `ok` ở gốc worktree, rồi viết `report.md`. Pass khi đủ cả ba: process exit 0, `<worktree>/smoke.txt` tồn tại với nội dung `ok` (`git -C <worktree> status --porcelain` thấy nó), và `report.md` tồn tại. Chỉ có `report.md` mà không có `smoke.txt` là fail: worker chỉ ghi được vào `<run-dir>` qua `--add-dir`. Fail thì chỉ host `codex` mất worker: thêm vào đầu hàng `codex` của `spawn-sub-session` trong hosts.md câu `On a codex host: unavailable (nested codex exec failed the smoke test).`, giữ nguyên phần còn lại của hàng cùng các hàng `wait`, `talk`, `stop` cho Codex worker do host `claude-code` chạy qua `--cross-runtime`; dòng README đổi như mục 5. Ghi kết quả (pass/fail, output) vào mô tả PR. Xóa worktree tạm sau đó.

Không chạy skill trên Cursor/OpenCode/omp thật trong change này. Profile Cursor, OpenCode và omp chỉ được phép set field có trong schema tool của session; test không giả lập schema đó.

## Thứ tự làm

1. Commit riêng file plan này trước khi implement, để diff của change không lẫn với diff của plan. Chỉ commit khi user bảo.
2. Bước 1 → 6 ở trên, rồi `npm test`.
3. Smoke test Codex ở trên, trước khi merge.

## Assumptions & contingencies

- "Provider" ở đây là coding-agent host đang đọc skill, không phải provider Jev. Nếu người dùng muốn map model Claude sang model Cursor/Codex, không làm trong change này: job có pin `haiku|sonnet|opus|fable` trên worker runtime khác là `blocked`.
- `argument-hint` và `disable-model-invocation` giữ trong frontmatter. Codex bỏ qua field lạ (parser chỉ đọc `name`, `description`, `metadata`). Nếu một host từ chối cả file vì field lạ, không xóa field: Claude Code cần chúng. Ghi host đó là không load được và dừng, không tạo bản SKILL.md thứ hai.
- OpenCode có thể không có tool subagent. Khi tool list không có, `spawn-subagent` và `spawn-sub-session` là `unavailable`. Không đặt tên tool OpenCode ngoài `skill`.
- Tên field input của tool `skill` trong OpenCode chưa được kiểm chứng trong repo này. Bind chỉ dựa trên tên tool `skill`; handoff dùng field mà schema session mô tả là tên/id skill, không có thì đọc `SKILL.md` của sibling. Không hardcode `id` hay `name`.
- Cursor Task không có schema cố định trong repo. Chỉ set field mà schema session đang hiện. Không có field resume thì follow-up là attempt mới; không có field background thì chỉ chạy foreground. Không đoán tên field, không đoán đường dẫn file kết quả trên đĩa.
- Run tạo trước change này không có `host:` hay `runtime`. Coordinator của mọi run đó là Claude Code, nhưng worker có thể là Codex hoặc Orca qua `--cross-runtime`, nên `runtime` từng job được suy từ unit và id đã ghi (bước 3, Resume), không gán chung `claude-code`. Resume chéo host không hỗ trợ: handle của sub-session (`claude --bg`, `codex exec`) chỉ đợi và dừng được từ đúng runtime đó.
- omp: tool chữ thường `task`, `ask`, `wait`; không có tool `skill`, skill đọc qua `read skill://<name>`, gọi tay `/skill:<name>`; honour `disable-model-invocation`. Bind dựa trên `skill://`, đặt trước `cursor` để `task` chữ thường không bị nhận là `Task`. Chưa kiểm chứng system prompt omp luôn nhắc `skill://`; không thấy thì rơi về `generic`, user dùng `--host omp`. `spawn-sub-session` không set `isolated`: isolation của omp tự tạo checkout và apply patch về checkout cha, bỏ qua worktree và branch của job.
- Worker của Claude Code thường không có tool `Agent`, nên nếu tự probe sẽ bind sai thành `generic`. Vì vậy worker không probe; host đến từ dòng `Host:` trong brief.
- Host không tôn trọng `disable-model-invocation` lẫn `agents/openai.yaml` có thể tự load `ai-main`. hosts.md Invocation đã nói cách xử lý; không tạo bản `ai-main` riêng cho host đó.
- `spawn_agent` của Codex không dùng. Isolated worker Codex là `codex exec` đã có trong cross-runtime.md.
- Installer không đổi agent mặc định. Ví dụ README đa host là lệnh user tự chạy, không phải default của `bin/install.mjs`.
