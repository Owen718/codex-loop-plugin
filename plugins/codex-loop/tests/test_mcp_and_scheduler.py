from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from codex_loop.mcp_server import LoopMcpServer
from codex_loop.models import RunResult, utcnow
from codex_loop.parser import parse_loop_args
from codex_loop.runtime_state import write_active_runtime
from codex_loop.scheduler import CodexMcpRunner, DryRunRunner, build_iteration_prompt, run_once
from codex_loop.store import LoopStore


class McpAndSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env_patch = mock.patch.dict("os.environ", {"CODEX_LOOP_ACTIVE_RUNTIME": str(self.root / "active-runtime.json")}, clear=False)
        self.env_patch.start()
        self.store = LoopStore(self.root / "loop.sqlite3")
        self.server = LoopMcpServer(self.store)
        self.daemon_patch = mock.patch("codex_loop.mcp_server.ensure_daemon_running")
        self.ensure_daemon = self.daemon_patch.start()
        self.ensure_daemon.return_value.to_dict.return_value = {
            "enabled": True,
            "running": True,
            "started": False,
            "pid": 123,
            "reason": "running",
        }

    def tearDown(self) -> None:
        self.daemon_patch.stop()
        self.env_patch.stop()
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

    def test_mcp_create_uses_env_thread_id_and_autostarts_daemon(self) -> None:
        token_file = str(Path(self.tmp.name) / "ws-token")
        with mock.patch.dict(
            "os.environ",
            {"CODEX_THREAD_ID": "thread-real", "CODEX_LOOP_APP_SERVER_TOKEN_FILE": token_file},
            clear=False,
        ):
            self.ensure_daemon.return_value.to_dict.return_value = {
                "enabled": True,
                "running": True,
                "started": True,
                "pid": 123,
                "reason": "started",
            }
            created = self._call(
                "loop_create",
                {"raw_user_input": "5m check deploy", "cwd": self.tmp.name, "app_server": "ws://127.0.0.1:4500"},
            )

        self.assertEqual(created["created"]["thread_id"], "thread-real")
        self.assertEqual(created["daemon"]["started"], True)
        self.ensure_daemon.assert_called_once()
        self.assertEqual(Path(self.ensure_daemon.call_args.kwargs["db_path"]), self.store.path)
        self.assertEqual(self.ensure_daemon.call_args.kwargs["runner"], "app-server")
        self.assertEqual(self.ensure_daemon.call_args.kwargs["app_server"], "ws://127.0.0.1:4500")
        self.assertEqual(self.ensure_daemon.call_args.kwargs["app_server_token_file"], token_file)

    def test_mcp_create_warns_without_concrete_thread_id(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            created = self._call("loop_create", {"raw_user_input": "5m check deploy", "cwd": self.tmp.name})

        self.assertEqual(created["created"]["thread_id"], "current")
        self.assertIn("warning", created)
        self.assertIn("thread id", created["warning"])

    def test_mcp_server_switches_to_active_runtime_db_without_env_inheritance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active_runtime = root / "active-runtime.json"
            runtime_dir = root / "runtime"
            runtime_db = runtime_dir / "loop.sqlite3"
            with mock.patch.dict("os.environ", {"HOME": tmp, "CODEX_LOOP_ACTIVE_RUNTIME": str(active_runtime)}, clear=True):
                server = LoopMcpServer()
                write_active_runtime(
                    {
                        "CODEX_LOOP_RUNTIME_DIR": str(runtime_dir),
                        "CODEX_LOOP_DB": str(runtime_db),
                        "CODEX_LOOP_APP_SERVER": "ws://127.0.0.1:4555",
                        "CODEX_LOOP_APP_SERVER_TOKEN_FILE": str(runtime_dir / "ws-token"),
                        "CODEX_LOOP_RUNNER": "app-server",
                        "CODEX_LOOP_VISIBILITY_POLICY": "visible_only",
                    }
                )
                with mock.patch("codex_loop.mcp_server.ensure_daemon_running") as ensure_daemon:
                    ensure_daemon.return_value.to_dict.return_value = {"enabled": True, "running": True}
                    response = server.handle(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "tools/call",
                            "params": {
                                "name": "loop_create",
                                "arguments": {"raw_user_input": "1m ping", "cwd": tmp, "thread_id": "thread-real"},
                            },
                        }
                    )

            self.assertNotIn("error", response)
            self.assertEqual(Path(ensure_daemon.call_args.kwargs["db_path"]), runtime_db)
            self.assertEqual(LoopStore(runtime_db).list_tasks(thread_id="thread-real")[0].prompt, "ping")

    def test_tools_list(self) -> None:
        response = self.server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("loop_create", names)
        self.assertIn("loop_bind_session", names)
        self.assertIn("loop_complete_iteration", names)

    def test_build_iteration_prompt_contains_completion_contract(self) -> None:
        task = self.store.create_task(parse_loop_args("do work"), thread_id="t1", cwd=self.tmp.name)
        prompt = build_iteration_prompt(task)
        self.assertIn(f"[Codex Loop Job: {task.id}]", prompt)
        self.assertIn("loop_complete_iteration", prompt)
        self.assertIn("60..3600", prompt)

    def test_run_once_processes_due_task(self) -> None:
        old = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(
            parse_loop_args("1m do work"),
            thread_id="t1",
            cwd=self.tmp.name,
            visibility_policy="background_ok",
            runner="dry-run",
            now=old,
        )
        count = run_once(self.store, DryRunRunner())
        self.assertEqual(count, 1)
        updated = self.store.get_task(task.id)
        self.assertEqual(updated.status, "active")
        self.assertEqual(updated.run_count, 1)
        self.assertEqual(updated.last_result_summary, f"dry run for {task.id}")

    def test_run_once_pauses_visible_task_without_current_session_runner(self) -> None:
        old = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(parse_loop_args("1m do work"), cwd=self.tmp.name, now=old)

        count = run_once(self.store, DryRunRunner())

        self.assertEqual(count, 1)
        updated = self.store.get_task(task.id)
        self.assertEqual(updated.status, "paused")
        self.assertEqual(updated.run_count, 0)
        self.assertIn("Loop task requires runner app-server", updated.last_result_summary)
        self.assertIsNone(updated.current_run_id)

    def test_codex_mcp_runner_replies_to_known_thread_id(self) -> None:
        class FakeClient:
            def __init__(self) -> None:
                self.calls = []

            def list_tools(self) -> list[dict]:
                return [{"name": "codex"}, {"name": "codex-reply"}]

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.calls.append((name, arguments))
                return {"threadId": arguments.get("threadId", "new-thread"), "content": "ok"}

        task = self.store.create_task(parse_loop_args("1m do work"), thread_id="thread-real", cwd=self.tmp.name)
        client = FakeClient()
        runner = CodexMcpRunner.__new__(CodexMcpRunner)
        runner.client = client

        result: RunResult = runner.run(task, "prompt")

        self.assertEqual(result.thread_id, "thread-real")
        self.assertEqual(client.calls, [("codex-reply", {"threadId": "thread-real", "prompt": "prompt", "cwd": self.tmp.name})])


if __name__ == "__main__":
    unittest.main()
