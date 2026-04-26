from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from codex_loop.models import parse_iso, utcnow
from codex_loop.parser import parse_loop_args
from codex_loop.store import LoopStore, deterministic_jitter_seconds


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
        self.assertEqual(len(self.store.list_tasks(thread_id="t1")), 1)

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
        self.assertEqual(self.store.get_task(task.id).status, "running")
        self.assertEqual(self.store.acquire_due_tasks(now=utcnow()), [])

    def test_complete_fixed_interval_uses_jitter(self) -> None:
        now = utcnow() - timedelta(minutes=2)
        task = self.store.create_task(parse_loop_args("5m do work"), thread_id="t1", cwd=self.tmp.name, now=now)
        self.store.acquire_due_tasks(now=utcnow())
        completed_at = utcnow()
        updated = self.store.complete_iteration(task.id, status="continue", summary="ok", now=completed_at)
        expected_delay = 300 + deterministic_jitter_seconds(task.id, 300)
        self.assertEqual(parse_iso(updated.next_run_at), completed_at + timedelta(seconds=expected_delay))
        self.assertEqual(updated.run_count, 1)
        self.assertEqual(updated.status, "active")

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
