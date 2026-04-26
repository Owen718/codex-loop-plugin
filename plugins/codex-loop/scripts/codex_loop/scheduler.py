from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Protocol

from .app_server import AppServerRunner
from .models import LoopTask, RunResult
from .store import LoopStore, has_concrete_thread_id


def build_iteration_prompt(task: LoopTask) -> str:
    run_id = task.current_run_id or "<unknown>"
    complete_instruction = (
        "When finished, call the MCP tool loop_complete_iteration with:\n"
        f"- job_id: {task.id}\n"
        f"- run_id: {run_id}\n"
        "- status: continue | pause | done | failed\n"
        "- summary: short result\n"
        "- next_delay_seconds: required for dynamic loops, choose 60..3600\n"
        "- next_delay_reason: why this delay makes sense\n"
    )
    if task.schedule_kind == "fixed":
        complete_instruction = (
            "When finished, call the MCP tool loop_complete_iteration with:\n"
            f"- job_id: {task.id}\n"
            f"- run_id: {run_id}\n"
            "- status: continue | pause | done | failed\n"
            "- summary: short result\n"
            "- next_delay_reason: short reason; next_delay_seconds is optional for fixed loops\n"
        )

    return f"""[Codex Loop Job: {task.id}]
[Codex Loop Run: {run_id}]

Run this scheduled loop iteration.

Loop metadata:
- schedule_kind: {task.schedule_kind}
- fixed_interval_seconds: {task.fixed_interval_seconds}
- visibility_policy: {task.visibility_policy}
- binding_status: {task.binding_status}
- runner: {task.runner}
- run_count_before_this_iteration: {task.run_count}
- expires_at: {task.expires_at}
- no_catch_up: true

Original user prompt:
{task.prompt}

Rules:
- Stay within the user's original authorization and this thread's existing context.
- Do not start unrelated work.
- Do not push, publish, delete, force-push, or perform irreversible actions unless clearly authorized in the existing conversation or original prompt.
- If blocked, report the blocker and pause or lengthen the next interval.

{complete_instruction}
"""


class Runner(Protocol):
    kind: str

    def run(self, task: LoopTask, prompt: str) -> RunResult:
        ...


@dataclass
class DryRunRunner:
    kind: str = "dry-run"

    def run(self, task: LoopTask, prompt: str) -> RunResult:
        return RunResult(status="completed", summary=f"dry run for {task.id}", output=prompt)


