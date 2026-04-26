from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from codex_loop.mcp_server import LoopMcpServer
from codex_loop.models import utcnow
from codex_loop.parser import parse_loop_args
from codex_loop.scheduler import DryRunRunner, build_iteration_prompt, run_once
from codex_loop.store import LoopStore


class McpAndSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LoopStore(Path(self.tmp.name) / "loop.sqlite3")
        self.server = LoopMcpServer(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _call(self, name: str, arguments: dict) -> dict:
        response = self.server.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
        )
        self.assertNotIn("error", response)
        return response["result"]["structuredContent"]

    def test_mcp_create_list_complete(self) -> None:
        created = self._call("loop_create", {"raw_user_input": "5m check deploy", "cwd": self.tmp.name, "thread_id": "t1"})
        job_id = created["created"]["id"]
        listed = self._call("loop_list", {"thread_id": "t1"})
        self.assertEqual(listed["tasks"][0]["id"], job_id)

        updated = self._call(
            "loop_complete_iteration",
            {"job_id": job_id, "status": "pause", "summary": "blocked"},
        )
        self.assertEqual(updated["task"]["status"], "paused")

    def test_tools_list(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("loop_create", names)
        self.assertIn("loop_complete_iteration", names)

    def test_build_iteration_prompt_contains_completion_contract(self) -> None:
        task = self.store.create_task(parse_loop_args("do work"), thread_id="t1", cwd=self.tmp.name)
        prompt = build_iteration_prompt(task)
        self.assertIn(f"[Codex Loop Job: {task.id}]", prompt)
        self.assertIn("loop_complete_iteration", prompt)
        self.assertIn("60..3600", prompt)

    def test_run_once_processes_due_task(self) -> None:
        old = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(parse_loop_args("1m do work"), thread_id="t1", cwd=self.tmp.name, now=old)
        count = run_once(self.store, DryRunRunner())
        self.assertEqual(count, 1)
        updated = self.store.get_task(task.id)
        self.assertEqual(updated.status, "active")
        self.assertEqual(updated.run_count, 1)
        self.assertEqual(updated.last_result_summary, f"dry run for {task.id}")


if __name__ == "__main__":
    unittest.main()
