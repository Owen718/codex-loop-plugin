from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Callable

from .daemon import daemon_status, ensure_daemon_running
from .parser import parse_loop_args
from .prompts import resolve_default_prompt
from .store import LoopStore, summarize_tasks


def _text_result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
            }
        ],
        "structuredContent": value,
    }


def _tool(name: str, description: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": schema}


class LoopMcpServer:
    def __init__(self, store: LoopStore):
        self.store = store
        self.tools: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "loop_create": self.loop_create,
            "loop_list": self.loop_list,
            "loop_delete": self.loop_delete,
            "loop_update": self.loop_update,
            "loop_complete_iteration": self.loop_complete_iteration,
            "loop_read_default_prompt": self.loop_read_default_prompt,
        }

    def tool_specs(self) -> list[dict[str, Any]]:
        return [
            _tool(
                "loop_create",
                "Create a session-scoped recurring Codex loop task from raw /loop-style arguments.",
                {
                    "type": "object",
                    "properties": {
                        "raw_user_input": {"type": "string", "description": "Raw loop arguments, e.g. '5m check deploy'."},
                        "thread_id": {
                            "type": "string",
                            "description": "Current Codex thread id. In Codex, pass CODEX_THREAD_ID so loopd can reply to this thread.",
                        },
                        "cwd": {"type": "string", "description": "Working directory for this loop."},
                        "approval_policy": {"type": "string", "description": "Approval policy snapshot, e.g. never/on-request."},
                        "sandbox": {"type": "string", "description": "Sandbox snapshot, e.g. read-only/workspace-write/danger-full-access."},
                        "model": {"type": "string", "description": "Model snapshot."},
                        "max_runs": {"type": "integer", "minimum": 1},
                    },
                    "required": ["raw_user_input"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "loop_list",
                "List loop tasks, optionally filtered to a thread.",
                {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string"},
                        "include_inactive": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
            ),
            _tool(
                "loop_delete",
                "Cancel a loop task. Running tasks are marked cancel_requested and will stop after the current turn.",
                {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "loop_update",
                "Pause or resume a loop task.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["active", "paused", "cancelled", "failed", "done"]},
                    },
                    "required": ["job_id", "status"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "loop_complete_iteration",
                "Complete the current loop iteration and schedule the next run.",
                {
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "status": {"type": "string", "enum": ["continue", "pause", "done", "failed"]},
                        "summary": {"type": "string"},
                        "next_delay_seconds": {"type": "integer", "minimum": 60, "maximum": 3600},
                        "next_delay_reason": {"type": "string"},
                        "thread_id": {"type": "string"},
                    },
                    "required": ["job_id", "status"],
                    "additionalProperties": False,
                },
            ),
            _tool(
                "loop_read_default_prompt",
                "Resolve the default maintenance prompt for a cwd.",
                {
                    "type": "object",
                    "properties": {"cwd": {"type": "string"}},
                    "additionalProperties": False,
                },
            ),
        ]

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        request_id = message.get("id")
        try:
            if method == "initialize":
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "codex-loop", "version": "0.1.2"},
                    },
                }
            if method == "notifications/initialized":
                return None
            if method == "tools/list":
                return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tool_specs()}}
            if method == "tools/call":
                params = message.get("params") or {}
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name not in self.tools:
                    raise KeyError(f"unknown tool: {name}")
                result = self.tools[name](arguments)
                return {"jsonrpc": "2.0", "id": request_id, "result": result}
            if request_id is None:
                return None
            return self._error(request_id, -32601, f"method not found: {method}")
        except Exception as exc:
            if request_id is None:
                return None
            return self._error(request_id, -32000, f"{exc}", traceback.format_exc())

    def _error(self, request_id: Any, code: int, message: str, data: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    def loop_create(self, args: dict[str, Any]) -> dict[str, Any]:
        cwd = args.get("cwd") or os.getcwd()
        parsed = parse_loop_args(args["raw_user_input"], cwd=cwd)
        if parsed.action != "create":
            return _text_result({"action": parsed.action, "message": "Use loop_list/loop_delete/loop_update for management actions."})
        thread_id = args.get("thread_id") or os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_LOOP_THREAD_ID")
        task = self.store.create_task(
            parsed,
            thread_id=thread_id,
            cwd=cwd,
            approval_policy=args.get("approval_policy"),
            sandbox=args.get("sandbox"),
            model=args.get("model"),
            max_runs=args.get("max_runs"),
        )
        daemon = ensure_daemon_running(db_path=self.store.path)
        result = {"created": task.to_dict(), "daemon": daemon.to_dict()}
        if task.thread_id == "current":
            result["warning"] = "No concrete Codex thread id was provided; scheduled runs may start a new session instead of replying here."
        return _text_result(result)

    def loop_list(self, args: dict[str, Any]) -> dict[str, Any]:
        tasks = self.store.list_tasks(
            thread_id=args.get("thread_id"),
            include_inactive=bool(args.get("include_inactive", False)),
        )
        return _text_result({"tasks": summarize_tasks(tasks), "daemon": daemon_status().to_dict()})

    def loop_delete(self, args: dict[str, Any]) -> dict[str, Any]:
        task = self.store.request_cancel(args["job_id"])
        return _text_result({"task": task.to_dict()})

    def loop_update(self, args: dict[str, Any]) -> dict[str, Any]:
        task = self.store.update_status(args["job_id"], args["status"])
        return _text_result({"task": task.to_dict()})

    def loop_complete_iteration(self, args: dict[str, Any]) -> dict[str, Any]:
        task = self.store.complete_iteration(
            args["job_id"],
            status=args["status"],
            summary=args.get("summary", ""),
            next_delay_seconds=args.get("next_delay_seconds"),
            next_delay_reason=args.get("next_delay_reason"),
            thread_id=args.get("thread_id"),
        )
        return _text_result({"task": task.to_dict()})

    def loop_read_default_prompt(self, args: dict[str, Any]) -> dict[str, Any]:
        prompt, path = resolve_default_prompt(args.get("cwd"))
        return _text_result({"prompt": prompt, "path": path})

    def serve(self) -> int:
        for line in sys.stdin:
            if not line.strip():
                continue
            response = self.handle(json.loads(line))
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n")
                sys.stdout.flush()
        return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex Loop MCP server")
    parser.add_argument("--db", default=None, help="SQLite DB path. Defaults to $CODEX_LOOP_DB or ~/.codex-loop/loop.sqlite3.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return LoopMcpServer(LoopStore(args.db)).serve()


if __name__ == "__main__":
    raise SystemExit(main())
