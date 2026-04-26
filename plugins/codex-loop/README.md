# Codex Loop

Codex Loop is a plugin-shaped implementation of recurring prompt scheduling for Codex. It mirrors the useful parts of Claude Code `/loop` with a Codex-native split:

- `skills/loop`: user-facing `$loop` and `/prompts:loop` semantics
- MCP server: durable task CRUD and iteration completion tools
- SQLite store: task state, leases, expiry, no-catch-up scheduling
- `codex-loopd`: external scheduler daemon
- runners: `app-server`, `codex-mcp`, `exec`, and `dry-run`
- Stop hook fallback: immediate continuation for already-due jobs

## Quick Start

This repository includes a marketplace file at `.agents/plugins/marketplace.json` that exposes `./plugins/codex-loop`.

`codex plugin marketplace add ...` registers the marketplace source only. After that, restart Codex and install/enable `Codex Loop` from the plugin directory UI. That is what loads `$loop` and the MCP tools into Codex.

Create a loop task:

```bash
plugins/codex-loop/scripts/codex-loop create 5m "check deploy status"
```

Run the scheduler:

```bash
plugins/codex-loop/scripts/codex-loopd --runner exec
```

List or cancel tasks:

```bash
plugins/codex-loop/scripts/codex-loop list
plugins/codex-loop/scripts/codex-loop cancel <job_id>
```

## Codex Entrypoints

Copy `prompts/loop.md` to `~/.codex/prompts/loop.md` if you want:

```text
/prompts:loop 5m check deploy
```

When this plugin is installed, use:

```text
$loop 5m check deploy
$loop list
$loop cancel <job_id>
```

## Runners

`exec` is the simplest and most stable runner. It starts non-interactive Codex turns:

```bash
plugins/codex-loop/scripts/codex-loopd --runner exec
```

`codex-mcp` drives `codex mcp-server` and uses `codex` / `codex-reply` when exposed:

```bash
plugins/codex-loop/scripts/codex-loopd --runner codex-mcp
```

`app-server` is closest to a true interactive scheduled thread. Start Codex app-server and remote TUI first:

```bash
codex app-server --listen ws://127.0.0.1:4500 --ws-auth capability-token --ws-token-file ~/.codex-loop/ws-token
export CODEX_WS_TOKEN="$(cat ~/.codex-loop/ws-token)"
codex --remote ws://127.0.0.1:4500 --remote-auth-token-env CODEX_WS_TOKEN
plugins/codex-loop/scripts/codex-loopd --runner app-server --app-server ws://127.0.0.1:4500
```

The app-server runner needs Python's optional `websockets` package.

## Default Prompt

Bare loops resolve the maintenance prompt in this order:

1. `.codex/loop.md` in the current tree or nearest Git root
2. `~/.codex/loop.md`
3. built-in conservative maintenance prompt

## Safety

Tasks snapshot cwd, approval policy, sandbox, and model. Later iterations do not auto-upgrade them. Tasks expire after seven days, use deterministic jitter for fixed intervals, do not catch up missed ticks, and pause after repeated runner failures.
