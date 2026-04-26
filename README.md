# Codex Loop Plugin

[![test](https://github.com/Owen718/codex-loop-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/Owen718/codex-loop-plugin/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Codex Plugin](https://img.shields.io/badge/Codex-plugin-2563EB.svg)](plugins/codex-loop)

Claude Code-style recurring loop scheduling for OpenAI Codex.

Codex Loop adds a practical `/loop` equivalent to Codex by combining a Codex skill, MCP tools, durable SQLite state, a scheduler daemon, and an app-server based launcher. The default mode is now conservative and visible-session oriented: loop turns are scheduled through a shared `codex app-server` runtime, and tasks pause instead of silently opening a hidden new Codex session.

```text
$loop 5m check deploy
$loop list
$loop cancel a1b2c3d4
```

## Why this exists

Codex skills and MCP servers are useful, but neither is a scheduler by itself. A skill can describe a workflow; an MCP server can expose tools and state; neither can reliably wake an idle Codex session on a timer.

Codex Loop separates those concerns:

```text
Codex skill / custom prompt
  -> MCP task tools
  -> SQLite task store
  -> codex-loopd scheduler
  -> Codex runner
```

That architecture gives you a close equivalent of Claude Code scheduled tasks without pretending that a skill or MCP server alone can own the clock.

## Features

| Feature | Status |
| --- | --- |
| Fixed interval loops, e.g. `5m check deploy` | Supported |
| Dynamic loops, e.g. `check deploy` chooses 60..3600s after each run | Supported |
| Bare maintenance loop with default prompt | Supported |
| `list`, `cancel`, `pause`, `resume` management | Supported |
| Durable SQLite state | Supported |
| No catch-up for missed ticks | Supported |
| 7-day task expiry | Supported |
| Deterministic jitter for fixed loops | Supported |
| Stop hook fallback | Supported |
| Visible app-server launcher, `codex-loop tui` | Supported |
| Scheduler daemon autostart for visible runtime | Supported |
| `codex exec` runner | Supported |
| `codex mcp-server` runner | Supported |
| `codex app-server` runner | Experimental |

## Repository Layout

```text
.agents/plugins/marketplace.json      Codex marketplace entry
plugins/codex-loop/                   Plugin package
  .codex-plugin/plugin.json           Plugin manifest
  .mcp.json                           MCP server config
  skills/loop/SKILL.md                $loop skill instructions
  prompts/loop.md                     /prompts:loop template
  scripts/codex-loop                  CLI, including `codex-loop tui`
  scripts/codex-loopd                 Scheduler daemon
  scripts/codex-loop-mcp              MCP server entrypoint
  scripts/codex_loop/                 Runtime implementation
  hooks/stop_loop_fallback.py         Stop hook fallback
  tests/                              Unit tests
```

## Quick Start

Most users do not need to clone this repository.

Add this repository as a Codex plugin marketplace:

```bash
codex plugin marketplace add Owen718/codex-loop-plugin
```

Restart Codex. In the plugin directory UI, install or enable the plugin:

```text
Marketplace/source: Codex Loop Plugin
Plugin: Codex Loop
Action: Install or Enable
```

Then start Codex through the Codex Loop launcher. This is the recommended startup path for the current version:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loop" tui --cwd "$PWD"
```

That command starts a local `codex app-server`, starts `codex-loopd` against the same app-server, then opens `codex --remote`. Create loops inside the TUI opened by this command:

```text
$loop 5m check git status and summarize
$loop list
$loop cancel <job_id>
```

What each step means:

- `marketplace add` registers this GitHub repository as a plugin source.
- Installing/enabling `Codex Loop` loads the `$loop` skill and `codex_loop` MCP tools into Codex.
- The launcher exports `CODEX_LOOP_APP_SERVER`, `CODEX_LOOP_APP_SERVER_TOKEN_FILE`, `CODEX_LOOP_RUNNER=app-server`, and `CODEX_LOOP_VISIBILITY_POLICY=visible_only` into the new Codex TUI.
- The launcher stores each runtime's DB, pid files, logs, and websocket token under `~/.codex-loop/runtimes/<host-port>/`, so new code does not reuse an old global `~/.codex-loop/loop.sqlite3` schema.
- The launcher also writes `~/.codex-loop/active-runtime.json`; the MCP server reads this file if Codex does not inherit the TUI environment into MCP subprocesses.
- The MCP create tool starts `codex-loopd` against that app-server and reports daemon status in the tool response.
- If you create a loop in a normal Codex TUI without this runtime, the default `visible_only` task pauses instead of opening a hidden new session.

## Installation Details

### 1. Add The Marketplace

```bash
codex plugin marketplace add Owen718/codex-loop-plugin
```

### 2. Install The Plugin In Codex

Restart Codex, open the plugin directory UI, select `Codex Loop Plugin`, then install or enable `Codex Loop`.

After this, `$loop` is available inside Codex.

### 3. Launch The Visible Runtime

Use the installed plugin script to launch the Codex TUI:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loop" tui --cwd "$PWD"
```

The launcher creates a runtime under `~/.codex-loop/runtimes/<host-port>/`, starts a local app-server, starts `codex-loopd --runner app-server`, then opens a remote Codex TUI attached to that runtime.

Pass Codex CLI options after `--`:

```bash
"$LOOP_PLUGIN/scripts/codex-loop" tui --cwd "$PWD" -- --model gpt-5.2
```

When `$loop` creates a task through the MCP tool in this launched TUI, it is created as `visibility_policy=visible_only` and `runner=app-server`. The task DB, daemon pid, daemon log, app-server pid, app-server log, and websocket token are written under the launcher runtime directory.

Set `CODEX_LOOP_AUTOSTART=0` before launching Codex if you prefer to manage the daemon yourself.

### Optional: enable `/prompts:loop`

The plugin skill gives you `$loop`. If you also want `/prompts:loop`, copy the prompt template from the installed plugin:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
mkdir -p ~/.codex/prompts
cp "$LOOP_PLUGIN/prompts/loop.md" ~/.codex/prompts/loop.md
```

Then use:

```text
/prompts:loop 5m check deploy
```

## Startup And Runner Modes

The plugin creates and manages loop tasks. For current-session visible behavior, use `codex-loop tui`. You can still run lower-level pieces manually if you want a different service manager.

### Recommended: `codex-loop tui`

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loop" tui --cwd "$PWD"
```

Useful options:

```bash
"$LOOP_PLUGIN/scripts/codex-loop" tui --help
"$LOOP_PLUGIN/scripts/codex-loop" tui --cwd "$PWD" --port 4500
"$LOOP_PLUGIN/scripts/codex-loop" tui --cwd "$PWD" -- --model gpt-5.2
```

### Manual app-server runtime

You can wire the same runtime manually:

```bash
mkdir -p ~/.codex-loop
codex app-server \
  --listen ws://127.0.0.1:4500 \
  --ws-auth capability-token \
  --ws-token-file ~/.codex-loop/ws-token
```

In another terminal:

```bash
export CODEX_LOOP_APP_SERVER=ws://127.0.0.1:4500
export CODEX_LOOP_APP_SERVER_TOKEN_ENV=CODEX_WS_TOKEN
export CODEX_LOOP_APP_SERVER_TOKEN_FILE=~/.codex-loop/ws-token
export CODEX_LOOP_RUNNER=app-server
export CODEX_LOOP_VISIBILITY_POLICY=visible_only
export CODEX_LOOP_DB=~/.codex-loop/runtimes/127-0-0-1-4500/loop.sqlite3
export CODEX_WS_TOKEN="$(cat ~/.codex-loop/ws-token)"
codex --remote "$CODEX_LOOP_APP_SERVER" --remote-auth-token-env CODEX_WS_TOKEN
```

Then start the daemon:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loopd" \
  --runner app-server \
  --app-server "$CODEX_LOOP_APP_SERVER" \
  --app-server-token-env CODEX_WS_TOKEN \
  --app-server-token-file "$CODEX_LOOP_APP_SERVER_TOKEN_FILE" \
  --db "$CODEX_LOOP_DB"
```

The app-server runner requires Python's optional `websockets` package:

```bash
python3 -m pip install websockets
```

### Background runners

The simplest runner is `exec`:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loopd" --runner exec
```

This uses `codex exec` for each due task. It is stable and easy to operate, but it behaves like background automation rather than a live TUI session.

Use `exec` or `codex-mcp` only for tasks explicitly created as `thread_only` or `background_ok`. Default `visible_only` tasks refuse these runners because they cannot guarantee that the current TUI sees the scheduled turn.

## Usage

Create a fixed loop:

```text
$loop 5m check deploy
```

The minimum interval is 60 seconds, so `20s` and `30s` are normalized to `60s`.

Create a dynamic loop:

```text
$loop watch CI and review comments
```

Use the default maintenance prompt:

```text
$loop
$loop 15m
```

Manage tasks:

```text
$loop list
$loop pause a1b2c3d4
$loop resume a1b2c3d4
$loop cancel a1b2c3d4
```

You can also use the CLI directly:

```bash
LOOP_PLUGIN="$(find ~/.codex/plugins/cache/codex-loop-plugin/codex-loop -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
"$LOOP_PLUGIN/scripts/codex-loop" create 5m "check deploy"
"$LOOP_PLUGIN/scripts/codex-loop" list
"$LOOP_PLUGIN/scripts/codex-loop" cancel a1b2c3d4
```

Direct CLI-created tasks use the same defaults as `$loop`: `visible_only` and `app-server`. They need `CODEX_LOOP_APP_SERVER` to run, unless you explicitly create a `thread_only` or `background_ok` task for a background runner.

## Loop Semantics

| Form | Meaning |
| --- | --- |
| `$loop 5m check deploy` | Fixed 5-minute interval |
| `$loop check deploy` | Dynamic interval, chosen after each run |
| `$loop 15m` | Fixed 15-minute maintenance loop |
| `$loop` | Dynamic maintenance loop |
| `$loop 20m /review` | Fixed interval command-style prompt with adapter support |

Default maintenance prompts are resolved in this order:

1. `.codex/loop.md` in the current project tree
2. `~/.codex/loop.md`
3. built-in conservative maintenance prompt

Dynamic loops ask Codex to choose the next delay between 60 and 3600 seconds. If Codex does not call `loop_complete_iteration`, the scheduler falls back to 10 minutes.

Fixed loops use deterministic jitter:

```text
next_run_at = now + interval + min(interval * 10%, 15 minutes) * stable_fraction(job_id)
```

## Safety Model

Codex Loop is deliberately conservative.

- Tasks snapshot `cwd`, approval policy, sandbox, and model at creation time.
- Later iterations do not auto-upgrade permissions.
- A task expires after seven days.
- Missed ticks do not catch up; one due task run is scheduled when the daemon comes back.
- Running tasks are leased so two daemon processes do not execute the same task concurrently.
- Every acquired iteration gets a durable `run_id`; completion is idempotent for that run.
- Default `visible_only` tasks require a concrete session/thread binding and an app-server runner.
- Running task cancellation sets `cancel_requested`; it stops after the current iteration completes.
- Repeated failures pause the task.
- The built-in maintenance prompt tells Codex not to start unrelated work or perform irreversible actions without explicit authorization.

## MCP Tools

The plugin exposes these MCP tools:

| Tool | Purpose |
| --- | --- |
| `loop_create` | Create a loop task from raw `/loop`-style arguments |
| `loop_list` | List active or historical tasks |
| `loop_delete` | Cancel a task |
| `loop_update` | Pause, resume, fail, or mark done |
| `loop_bind_session` | Bind a pending task to a concrete Codex session/thread id |
| `loop_complete_iteration` | Complete a run and schedule the next one |
| `loop_read_default_prompt` | Resolve the maintenance prompt |

## Development

Clone the repository only if you want to hack on the plugin:

```bash
git clone git@github.com:Owen718/codex-loop-plugin.git
cd codex-loop-plugin
```

Run tests:

```bash
make test
```

Validate JSON config:

```bash
make json-check
```

Run a local smoke test:

```bash
make smoke
```

The implementation uses only the Python standard library for the `exec`, MCP, and launcher paths. The app-server runner needs `websockets`.

## Limitations

- Bare `/loop` is not a native Codex slash command. Use `$loop` from the skill or `/prompts:loop` via the custom prompt template.
- The Stop hook fallback can only continue when a task is already due at turn stop time. It is not the primary scheduler.
- The app-server runner is the default visible-session path, but Codex app-server WebSocket support is still experimental.
- `codex-loop tui` currently uses loopd as a second app-server client. If a Codex TUI build does not render turns started by another app-server client, a proxy/multiplexer layer is still needed.
- Arbitrary TUI slash commands inside loop prompts need adapters; `/prompts:name` and `/review` are handled.

## License

MIT. See [LICENSE](LICENSE).
