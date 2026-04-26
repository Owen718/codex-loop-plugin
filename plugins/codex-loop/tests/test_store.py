from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from codex_loop.models import parse_iso, utcnow
from codex_loop.parser import parse_loop_args
from codex_loop.store import LoopStore, default_db_path, deterministic_jitter_seconds


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "loop.sqlite3"
        self.store = LoopStore(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_create_and_list(self) -> None:
        task = self.store.create_task(
            parse_loop_args("5m check deploy"),
            thread_id="t1",
            cwd=self.tmp.name,
            approval_policy="never",
            sandbox="workspace-write",
            model="gpt-test",
        )
        self.assertEqual(task.thread_id, "t1")
        self.assertEqual(task.schedule_kind, "fixed")
        self.assertEqual(task.fixed_interval_seconds, 300)
        self.assertEqual(task.status, "active")
        self.assertEqual(task.binding_status, "bound")
        self.assertEqual(task.visibility_policy, "visible_only")
        self.assertEqual(task.runner, "app-server")
        self.assertEqual(len(self.store.list_tasks(thread_id="t1")), 1)

    def test_create_without_thread_starts_pending_visible_task(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            task = self.store.create_task(parse_loop_args("5m check deploy"), cwd=self.tmp.name)

        self.assertEqual(task.thread_id, "current")
        self.assertEqual(task.binding_status, "pending")
        self.assertEqual(task.visibility_policy, "visible_only")

    def test_default_db_path_derives_runtime_from_app_server(self) -> None:
        with mock.patch.dict("os.environ", {"CODEX_LOOP_APP_SERVER": "ws://127.0.0.1:4555"}, clear=True):
            path = default_db_path()

        self.assertTrue(str(path).endswith(".codex-loop/runtimes/127-0-0-1-4555/loop.sqlite3"))

    def test_bind_task_thread_sets_bound_and_resumes_paused(self) -> None:
        task = self.store.create_task(parse_loop_args("5m check deploy"), cwd=self.tmp.name)
        self.store.update_status(task.id, "paused")

        bound = self.store.bind_task_thread(task.id, "thread-real")

        self.assertEqual(bound.thread_id, "thread-real")
        self.assertEqual(bound.binding_status, "bound")
        self.assertEqual(bound.status, "active")

    def test_acquire_due_marks_running_and_no_catch_up(self) -> None:
        now = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(
            parse_loop_args("1m do work"),
            thread_id="t1",
            cwd=self.tmp.name,
            now=now,
        )
        due = self.store.acquire_due_tasks(now=utcnow())
        self.assertEqual([t.id for t in due], [task.id])
        updated = self.store.get_task(task.id)
        self.assertEqual(updated.status, "running")
        self.assertIsNotNone(updated.current_run_id)
        self.assertEqual(self.store.acquire_due_tasks(now=utcnow()), [])

    def test_complete_fixed_interval_uses_jitter(self) -> None:
        now = utcnow() - timedelta(minutes=6)
        task = self.store.create_task(parse_loop_args("5m do work"), thread_id="t1", cwd=self.tmp.name, now=now)
        self.store.acquire_due_tasks(now=utcnow())
        completed_at = utcnow()
        updated = self.store.complete_iteration(task.id, status="continue", summary="ok", now=completed_at)
        expected_delay = 300 + deterministic_jitter_seconds(task.id, 300)
        self.assertEqual(parse_iso(updated.next_run_at), completed_at + timedelta(seconds=expected_delay))
        self.assertEqual(updated.run_count, 1)
        self.assertEqual(updated.status, "active")

    def test_complete_iteration_is_idempotent_for_run_id(self) -> None:
        now = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(parse_loop_args("1m do work"), thread_id="t1", cwd=self.tmp.name, now=now)
        running = self.store.acquire_due_tasks(now=utcnow())[0]
        run_id = running.current_run_id

        first = self.store.complete_iteration(running.id, run_id=run_id, status="continue", summary="ok")
        second = self.store.complete_iteration(running.id, run_id=run_id, status="continue", summary="again")

        self.assertEqual(first.run_count, 1)
        self.assertEqual(second.run_count, 1)
        self.assertEqual(second.last_result_summary, "ok")
        run = self.store.get_run(run_id)
        self.assertEqual(run.status, "completed")

    def test_complete_dynamic_clamps_delay(self) -> None:
        now = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(parse_loop_args("do work"), thread_id="t1", cwd=self.tmp.name, now=now)
        self.store.acquire_due_tasks(now=utcnow())
        completed_at = utcnow()
        updated = self.store.complete_iteration(
            task.id,
            status="continue",
            next_delay_seconds=10,
            summary="ok",
            now=completed_at,
        )
        self.assertEqual(parse_iso(updated.next_run_at), completed_at + timedelta(seconds=60))

    def test_cancel_running_sets_cancel_requested_then_cancelled_on_complete(self) -> None:
        now = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(parse_loop_args("1m do work"), thread_id="t1", cwd=self.tmp.name, now=now)
        self.store.acquire_due_tasks(now=utcnow())
        cancelled = self.store.request_cancel(task.id)
        self.assertEqual(cancelled.status, "running")
        self.assertTrue(cancelled.cancel_requested)
        completed = self.store.complete_iteration(task.id, status="continue", summary="done")
        self.assertEqual(completed.status, "cancelled")

    def test_acquire_due_filters_thread_before_claiming(self) -> None:
        old = utcnow() - timedelta(minutes=2)
        t1 = self.store.create_task(parse_loop_args("1m one"), thread_id="t1", cwd=self.tmp.name, now=old)
        t2 = self.store.create_task(parse_loop_args("1m two"), thread_id="t2", cwd=self.tmp.name, now=old)
        due = self.store.acquire_due_tasks(now=utcnow(), thread_id="t2")
        self.assertEqual([task.id for task in due], [t2.id])
        self.assertEqual(self.store.get_task(t1.id).status, "active")


if __name__ == "__main__":
    unittest.main()
