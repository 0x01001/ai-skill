# Plan: skills chạy được trên host khác Claude Code

## Context

`skills/` hôm nay là skill Claude Code: `Skill()`, `Agent`, `AskUserQuestion`, `SendMessage`, `ListAgents`, `claude --bg`, đường dẫn `.claude/skills/ai-orchestrate/scripts/sessions.py`. `npx skills` cài cùng thư mục `SKILL.md` cho nhiều agent (canonical `.agents/skills/`, symlink `.claude/skills/`), nhưng body không chạy được khi host không có những tool đó.

Kết quả cần có: một bản `ai-main`, `ai-auto`, `ai-orchestrate`. Host đọc skill tự bind theo tool nó thật sự có, rồi hỏi người dùng, chuyển skill, và spawn worker theo profile. Khi host là Claude Code, lệnh worker và gate không đổi. Host không có isolated worker vẫn làm được lane Direct và việc inline; job cần session riêng thì `blocked`, không bịa tool.

Không đụng `cli/src/ai_skill_jev/provider.py`. Provider Jev (`typesafe`, `vercel`, `openrouter`, `opencode`, `custom`) đã tách khỏi host đang đọc skill.

## Approach

Một file profile, không fork skill theo agent. Mọi tên tool và lệnh spawn nằm trong `skills/ai-orchestrate/references/hosts.md`. Ba `SKILL.md` chỉ gọi tên operation. `sub-sessions.md` giữ nguyên cơ chế Claude Code và được hosts.md trỏ tới, không copy lệnh.

Host đóng, đúng năm id: `claude-code`, `opencode`, `cursor`, `codex`, `generic`. Gemini CLI, GitHub Copilot, Windsurf và mọi agent còn lại trong bảng `npx skills` dùng `generic`. Không thêm profile mới trong change này.

### 1. Viết `skills/ai-orchestrate/references/hosts.md`

Tạo file mới, nội dung dưới đây là toàn bộ file. Không thêm host, tool, hay flag ngoài các dòng này.

