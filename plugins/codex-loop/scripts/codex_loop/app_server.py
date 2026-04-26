from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import LoopTask, RunResult


class AppServerError(RuntimeError):
    pass


@dataclass
class AppServerRunner:
    url: str
    token: str | None = None
    token_file: str | Path | None = None
    turn_timeout_seconds: int = 60 * 60
    kind: str = "app-server"

    def run(self, task: LoopTask, prompt: str) -> RunResult:
        return asyncio.run(self._run_async(task, prompt))

    async def _run_async(self, task: LoopTask, prompt: str) -> RunResult:
        try:
            import websockets
        except Exception as exc:
            raise AppServerError("app-server runner requires the optional 'websockets' Python package") from exc

        headers = self.auth_headers()

        connect_kwargs: dict[str, Any] = {}
        if headers:
            header_arg = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
            connect_kwargs[header_arg] = headers
        connection = websockets.connect(self.url, **connect_kwargs)

        async with connection as ws:
            rpc = _JsonRpc(ws)
            await rpc.call(
                "initialize",
                {
                    "clientInfo": {"name": "codex-loopd", "version": "0.1.3"},
                    "capabilities": {"experimentalApi": True},
                },
            )
            await rpc.notify("initialized", {})
            if task.thread_id and task.thread_id != "current":
                resume = await rpc.call(
                    "thread/resume",
                    {
                        "threadId": task.thread_id,
                        "cwd": task.cwd,
                        "approvalPolicy": task.approval_policy_snapshot,
                        "model": task.model_snapshot,
                    },
                )
                thread_id = resume.get("thread", {}).get("id", task.thread_id)
            elif task.visibility_policy == "background_ok":
                started = await rpc.call(
                    "thread/start",
                    {
                        "cwd": task.cwd,
                        "approvalPolicy": task.approval_policy_snapshot,
                        "model": task.model_snapshot,
                        "sessionStartSource": "startup",
                    },
                )
                thread_id = started.get("thread", {}).get("id")
                if not thread_id:
                    raise AppServerError("thread/start response did not include thread.id")
            else:
                raise AppServerError("visible/thread-only loop is not bound to a concrete app-server thread")

            turn = await rpc.call(
                "turn/start",
                {
                    "threadId": thread_id,
                    "cwd": task.cwd,
                    "input": [{"type": "text", "text": prompt, "text_elements": []}],
                    "approvalPolicy": task.approval_policy_snapshot,
                    "model": task.model_snapshot,
                },
            )
            turn_id = turn.get("turn", {}).get("id")
            status = await rpc.wait_for_turn(thread_id, turn_id, self.turn_timeout_seconds)
            if status == "completed":
                return RunResult(status="completed", summary="app-server turn completed", thread_id=thread_id)
            return RunResult(status="failed", summary=f"app-server turn ended with status {status}", thread_id=thread_id)

    def auth_headers(self) -> dict[str, str]:
        token = self.auth_token()
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    def auth_token(self) -> str | None:
        if self.token_file is not None:
            path = Path(self.token_file).expanduser()
            try:
                token = path.read_text(encoding="utf-8").strip()
            except FileNotFoundError as exc:
                raise AppServerError(f"app-server token file not found: {path}") from exc
            if token:
                return token
        return self.token


class _JsonRpc:
    def __init__(self, ws: Any):
        self.ws = ws
        self.next_id = 1
        self.pending_notifications: list[dict[str, Any]] = []

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise AppServerError(f"{method} failed: {message['error']}")
                return message.get("result") or {}
            self.pending_notifications.append(message)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self.ws.send(json.dumps({"method": method, "params": params}))

    async def wait_for_turn(self, thread_id: str, turn_id: str | None, timeout_seconds: int) -> str:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if self.pending_notifications:
                message = self.pending_notifications.pop(0)
            else:
                message = json.loads(await asyncio.wait_for(self.ws.recv(), timeout=max(1, deadline - time.monotonic())))
            if message.get("method") != "turn/completed":
                continue
            params = message.get("params") or {}
            turn = params.get("turn") or {}
            if params.get("threadId") != thread_id:
                continue
            if turn_id and turn.get("id") != turn_id:
                continue
            return turn.get("status") or "completed"
        raise AppServerError(f"timed out waiting for turn {turn_id or '<unknown>'}")
