from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from codex_loop.app_server import AppServerError, AppServerRunner, _JsonRpc


class FakeWebSocket:
    def __init__(self, responses: list[dict]):
        self.responses = [json.dumps(response) for response in responses]
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        return self.responses.pop(0)


class AppServerRunnerTests(unittest.TestCase):
    def test_auth_headers_read_token_file_and_strip_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_file = Path(tmp) / "ws-token"
            token_file.write_text("file-token\n", encoding="utf-8")
            runner = AppServerRunner("ws://127.0.0.1:4500", token="env-token", token_file=token_file)

            self.assertEqual(runner.auth_headers(), {"Authorization": "Bearer file-token"})

    def test_auth_headers_report_missing_explicit_token_file(self) -> None:
        runner = AppServerRunner("ws://127.0.0.1:4500", token_file="/missing/ws-token")

        with self.assertRaises(AppServerError):
            runner.auth_headers()

    def test_json_rpc_uses_app_server_wire_format_and_initialized_notification(self) -> None:
        async def run() -> tuple[dict, FakeWebSocket]:
            ws = FakeWebSocket(
                [
                    {"method": "thread/status/changed", "params": {"threadId": "t1"}},
                    {"id": 1, "result": {"ok": True}},
                ]
            )
            rpc = _JsonRpc(ws)
            result = await rpc.call("initialize", {"clientInfo": {"name": "test"}})
            await rpc.notify("initialized", {})
            return result, ws

        result, ws = asyncio.run(run())

        self.assertEqual(result, {"ok": True})
        self.assertEqual(ws.sent[0]["method"], "initialize")
        self.assertNotIn("jsonrpc", ws.sent[0])
        self.assertEqual(ws.sent[1], {"method": "initialized", "params": {}})


if __name__ == "__main__":
    unittest.main()