```markdown
# Hosts

The coordinator and every worker follow this file for any operation that is not plain shell, git, or reading a file. Bind the host before the first such operation.

## Skill root

The skill root is the directory that contains the SKILL.md you are following. Sibling skill `<name>` is `<this-skill-root>/../<name>/SKILL.md`. `sessions.py` is `<ai-orchestrate-skill-root>/scripts/sessions.py`. Never hardcode `.claude/skills` or `.agents/skills` for these three skills. If the sibling file is missing, stop and say that skill is not installed for this host. Do not search the network and do not install it.

## Bind

First match wins. Record `host: <id>` in `run.md`, or in the brief Constraints when no `run.md` exists yet. `--host <id>` on the user command wins over the probe. If `--host` names an id in this file and that id's required tool is absent, stop and report the mismatch; do not fall through. An unknown `--host` value is the same stop.

1. `claude-code` — this session can call tools named `Skill`, `Agent`, and `AskUserQuestion`.
2. `opencode` — this session can call a tool named `skill` whose input has an `id` field, and it cannot call `Skill`.
3. `cursor` — this session can call a tool named `Task` to launch a subagent, and it cannot call `Skill` or OpenCode's `skill` tool.
4. `codex` — none of those tools exist, and this session was started as Codex (the loaded skill wrapper is a Codex skill block, or the user invoked `$ai-main`, `$ai-auto`, or `$ai-orchestrate`).
5. `generic` — anything else.

Do not bind `claude-code` because `claude` is on PATH. Do not bind `codex` because `codex` is on PATH. A PATH probe selects a worker runtime, not the host.

## Worker runtime

The default worker runtime is the host. A different runtime is allowed only when `--cross-runtime` is set or the request names that runtime, using [cross-runtime.md](cross-runtime.md). A Codex host's own `codex exec` workers are this host profile, not cross-runtime. A Claude Code host's own `claude --bg` workers are this host profile, not cross-runtime.

## Operations

`unavailable` means: run the job `inline` when the unit table allows inline; otherwise mark it `blocked` with class `runtime_missing` before dispatch. Never invent a tool name that is not in this file and not in the tool schema this session already shows you.

### ask

One question, recommended option first. Same content rules as the skill that called you.

- `claude-code`: `AskUserQuestion`.
- `codex`, `cursor`, `opencode`, `generic`: one user-visible message, other options labeled, then wait for the reply. Do not call `AskUserQuestion`.

### handoff

Load sibling skill `<name>` and continue with `<args>` as the request, in this same session. Do not start a new top-level session for a handoff.

- `claude-code`: `Skill(skill: "<name>", args: "<args>")`.
- `opencode`: `skill` tool with `{"id": "<name>"}`, then continue with `<args>` as the task text.
- `codex`, `cursor`, `generic`: read `<this-skill-root>/../<name>/SKILL.md` and follow it. `<args>` is the request text.

### spawn-subagent

A fragment in a fresh context, result needed this turn. Read-only when the job is R0.

- `claude-code`: `Agent` with `model` and `effort` from [models.md](models.md) (`haiku`, `sonnet`, `opus`, `fable`; `low`, `medium`, `high`). `isolation: "worktree"` only when [recovery.md](recovery.md) rung 4 says so.
- `cursor`: one `Task` tool call. The prompt is the brief path plus the one-line goal. Foreground: do not set a background flag. If the visible Task schema has a readonly, model, or effort field, set readonly for R0 and pass the effort word only when that schema's enum contains it. Do not pass `haiku`, `sonnet`, `opus`, or `fable`. If the schema has no such field, omit it and record `host-configured model, not matched to tier`.
- `opencode`: if this session's tool list has a subagent or task tool, call that tool with the brief path as the prompt and no Claude model alias. If it does not, this operation is `unavailable`.
- `codex`: `unavailable`. Use spawn-sub-session. Do not call `spawn_agent`.
- `generic`: `unavailable`.

### spawn-sub-session

One deliverable with its own branch or PR. The worktree already exists: [sub-sessions.md](sub-sessions.md) Launch steps 1 and 2, using `git worktree add`, unless the worktree-helper probe below passes.

- `claude-code`: the launch command in [sub-sessions.md](sub-sessions.md). Permission mode, `--add-dir`, and the name pattern stay as written there.
- `codex`: the Codex CLI command block in [cross-runtime.md](cross-runtime.md), including that file's model and effort rules. `--cross-runtime` is not required when the host is `codex`. Record the session id the command prints and the pid of the process you started.
- `cursor`: one `Task` tool call. The prompt says the worktree path is the only writable root and names the brief. If the visible schema has a cwd or path field, set it to that worktree. If it has an isolation or worktree flag, set it. Record the agent id the tool returns. Do not pass a Claude model alias or `--permission-mode`.
- `opencode`: `unavailable`.
- `generic`: `unavailable`.

### wait

- `claude-code`: `SendMessage(to: "<name>", notify_when_idle: true)` with no message. Fallback: `python3 <ai-orchestrate-skill-root>/scripts/sessions.py wait <ids...>`, with the same `--all` and `--timeout` rules as [sub-sessions.md](sub-sessions.md). Never `<repo-root>/.claude/skills/...`.
- `codex`: the `codex exec` you started is the wait. Run it as a background shell and take the completion notice this session already uses for background shells. Do not poll. Do not call `sessions.py`. The report is `<run-dir>/jobs/<job-id>/last.md` for a read-only job, otherwise `report.md`.
- `cursor`: a foreground Task has already waited. For a background Task, read `~/.cursor/subagents/` for that agent id when this session gets a completion notice. Do not poll in a loop. Do not call `sessions.py`.
- `opencode`, `generic`: there is no handle to wait on.

### talk

- `claude-code`: `SendMessage` to the session name. `ListAgents` lists names, including this session's name for the brief. Delivery stays inside the permission-class rule in [sub-sessions.md](sub-sessions.md).
- `codex`: `codex exec resume <session-id> "<follow-up>"` only after that exec has exited. Do not resume a running exec. A question that blocks the worker is `BLOCKER / QUESTION` in `report.md`; the coordinator reads it after wait. Do not invent a live message tool.
- `cursor`: if the Task schema in this session has a resume field, pass the recorded agent id and the follow-up. If it has no resume field, a follow-up is a new Task with the brief plus the evidence, recorded as a new attempt. A blocking question is `BLOCKER / QUESTION` in `report.md`.
- `opencode`, `generic`: a blocking question is `BLOCKER / QUESTION` in the worker's final message. No live message tool.

### stop

- `claude-code`: `claude stop <id>` keeps the conversation; `claude rm <id>` deletes it. Worktree removal stays [sub-sessions.md](sub-sessions.md) Clean up.
- `codex`: stop the process you started. Do not run `claude stop`. Then `git worktree remove` as in that Clean up section.
- `cursor`: no CLI stop. After the Task has ended, `git worktree remove` a worktree this run created only when `git worktree list` shows it and it has no uncommitted changes. Never remove a worktree you cannot see in `git worktree list`.
- `opencode`, `generic`: nothing to stop.

### usage

Tokens only, never a price, same rule as ai-orchestrate section 7.

- `claude-code`: `sessions.py usage <id|name>` for a sub-session; the `Agent` result's usage for a subagent.
- `codex`: the `tokens used` line that command prints, otherwise `n/a`.
- `cursor`, `opencode`, `generic`: `n/a` unless the tool result includes a token count.

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
- `opencode`: `@ai-main`, `@ai-auto`, `@ai-orchestrate`
- `generic`: that host's own command for the skill name

`ai-main` is user-invoked. `disable-model-invocation: true` stays in its frontmatter. Codex reads `agents/openai.yaml` in that skill instead of that field.
```

