# Sub-sessions: launch, watch, talk, clean up

A sub-session is a real Claude Code session running in the background with its own context, model, worktree and lifecycle; the user can open it with `claude attach <id>`. The commands below were verified on Claude Code 2.1.318 on Windows; launch, resume, stop and rm again on 2.1.322. When a flag fails, `claude --help` and `claude agents --help` are the source of truth.

## Launch

1. **Worktree from the job's start point.** Read-only jobs, the arbiter included, get a detached worktree instead, never the main checkout: `git worktree add --detach <path> <commit>`, with `<commit>` the commit they read (the start point; for the arbiter, the integrated result's HEAD). Record `<commit>` in `run.md`; like a writer, the job sees only committed state.
   - **Start point:** the contract's base branch; when Delivery pushes, `origin/<base>` after `git fetch origin <base>`, so local commits not on the remote never ride along into a PR. A writer whose deps include writer jobs starts from their commits, never from the bare base: from the dependency's branch when it has a single writer dependency, otherwise from the base with every dependency branch merged in right after creating the worktree (`git -C <worktreePath> merge --no-ff <dep-branch>...`; a conflict is a `merge_conflict`, see [recovery.md](recovery.md)). Its Delivery says so: a PR stacked on that dependency branch, or a PR against the base that lists the dependency PRs it contains.
   - Create it with `git worktree add -b <prefix>/<slug> <path> <start-point>`.
   - When the repo has `.claude/skills/worktree/scripts/worktree.cjs`, that helper may create it instead: it copies `.env*.example` to `.env*` (placeholder values: real build-time values must come from the process environment) and prints `worktreePath`, but always branches from the first of `dev`, `develop`, `main`, `master` that exists, takes no base override, and checks out an already existing branch as it is. Run it with `--dry-run` first and use it only when `wouldCreate.baseBranch` is the start point's branch and `wouldCreate.branchExists` is false:
     `node <repo-root>/.claude/skills/worktree/scripts/worktree.cjs create "<slug>" --prefix <feat|fix|refactor|docs|test|chore|perf> --json`
   - Right after creating it, check that `git -C <worktreePath> rev-parse HEAD` equals `git rev-parse <start-point>`; if not, remove that worktree and use the `git worktree add` line with a fresh slug.
   - Record the start commit (`git -C <worktreePath> rev-parse HEAD`, after any dependency merge and before the worker runs) in `run.md`: it is the job's `<base>` for diff checks.
   - `claude -w` bases its worktree on `origin/HEAD` (the default branch), not your current branch; use it only when that is the intended base.
   - A worktree starts from committed `HEAD`: uncommitted changes in the main checkout are invisible to the worker. Never commit the user's changes yourself: ask, or state in the brief what the worker will not see.
2. **Brief** at `<run-dir>/jobs/<job-id>/brief.md` ([worker-brief.md](worker-brief.md)).
3. **Launch from the worktree**, prompt last:

   ```bash
   cd "<worktreePath>" && claude --bg --add-dir "<run-dir>" -n "<run>-<job-id>-a<n>" \
     --model <alias> --effort <level> --permission-mode <mode> \
     "Read <run-dir>/jobs/<job-id>/brief.md and carry it out; it is your whole task."
   ```

   - `<run-dir>` is the absolute path from `run.md`: a relative one would resolve inside the worktree, which has no `ai/running/`. `<n>` is the attempt number, so a re-dispatch never reuses a name.
   - `--add-dir` is variadic: it swallows every following bare argument, the prompt included, and the session then starts idle with no task. Keep another flag between `--add-dir` and the prompt.
   - Output: `backgrounded · <id> · <name>`. Record id, name, `sessionId` (from `sessions.py status <name>`), worktree and branch in `run.md`.
   - **Permission mode:** writers run in your own permission mode. Messages and idle notices flow automatically only between sessions of the same permission class; a session in another class holds them for a human to approve and shows `status: waiting, state: blocked`. Read-only jobs, the arbiter included, run in the same mode: the brief keeps them read-only, and their detached worktree proves it: after they settle, `git -C <worktreePath> status --porcelain` prints nothing and `git -C <worktreePath> rev-parse HEAD` is still `<commit>`; anything else is a `verification_failure`. A `plan` session cannot write its `report.md` and its idle notice may be held; if one runs in `plan` anyway, wait on it with `sessions.py wait` and read it with `sessions.py last`.
   - `<alias>` and `<level>` are the job's tier and effort resolved in [models.md](models.md).

## Watch

- **Idle notice (preferred):** `SendMessage(to: "<name>", notify_when_idle: true)` with no message. One notice arrives when the session next goes idle or exits. Re-subscribe after each notice while the job is open.
- **Fallback:** `python3 <repo-root>/.claude/skills/ai-orchestrate/scripts/sessions.py wait <id|name...> [--all] [--timeout 3600]`, run in the background; it returns once any (or every) listed session leaves `state: working`. List only jobs still open: without `--all`, one that already settled returns at once. A name matches its newest session; it exits 1 when it timed out on a failing `claude agents` call.
- `sessions.py status [id|name...]` shows `status`/`state`: `idle`/`done` finished its turn, `waiting`/`blocked` needs approval, `working` busy.
- `sessions.py last <id|name>` prints the session's last assistant message from its transcript: the fallback when `report.md` is missing.
- `claude logs <id>` is raw terminal output; use it to see what a blocked session is showing (a permission prompt, a held message).

## Talk

- **Live session:** `SendMessage` to its name (`ListAgents` lists names). Delivery is direct only within the same permission class (see Launch).
- **Worker to coordinator:** put your own session name (from `ListAgents`, "This session is ...") in the brief so a worker can message you.
- **Stopped session:** `claude --bg --resume <sessionId> "<follow-up>"` continues it under the same id with its saved options. Any added flag (`--effort`, `-n`) starts a copy that keeps the conversation under a new id instead. Only on a stopped one (`claude stop <id>` first if needed): on a running session the CLI starts a copy in the same worktree.
- Never ask a worker to do something your own session was denied: that launders a permission decision. Take it to the user.

## Clean up

- `claude stop <id>` stops a session and keeps its conversation; `claude rm <id>` deletes it, plus any worktree `claude -w` created for it.
- Worktrees you created: once its session and every process running inside it have stopped (on Windows they lock its files), `git worktree remove <absolute path>` without `--force`, whether or not the branch was pushed. The branch and its commits stay; only the directory goes, with its ignored files (`.env`, `target/`, `node_modules`). Git refuses a worktree with uncommitted changes or untracked files: keep it and list it in the final report. When the repo has the `worktree.cjs` helper, not its `remove`: it forces and matches names by prefix, so uncommitted work is lost and `job-1` can hit `job-10`.
- Branches: delete a local branch only once `git branch --merged <target>` lists it (for a PR merged on GitHub, `git fetch origin <target>` first and use `origin/<target>`), with `git branch -d <branch>`, never `-D`. A squash-merged PR never shows up as merged, so its branch stays. Never delete a remote branch.