@dataclass
class ExecRunner:
    codex_bin: str = "codex"
    timeout_seconds: int = 60 * 60
    kind: str = "exec"

    def run(self, task: LoopTask, prompt: str) -> RunResult:
        cmd = [
            self.codex_bin,
            "exec",
            "--cd",
            task.cwd,
            "--ask-for-approval",
            task.approval_policy_snapshot or "never",
        ]
        sandbox = task.sandbox_snapshot
        if sandbox:
            cmd.extend(["--sandbox", sandbox])
        if task.model_snapshot:
            cmd.extend(["--model", task.model_snapshot])
        cmd.append(prompt)
        proc = subprocess.run(
            cmd,
            cwd=task.cwd,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode == 0:
            return RunResult(status="completed", summary="codex exec completed", output=output[-4000:])
        return RunResult(status="failed", summary=f"codex exec failed with exit {proc.returncode}", output=output[-4000:])


class CodexMcpRunner:
    kind = "codex-mcp"

    def __init__(self, codex_bin: str = "codex", timeout_seconds: int = 60 * 60):
        from .stdio_mcp_client import StdioMcpClient

        self.timeout_seconds = timeout_seconds
        self.client = StdioMcpClient([codex_bin, "mcp-server"], timeout_seconds=timeout_seconds)
        self.client.initialize()

    def close(self) -> None:
        self.client.close()

    def run(self, task: LoopTask, prompt: str) -> RunResult:
        tools = {tool["name"] for tool in self.client.list_tools()}
        if has_concrete_thread_id(task.thread_id) and "codex-reply" in tools:
            result = self.client.call_tool(
                "codex-reply",
                {"threadId": task.thread_id, "prompt": prompt, "cwd": task.cwd},
            )
        elif task.visibility_policy == "background_ok" and "codex" in tools:
            result = self.client.call_tool(
                "codex",
                {
                    "prompt": prompt,
                    "cwd": task.cwd,
                    "sandbox": task.sandbox_snapshot,
                    "approvalPolicy": task.approval_policy_snapshot,
                    "model": task.model_snapshot,
                },
            )
        else:
            return RunResult(
                status="failed",
                summary="Codex MCP cannot safely continue this loop without a concrete thread id",
            )

        text = json.dumps(result, ensure_ascii=False)
        thread_id = _find_thread_id(result)
        return RunResult(status="completed", summary="codex mcp run completed", thread_id=thread_id, output=text[-4000:])


def _find_thread_id(value) -> str | None:
    if isinstance(value, dict):
        for key in ("threadId", "thread_id"):
            if isinstance(value.get(key), str):
                return value[key]
        for child in value.values():
            found = _find_thread_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_thread_id(child)
            if found:
                return found
    elif isinstance(value, str):
        try:
            return _find_thread_id(json.loads(value))
        except Exception:
            return None
    return None


def make_runner(args: argparse.Namespace) -> Runner:
    if args.runner == "dry-run":
        return DryRunRunner()
    if args.runner == "exec":
        return ExecRunner(codex_bin=args.codex_bin, timeout_seconds=args.turn_timeout)
    if args.runner == "codex-mcp":
        return CodexMcpRunner(codex_bin=args.codex_bin, timeout_seconds=args.turn_timeout)
    if args.runner == "app-server":
        if not args.app_server:
            raise SystemExit("--app-server is required for app-server runner")
        return AppServerRunner(
            url=args.app_server,
            token=os.environ.get(args.app_server_token_env) if args.app_server_token_env else None,
            turn_timeout_seconds=args.turn_timeout,
        )
    raise SystemExit(f"unknown runner: {args.runner}")


def _preflight_block_reason(task: LoopTask, runner_kind: str) -> str | None:
    if task.runner != runner_kind:
        return f"Loop task requires runner {task.runner}; active daemon runner is {runner_kind}."
    if task.visibility_policy in {"visible_only", "thread_only"} and not has_concrete_thread_id(task.thread_id):
        return "Loop task is not bound to a concrete Codex session id; refusing to start a new session."
    if task.visibility_policy == "visible_only" and runner_kind != "app-server":
        return "visible_only loop tasks require an app-server based runner attached to the current TUI runtime."
    if task.visibility_policy == "thread_only" and runner_kind not in {"app-server", "codex-mcp"}:
        return "thread_only loop tasks require a runner that can continue the existing Codex thread."
    return None


def run_once(store: LoopStore, runner: Runner, *, limit: int = 10) -> int:
    store.expire_due_tasks()
    tasks = store.acquire_due_tasks(limit=limit)
    runner_kind = getattr(runner, "kind", type(runner).__name__)
    for task in tasks:
        block_reason = _preflight_block_reason(task, runner_kind)
        if block_reason:
            store.abort_current_run(task.id, status="paused", summary=block_reason)
            continue
        prompt = build_iteration_prompt(task)
        try:
            result = runner.run(task, prompt)
        except Exception as exc:
            store.complete_iteration(
                task.id,
                run_id=task.current_run_id,
                status="failed",
                summary=f"runner exception: {exc}",
                completion_source="scheduler_exception",
            )
            continue
        if result.thread_id and task.thread_id == "current" and task.visibility_policy == "background_ok":
            store.replace_task_thread_id(task.id, result.thread_id)
        status = "continue" if result.status == "completed" else "failed"
        store.complete_iteration(
            task.id,
            run_id=task.current_run_id,
            status=status,
            summary=result.summary,
            thread_id=result.thread_id,
            completion_source="scheduler_fallback",
            next_delay_reason="runner completed without explicit loop_complete_iteration"
            if result.status == "completed"
            else "runner failed",
        )
    return len(tasks)


def run_daemon(args: argparse.Namespace) -> int:
    store = LoopStore(args.db)
    runner = make_runner(args)
    try:
        while True:
            count = run_once(store, runner, limit=args.limit)
            if args.once:
                return 0
            sleep_for = args.poll_seconds if count == 0 else min(args.poll_seconds, 1)
            time.sleep(sleep_for)
    finally:
        close = getattr(runner, "close", None)
        if close:
            close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Codex Loop scheduler daemon.")
    parser.add_argument("--db", default=None, help="SQLite DB path. Defaults to $CODEX_LOOP_DB or ~/.codex-loop/loop.sqlite3.")
    parser.add_argument("--runner", choices=["app-server", "codex-mcp", "exec", "dry-run"], default="exec")
    parser.add_argument("--app-server", default=None, help="App server websocket URL, e.g. ws://127.0.0.1:4500")
    parser.add_argument("--app-server-token-env", default="CODEX_WS_TOKEN")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--turn-timeout", type=int, default=60 * 60)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--once", action="store_true", help="Process due tasks once and exit.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return run_daemon(args)


if __name__ == "__main__":
    raise SystemExit(main())