### 2. Frontmatter và file Codex cho `ai-main`

Trong cả ba `SKILL.md`, thêm một dòng frontmatter ngay sau `argument-hint`, không đổi `name`, `description`, `argument-hint`, hay `disable-model-invocation`:

```yaml
compatibility: git required. Host profiles: Claude Code, Codex, Cursor, OpenCode; any other Agent Skills host runs the generic profile (direct and inline only). Claude background workers need the claude CLI and Python 3. Optional: gh, the ai CLI.
```

`description` giữ nguyên. Không kéo danh sách host vào `description` (giới hạn spec 1024 ký tự; field này đã đủ).

Tạo `skills/ai-main/agents/openai.yaml`, đúng hai dòng:

```yaml
policy:
  allow_implicit_invocation: false
```

Không thêm key khác. File này chỉ để Codex không tự gọi `ai-main`. Claude Code, Cursor, OpenCode bỏ qua file lạ trong thư mục skill.

### 3. Sửa ba `SKILL.md`: gọi operation, không gọi tool host

Giữ nguyên gate, bảng lane, luật `--resume`, luật brief/confirmed hash, luật Jev, luật R3, luật cleanup. Chỉ đổi cách gọi tool và câu project-rules.

`skills/ai-main/SKILL.md`:

- Câu project-rules đầu file đổi thành: `Bind the host in [hosts.md](../ai-orchestrate/references/hosts.md) before any ask, handoff, or spawn. Project rules, as that file defines them, win over this skill and every lane.`
- Mọi chỗ `Skill(skill: "ai-auto", args: "...")` và `Skill(skill: "ai-orchestrate", args: "...")` đổi thành `handoff` tới skill đó với cùng args (hosts.md). Args `--resume`, `--cross-runtime`, `--yes`, `--yagni` giữ nguyên nghĩa.
- `AskUserQuestion` đổi thành operation `ask`.
- Scout "subagent at tier `t1`" đổi thành `spawn-subagent` tier `t1` khi operation đó available; không available thì nhìn repo inline, không dispatch.

`skills/ai-auto/SKILL.md`:

