# Codex Loop

Codex Loop is a plugin-shaped implementation of recurring prompt scheduling for Codex. It mirrors the useful parts of Claude Code `/loop` with a Codex-native split:

- `skills/loop`: user-facing `$loop` and `/prompts:loop` semantics
- MCP server: durable task CRUD and iteration completion tools
- SQLite store: task state, leases, expiry, no-catch-up scheduling
- `codex-loopd`: scheduler daemon, autostarted by `$loop` task creation when a runnable app-server is configured
- runners: `app-server`, `codex-mcp`, `exec`, and `dry-run`
- PostToolUse hook binding plus Stop hook fallback: bind tasks to the current session when Codex exposes a hook `session_id`, and continue already-due jobs without opening a new session

## Quick Start

Most users do not need to clone this repository.

Add the marketplace:

```bash
codex plugin marketplace add Owen718/codex-loop-plugin
```

Restart Codex, then install or enable:

```text
Marketplace/source: Codex Loop Plugin
Plugin: Codex Loop
Action: Install or Enable
```

Create a loop task inside Codex:

```text
$loop 5m check deploy status
```

List or cancel tasks:

```text
$loop list
$loop cancel <job_id>
```

## Codex Entrypoints

The plugin gives you `$loop` after it is installed and enabled:

```text
$loop 5m check deploy
$loop list
$loop cancel <job_id>
```

If you also want `/prompts:loop`, copy the prompt template from the installed plugin:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
mkdir -p ~/.codex/prompts
cp "$LOOP_PLUGIN/prompts/loop.md" ~/.codex/prompts/loop.md
```

Then use:

```text
/prompts:loop 5m check deploy
```

## Runners

By default, new tasks use `visibility_policy=visible_only` and `runner=app-server`. This is intentionally conservative: if Codex Loop cannot bind the task to a concrete Codex session/thread id, or if no app-server runtime is configured, the task pauses instead of starting a hidden new Codex session.

Start Codex through the bundled launcher when you want Claude Code style visible scheduled turns:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loop" tui --cwd "$PWD"
```

The launcher starts a local `codex app-server`, starts `codex-loopd` against that same app-server, then opens `codex --remote` with environment variables that bind new `$loop` tasks to `visible_only` app-server execution.

You can also wire the runtime manually:

```bash
codex app-server --listen ws://127.0.0.1:4500 --ws-auth capability-token --ws-token-file ~/.codex-loop/ws-token
export CODEX_LOOP_APP_SERVER=ws://127.0.0.1:4500
export CODEX_WS_TOKEN="$(cat ~/.codex-loop/ws-token)"
codex --remote "$CODEX_LOOP_APP_SERVER" --remote-auth-token-env CODEX_WS_TOKEN
```

When `CODEX_LOOP_APP_SERVER` is set, `$loop` autostarts `codex-loopd` with the `app-server` runner and writes `~/.codex-loop/loopd.pid` plus `~/.codex-loop/loopd.log`. Set `CODEX_LOOP_AUTOSTART=0` before launching Codex to disable autostart.

`exec` is the simplest and most stable runner. It starts non-interactive Codex turns:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loopd" --runner exec
```

`codex-mcp` drives `codex mcp-server` and uses `codex` / `codex-reply` when exposed:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loopd" --runner codex-mcp
```

Use `codex-mcp` or `exec` only for tasks created with `visibility_policy=thread_only` or `visibility_policy=background_ok`. `visible_only` tasks refuse these runners because they cannot guarantee that the current TUI session will see the scheduled turn.

`app-server` is the current visible-session runner. Start Codex app-server and remote TUI first:

```bash
codex app-server --listen ws://127.0.0.1:4500 --ws-auth capability-token --ws-token-file ~/.codex-loop/ws-token
export CODEX_WS_TOKEN="$(cat ~/.codex-loop/ws-token)"
codex --remote ws://127.0.0.1:4500 --remote-auth-token-env CODEX_WS_TOKEN
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loopd" --runner app-server --app-server ws://127.0.0.1:4500
```

The app-server runner needs Python's optional `websockets` package.

## Default Prompt

Bare loops resolve the maintenance prompt in this order:

1. `.codex/loop.md` in the current tree or nearest Git root
2. `~/.codex/loop.md`
3. built-in conservative maintenance prompt

## Safety

Tasks snapshot cwd, approval policy, sandbox, and model. Later iterations do not auto-upgrade them. Tasks expire after seven days, use deterministic jitter for fixed intervals, do not catch up missed ticks, and pause after repeated runner failures. Every acquired iteration gets a durable `run_id`; `loop_complete_iteration` is idempotent for that run so model-side completion and scheduler fallback cannot double-increment `run_count`.
