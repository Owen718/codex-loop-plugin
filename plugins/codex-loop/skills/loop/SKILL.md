---
name: "loop"
description: "Create, inspect, pause, resume, cancel, and complete recurring Codex Loop tasks using the codex_loop MCP tools. Use when the user invokes $loop, /prompts:loop, or asks Codex to repeatedly run a prompt on an interval."
---

# Codex Loop

This skill is the user-facing entrypoint for the Codex Loop plugin. It parses the user intent, calls the `codex_loop` MCP tools, and relies on `codex-loopd` or the optional Stop hook fallback to trigger future iterations. New tasks default to `visibility_policy=visible_only` and `runner=app-server`: if the task cannot bind to a concrete current Codex session/thread id, or no app-server runtime is configured, it pauses instead of starting a hidden new session. The MCP tool starts `codex-loopd` automatically when the selected runner is runnable unless `CODEX_LOOP_AUTOSTART=0`.

## Supported user forms

- `$loop 5m check deploy`
- `$loop check deploy`
- `$loop 15m`
- `$loop`
- `$loop list`
- `$loop cancel <job_id>`
- `$loop pause <job_id>`
- `$loop resume <job_id>`
- `/prompts:loop 5m check deploy`

For Claude Code style visible loop turns, launch Codex through the repository script first:

- `codex-loop tui --cwd <repo>`

This starts a local app-server runtime, starts `codex-loopd` against it, and opens `codex --remote` with the `CODEX_LOOP_APP_SERVER` environment needed by `visible_only` loop tasks.

## Behavior

1. If the request is create-like, call `mcp__codex_loop__loop_create` with:
   - `raw_user_input`: the raw arguments after `$loop` or `/prompts:loop`
   - `cwd`: current working directory
   - `thread_id`: the current Codex thread id. Prefer the `CODEX_THREAD_ID` environment variable. If it is not already visible, run `printf '%s\n' "$CODEX_THREAD_ID"` and pass the non-empty value.
   - `approval_policy`, `sandbox`, `model`: current snapshots if known; omit unknown values
2. If the user asks for list/status, call `mcp__codex_loop__loop_list`.
3. If the user asks to cancel/delete/rm, call `mcp__codex_loop__loop_delete` with `job_id`.
4. If the user asks pause/resume, call `mcp__codex_loop__loop_update`.
5. After a scheduled iteration prompt explicitly asks you to complete the iteration, call `mcp__codex_loop__loop_complete_iteration`.
   - Include both `job_id` and `run_id` when the prompt provides them. Completion is idempotent per `run_id`.

## Defaults

Bare `$loop` and interval-only forms use the default maintenance prompt resolved in this order:

1. `<repo-or-current-tree>/.codex/loop.md`
2. `~/.codex/loop.md`
3. Built-in conservative maintenance prompt

## Safety Rules

- Do not create unrelated feature work from a bare maintenance loop.
- Do not upgrade approvals, sandbox, model, or cwd after task creation unless the user explicitly asks.
- Do not downgrade a visible loop to `thread_only` or `background_ok` unless the user explicitly asks. `visible_only` is the safe default because it refuses hidden new sessions.
- For dynamic loops, choose `next_delay_seconds` from 60 to 3600 based on observed urgency.
- If blocked, pause the task or choose a longer delay.
- If a slash command inside a loop cannot be safely expanded outside the TUI, explain that an adapter is needed and pause.

## User Response Style

When a task is created, report only the useful task fields: job id, schedule kind, next run time, prompt kind, daemon status, and how to cancel it. If the response includes a warning about a missing concrete thread id, surface that warning.