- Câu project-rules đổi thành: `Bind the host in [hosts.md](../ai-orchestrate/references/hosts.md) before any ask or handoff. Project rules, as that file defines them, win over the brief.`
- `AskUserQuestion` đổi thành `ask`. Vẫn một câu một lúc, recommended answer first, option `preview` khi phải so hình.
- Cả hai chỗ `Skill(skill: "ai-orchestrate", args: ...)` đổi thành `handoff` với cùng args (`<run-dir>/brief.md` cộng `--cross-runtime` khi user truyền; `--resume <that dir>` khi Resume).
- Bullet Permission mode: thêm một câu `On claude-code, the permission-mode flag is the one in sub-sessions.md. On every other host, do not pass a Claude permission-mode flag; if this host prompts before edits or commands, say so now.`

`skills/ai-orchestrate/SKILL.md`:

- Câu project-rules đổi thành cùng dạng ai-main, link `references/hosts.md`.
- `Skill(skill: "ai-auto", ...)` đổi thành `handoff`.
- Bảng unit, hàng Workflow: cột Fits nói `claude-code only; other hosts use the Workflow unit section of hosts.md`. Không bảo host khác load `workflow-authoring`.
- Hàng sub-session: bỏ `` `claude --bg` `` khỏi tên unit. Cột Fits thành `One deliverable with its own branch/PR, long-running, resumable. Mechanics: hosts.md spawn-sub-session. Claude Code commands: sub-sessions.md.`
- Hàng subagent: `spawn-subagent` (hosts.md), không gọi tên `Agent` trong `SKILL.md`.
- Bullet Wait: `wait` trong hosts.md. Xóa đường dẫn `.claude/skills/ai-orchestrate/scripts/sessions.py`.
- Bullet Relay: `talk` trong hosts.md, không viết `SendMessage` trong `SKILL.md`.
- Section 7 tokens: `usage` trong hosts.md. Giữ câu Codex `tokens used` và câu `n/a`, vì hosts.md là nguồn lệnh.
- Resume: `sessions.py status` chỉ khi worker runtime là `claude-code`. Worker runtime khác dùng `wait`/`talk` của profile đó. Job `done` mà evidence còn thì giữ, như hiện tại.

Sau bước này, grep ba `SKILL.md` không còn `Skill(skill:`, `AskUserQuestion`, `SendMessage`, `ListAgents`, `claude --bg`, `.claude/skills/ai-orchestrate`.

### 4. Reference còn lại trỏ về profile, không nhân đôi lệnh

`skills/ai-orchestrate/references/sub-sessions.md`: dòng mở đầu thành `Claude Code worker runtime only. Other hosts use hosts.md and do not run the commands in this file.` Giữ nguyên mọi lệnh `claude` bên dưới. Hai chỗ `python3 <repo-root>/.claude/skills/ai-orchestrate/scripts/sessions.py` đổi thành `python3 <ai-orchestrate-skill-root>/scripts/sessions.py`. Probe worktree helper đổi thành thứ tự trong hosts.md (`.agents/skills/worktree/...` rồi `.claude/skills/worktree/...`), cùng điều kiện dry-run đang viết.

`skills/ai-orchestrate/references/recovery.md`:

- Rung 2: câu `claude --bg --resume ...` chỉ áp khi worker runtime là `claude-code`. Runtime khác nâng effort bằng `talk` hoặc, khi `talk` không resume được session đang sống, một attempt mới qua `spawn-sub-session` / `spawn-subagent`, ghi id mới vào `run.md`. Không chạy `claude --bg` trên runtime khác.
- Fallback 1: `talk` của profile, không viết `SendMessage` như lệnh chung. Câu `claude --bg --resume` giữ, gắn nhãn `claude-code` only.
- Fallback 4: `Agent` with `isolation: "worktree"` chỉ khi worker runtime là `claude-code`. Runtime khác mà `spawn-sub-session` bị từ chối bởi policy (không phải user từ chối một prompt) thì job `blocked` với class `permission_denied`, không đổi sang một tool Claude.

`skills/ai-orchestrate/references/worker-brief.md`:

- Câu `Subagent jobs put the same sections in the Agent prompt` đổi thành `spawn-subagent jobs put the same sections in that operation's prompt`.
- Dòng Rules `AGENTS.md, CLAUDE.md, .claude/rules/` đổi thành `the project rules hosts.md names`.
- Dòng cuối `SendMessage the coordinator` đổi thành `A question that blocks you: if your brief names a claude-code coordinator session, SendMessage that session and wait. Otherwise write it under BLOCKER / QUESTION and stop.` `SendMessage` trong file này chỉ còn trong câu điều kiện claude-code đó.

