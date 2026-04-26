# Codex Loop Plugin

[![test](https://github.com/Owen718/codex-loop-plugin/actions/workflows/test.yml/badge.svg)](https://github.com/Owen718/codex-loop-plugin/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Codex Plugin](https://img.shields.io/badge/Codex-plugin-2563EB.svg)](plugins/codex-loop)

Claude Code-style recurring loop scheduling for OpenAI Codex.

Codex Loop adds a practical `/loop` equivalent to Codex by combining a Codex skill, MCP tools, durable SQLite state, and an external scheduler daemon. It supports fixed intervals, dynamic intervals, task management, default maintenance prompts, and multiple runner modes for different levels of Codex integration.

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
  scripts/codex-loop                  CLI
  scripts/codex-loopd                 Scheduler daemon
  scripts/codex-loop-mcp              MCP server entrypoint
  scripts/codex_loop/                 Runtime implementation
  hooks/stop_loop_fallback.py         Stop hook fallback
  tests/                              Unit tests
```

## Installation

### Option 1: Install as a Codex marketplace

Add this repository as a plugin marketplace:

```bash
codex plugin marketplace add Owen718/codex-loop-plugin
```

Restart Codex, open the plugin directory, choose `Codex Loop Plugin`, and install `Codex Loop`.

### Option 2: Local development install

Clone the repository:

```bash
git clone git@github.com:Owen718/codex-loop-plugin.git
cd codex-loop-plugin
```

Add the local marketplace:

```bash
codex plugin marketplace add "$PWD"
```

Restart Codex and install `Codex Loop` from the local marketplace.

### Optional: enable `/prompts:loop`

The plugin skill gives you `$loop`. If you also want `/prompts:loop`, copy the prompt template:

```bash
mkdir -p ~/.codex/prompts
cp plugins/codex-loop/prompts/loop.md ~/.codex/prompts/loop.md
```

Then use:

```text
/prompts:loop 5m check deploy
```

## Running the Scheduler

The scheduled tasks only run while `codex-loopd` is running.

The simplest runner is `exec`:

```bash
plugins/codex-loop/scripts/codex-loopd --runner exec
```

This uses `codex exec` for each due task. It is stable and easy to operate, but it behaves like background automation rather than a live TUI session.

For the closest interactive behavior, use the app-server runner:

```bash
mkdir -p ~/.codex-loop
codex app-server \
  --listen ws://127.0.0.1:4500 \
  --ws-auth capability-token \
  --ws-token-file ~/.codex-loop/ws-token
```

In another terminal:

```bash
export CODEX_WS_TOKEN="$(cat ~/.codex-loop/ws-token)"
codex --remote ws://127.0.0.1:4500 --remote-auth-token-env CODEX_WS_TOKEN
```

Then start the daemon:

```bash
plugins/codex-loop/scripts/codex-loopd \
  --runner app-server \
  --app-server ws://127.0.0.1:4500
```

The app-server runner requires Python's optional `websockets` package:

```bash
python3 -m pip install websockets
```

## Usage

Create a fixed loop:

```text
$loop 5m check deploy
```

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
plugins/codex-loop/scripts/codex-loop create 5m "check deploy"
plugins/codex-loop/scripts/codex-loop list
plugins/codex-loop/scripts/codex-loop cancel a1b2c3d4
```

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
| `loop_complete_iteration` | Complete a run and schedule the next one |
| `loop_read_default_prompt` | Resolve the maintenance prompt |

## Development

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

The implementation uses only the Python standard library for the default `exec` and MCP paths. The optional app-server runner needs `websockets`.

## Limitations

- Bare `/loop` is not a native Codex slash command. Use `$loop` from the skill or `/prompts:loop` via the custom prompt template.
- The Stop hook fallback can only continue when a task is already due at turn stop time. It is not the primary scheduler.
- The app-server runner is closest to interactive session scheduling, but Codex app-server WebSocket support is still experimental.
- Arbitrary TUI slash commands inside loop prompts need adapters; `/prompts:name` and `/review` are handled.

## License

MIT. See [LICENSE](LICENSE).