`skills/ai-orchestrate/references/models.md`:

- Dòng "Aliases for Claude Code units" giữ, thêm ngay sau bảng tier: `Pass haiku, sonnet, opus, or fable only when the worker runtime is claude-code. Every other runtime keeps the tier and the effort word and does not pass those aliases.`
- Rule 2 đổi thành: `Pin every dispatch through hosts.md. A claude-code worker passes model and effort explicitly. Any other runtime resolves the model in hosts.md. A contract pin that is one of those four aliases blocks the job unless the worker runtime is claude-code.`
- Rule 3, thêm sau câu đọc `ai model`: `On a host other than claude-code, still run ai model and record its output. Do not map that alias onto the host model. A t1–t4 contract cap still limits which job row you may pick, but you do not pass the alias. Effort on a Codex worker tops out at high unless the contract pins xhigh. There is no xhigh on any other non-Claude runtime; high is the top.`

`skills/ai-orchestrate/references/cross-runtime.md`: đoạn mở "Enter only with `--cross-runtime`" đổi thành hai luật:

- Orca, và Codex CLI khi host không phải `codex`: chỉ khi `--cross-runtime` hoặc request nêu tên runtime đó. Probe fail thì `blocked`, không đổi route im lặng. Giữ nguyên.
- Host là `codex`: block lệnh Codex CLI trong file này là `spawn-sub-session` của host, không cần `--cross-runtime`.

Không sửa lệnh `codex exec`, effort `minimal|low|medium|high|xhigh`, hay đoạn Orca. Không thêm `spawn_agent`.

`skills/ai-orchestrate/scripts/sessions.py`: không đổi hành vi. Dòng docstring đầu đổi thành `Inspect and wait on Claude Code background sessions. Only the claude-code worker runtime calls this.`

### 5. Installer và README: cài được sang host kia, mặc định Claude không đổi

`bin/lib.mjs` `HELP`: trong ví dụ pass-through, đổi `-a claude-code` thành `-a claude-code, -a codex, -a cursor, -a opencode`. Không thêm flag. Không đổi default. User không truyền `-a` thì `npx skills add` vẫn tự hỏi, như hiện tại. Không dùng `--agent '*'`.

`README.md`, sửa đúng các chỗ sau, không viết lại cả file:

- Câu mở: `Skills for Claude Code, Codex, Cursor, and OpenCode that take a task from request to verified result.` Phần sau của câu (`ai-main` routes…) giữ.
- Thêm một khối lệnh ngay sau khối install Claude, không xóa khối cũ:

```bash
# Claude Code, Codex, Cursor, and OpenCode, global
npx -y github:0x01001/ai-skill -a claude-code -a codex -a cursor -a opencode -y -g
```

- Usage: thêm bảng invocation đúng như mục Invocation trong hosts.md. Flags giữ nguyên.
- Requirements, bullet Claude Code đổi thành: một host trong hosts.md. `claude --bg` và `python3` chỉ bắt buộc khi worker runtime là `claude-code`. `git` vẫn bắt buộc. Codex hoặc Orca vẫn optional cho `--cross-runtime`.
- Layout: thêm `references/hosts.md` và `ai-main/agents/openai.yaml`.

`.gitignore` đã có `.agents/skills/` và `.claude/skills/`. Không thêm dòng.

### 6. Test tĩnh

`package.json` script `test` đổi thành `node --test test/install.test.mjs test/skills-host.test.mjs`. Không dùng glob: Windows không nở `test/*.test.mjs` trong npm script.

`test/install.test.mjs` dòng comment đầu cập nhật thành lệnh đó. Không đổi assertion installer. Test help cấm chuỗi `--list` trong help; ví dụ agent mới không được chứa `--list`.

Tạo `test/skills-host.test.mjs`. `ROOT` giống install test: `resolve(dirname(fileURLToPath(import.meta.url)), "..")`. Đọc file bằng `node:fs`. Fail khi:

- `skills/ai-main/SKILL.md`, `skills/ai-auto/SKILL.md`, `skills/ai-orchestrate/SKILL.md` thiếu `compatibility:` hoặc giá trị đó dài hơn 500, hoặc `name:` không khớp tên thư mục.
- Ba file đó chứa một trong: `Skill(skill:`, `AskUserQuestion`, `SendMessage`, `ListAgents`, `claude --bg`, `.claude/skills/ai-orchestrate`.
- `skills/ai-orchestrate/references/hosts.md` thiếu một trong các heading operation `### ask`, `### handoff`, `### spawn-subagent`, `### spawn-sub-session`, `### wait`, `### talk`, `### stop`, `### usage`, hoặc thiếu id `` `claude-code` ``, `` `opencode` ``, `` `cursor` ``, `` `codex` ``, `` `generic` ``.
- `skills/ai-main/agents/openai.yaml` không đúng hai dòng `policy:` và `  allow_implicit_invocation: false`.
- `skills/ai-orchestrate/references/sub-sessions.md` còn chuỗi `.claude/skills/ai-orchestrate`.
- `package.json` `scripts.test` không bằng lệnh ở trên.

Không gọi mạng, không gọi `claude` hay `codex`.

## Critical files & anchors

- `skills/ai-orchestrate/SKILL.md` bảng unit (khoảng dòng 46–52) và bullet Wait (khoảng dòng 66): chỗ duy nhất `SKILL.md` còn lệnh Claude sau khi đọc sơ. Phải rời hết sang hosts.md.
- `skills/ai-orchestrate/references/sub-sessions.md` Launch và Watch: nguồn lệnh `claude --bg` / `sessions.py`. Giữ lệnh, đổi path script và câu mở.
- `skills/ai-orchestrate/references/cross-runtime.md` block `codex exec`: nguồn duy nhất của worker Codex. Host `codex` dùng lại, không copy.
- `skills/ai-main/SKILL.md` các lời gọi `Skill(skill:)`: handoff giữa ba skill. Args không được đổi nghĩa.
- `bin/lib.mjs` `HELP` và `test/install.test.mjs` test `--help`: help không được chứa `--list`.

## Verification

Từ `D:/project/tool/ai-skill`:

```bash
npm test
```

Kỳ vọng: mọi test trong `test/install.test.mjs` vẫn pass, và `test/skills-host.test.mjs` pass. Không cần `claude`, `codex`, mạng, hay key.

Đọc lại `skills/ai-main/SKILL.md`: có link `hosts.md`, không có `Skill(skill:`. Đó là bằng chứng lane handoff không còn khóa Claude trong file entry.

Không chạy skill trên Cursor/Codex thật trong change này. Profile Cursor chỉ được phép set field có trong schema tool của session; test không giả lập schema đó.

## Assumptions & contingencies

- "Provider" ở đây là coding-agent host đang đọc skill, không phải provider Jev. Nếu người dùng muốn map model Claude sang model Cursor/Codex, không làm trong change này: job có pin `haiku|sonnet|opus|fable` trên worker runtime khác là `blocked`.
- `argument-hint` và `disable-model-invocation` giữ trong frontmatter. Codex bỏ qua field lạ (parser chỉ đọc `name`, `description`, `metadata`). Nếu một host từ chối cả file vì field lạ, không xóa field: Claude Code cần chúng. Ghi host đó là không load được và dừng, không tạo bản SKILL.md thứ hai.
- OpenCode có thể không có tool subagent. Khi tool list không có, `spawn-subagent` và `spawn-sub-session` là `unavailable`. Không đặt tên tool OpenCode ngoài `skill`.
- Cursor Task không có schema cố định trong repo. Chỉ set field mà schema session đang hiện. Không có field resume thì follow-up là attempt mới, không đoán tên field.
- `spawn_agent` của Codex không dùng. Isolated worker Codex là `codex exec` đã có trong cross-runtime.md.
- Installer không đổi agent mặc định. Ví dụ README đa host là lệnh user tự chạy, không phải default của `bin/install.mjs`.
