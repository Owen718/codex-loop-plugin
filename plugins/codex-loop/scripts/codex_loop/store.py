from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from .models import CompletionStatus, LoopTask, ParsedLoop, iso, utcnow


SCHEMA_VERSION = 1
DEFAULT_DB_PATH = Path.home() / ".codex-loop" / "loop.sqlite3"
DEFAULT_THREAD_ID = "current"
LEASE_SECONDS = 30 * 60
EXPIRE_DAYS = 7
MAX_TASKS_PER_THREAD = 50


def default_db_path() -> Path:
    return Path(os.environ.get("CODEX_LOOP_DB", DEFAULT_DB_PATH)).expanduser()


def deterministic_jitter_seconds(task_id: str, interval_seconds: int) -> int:
    max_jitter = min(int(interval_seconds * 0.10), 15 * 60)
    if max_jitter <= 0:
        return 0
    digest = hashlib.sha256(task_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % (max_jitter + 1)


class LoopStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS loop_tasks (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    raw_user_input TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    prompt_kind TEXT NOT NULL,
                    default_prompt_path TEXT,
                    schedule_kind TEXT NOT NULL,
                    fixed_interval_seconds INTEGER,
                    cron TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    next_run_at TEXT NOT NULL,
                    last_run_at TEXT,
                    status TEXT NOT NULL,
                    run_count INTEGER NOT NULL,
                    max_runs INTEGER,
                    jitter_seed TEXT NOT NULL,
                    no_catch_up INTEGER NOT NULL,
                    approval_policy_snapshot TEXT,
                    sandbox_snapshot TEXT,
                    model_snapshot TEXT,
                    last_result_summary TEXT,
                    last_next_delay_reason TEXT,
                    failure_count INTEGER NOT NULL,
                    lease_until TEXT,
                    cancel_requested INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_loop_due ON loop_tasks(status, next_run_at, expires_at)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_loop_thread ON loop_tasks(thread_id, status)"
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def _row_to_task(self, row: sqlite3.Row) -> LoopTask:
        values = dict(row)
        values["no_catch_up"] = bool(values.pop("no_catch_up"))
        values["cancel_requested"] = bool(values.pop("cancel_requested"))
        values["metadata"] = json.loads(values.pop("metadata_json") or "{}")
        return LoopTask(**values)

    def _task_columns(self) -> list[str]:
        return [
            "id",
            "thread_id",
            "cwd",
            "raw_user_input",
            "prompt",
            "prompt_kind",
            "default_prompt_path",
            "schedule_kind",
            "fixed_interval_seconds",
            "cron",
            "created_at",
            "updated_at",
            "expires_at",
            "next_run_at",
            "last_run_at",
            "status",
            "run_count",
            "max_runs",
            "jitter_seed",
            "no_catch_up",
            "approval_policy_snapshot",
            "sandbox_snapshot",
            "model_snapshot",
            "last_result_summary",
            "last_next_delay_reason",
            "failure_count",
            "lease_until",
            "cancel_requested",
            "metadata_json",
        ]

    def _insert_task(self, conn: sqlite3.Connection, task: LoopTask) -> None:
        values = task.to_dict()
        values["no_catch_up"] = int(task.no_catch_up)
        values["cancel_requested"] = int(task.cancel_requested)
        values["metadata_json"] = json.dumps(task.metadata, sort_keys=True)
        values.pop("metadata")
        columns = self._task_columns()
        placeholders = ", ".join(["?"] * len(columns))
        conn.execute(
            f"INSERT INTO loop_tasks ({', '.join(columns)}) VALUES ({placeholders})",
            tuple(values[col] for col in columns),
        )

    def create_task(
        self,
        parsed: ParsedLoop,
        *,
        thread_id: str | None = None,
        cwd: str | None = None,
        approval_policy: str | None = None,
        sandbox: str | None = None,
        model: str | None = None,
        max_runs: int | None = None,
        now=None,
    ) -> LoopTask:
        if parsed.action != "create":
            raise ValueError(f"cannot create task from action {parsed.action}")
        current = now or utcnow()
        tid = thread_id or os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_LOOP_THREAD_ID") or DEFAULT_THREAD_ID
        working_dir = str(Path(cwd or os.getcwd()).resolve())
        task_id = secrets.token_hex(4)
        if parsed.schedule_kind == "fixed":
            first_delay = int(parsed.fixed_interval_seconds or 60)
        else:
            first_delay = 60
        task = LoopTask(
            id=task_id,
            thread_id=tid,
            cwd=working_dir,
            raw_user_input=parsed.raw_user_input,
            prompt=parsed.prompt or "",
            prompt_kind=parsed.prompt_kind or "explicit",
            default_prompt_path=parsed.default_prompt_path,
            schedule_kind=parsed.schedule_kind or "dynamic",
            fixed_interval_seconds=parsed.fixed_interval_seconds,
            cron=None,
            created_at=iso(current),
            updated_at=iso(current),
            expires_at=iso(current + timedelta(days=EXPIRE_DAYS)),
            next_run_at=iso(current + timedelta(seconds=first_delay)),
            last_run_at=None,
            status="active",
            run_count=0,
            max_runs=max_runs,
            jitter_seed=task_id,
            no_catch_up=True,
            approval_policy_snapshot=approval_policy,
            sandbox_snapshot=sandbox,
            model_snapshot=model,
            last_result_summary=None,
            last_next_delay_reason=None,
            failure_count=0,
            lease_until=None,
            cancel_requested=False,
            metadata={},
        )
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active_count = conn.execute(
                """
                SELECT COUNT(*) FROM loop_tasks
                WHERE thread_id = ? AND status IN ('active', 'running', 'paused')
                """,
                (tid,),
            ).fetchone()[0]
            if active_count >= MAX_TASKS_PER_THREAD:
                raise ValueError(f"thread {tid} already has {MAX_TASKS_PER_THREAD} loop tasks")
            self._insert_task(conn, task)
            conn.execute("COMMIT")
        return task

    def get_task(self, task_id: str) -> LoopTask | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM loop_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(
        self,
        *,
        thread_id: str | None = None,
        include_inactive: bool = False,
    ) -> list[LoopTask]:
        sql = "SELECT * FROM loop_tasks"
        clauses: list[str] = []
        params: list[Any] = []
        if thread_id:
            clauses.append("thread_id = ?")
            params.append(thread_id)
        if not include_inactive:
            clauses.append("status IN ('active', 'running', 'paused')")
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY next_run_at ASC"
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_task(row) for row in rows]

    def update_status(self, task_id: str, status: str) -> LoopTask:
        if status not in {"active", "paused", "cancelled", "failed", "done"}:
            raise ValueError(f"unsupported status: {status}")
        now = iso(utcnow())
        with self.connect() as conn:
            conn.execute(
                "UPDATE loop_tasks SET status = ?, updated_at = ?, lease_until = NULL WHERE id = ?",
                (status, now, task_id),
            )
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        return task

    def request_cancel(self, task_id: str) -> LoopTask:
        now = iso(utcnow())
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status FROM loop_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError(task_id)
            if row["status"] == "running":
                conn.execute(
                    "UPDATE loop_tasks SET cancel_requested = 1, updated_at = ? WHERE id = ?",
                    (now, task_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE loop_tasks
                    SET status = 'cancelled', cancel_requested = 0, lease_until = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, task_id),
                )
            conn.execute("COMMIT")
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        return task

    def expire_due_tasks(self, *, now=None) -> int:
        current = iso(now or utcnow())
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE loop_tasks
                SET status = 'expired', updated_at = ?, lease_until = NULL
                WHERE status IN ('active', 'paused', 'running') AND expires_at <= ?
                """,
                (current, current),
            )
        return cursor.rowcount

    def acquire_due_tasks(self, *, limit: int = 10, now=None, thread_id: str | None = None) -> list[LoopTask]:
        current_dt = now or utcnow()
        current = iso(current_dt)
        lease_until = iso(current_dt + timedelta(seconds=LEASE_SECONDS))
        acquired: list[LoopTask] = []
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE loop_tasks
                SET status = 'failed', lease_until = NULL, updated_at = ?
                WHERE status = 'running' AND lease_until IS NOT NULL AND lease_until <= ?
                """,
                (current, current),
            )
            params: list[Any] = [current, current]
            thread_clause = ""
            if thread_id:
                thread_clause = " AND thread_id = ?"
                params.append(thread_id)
            params.append(limit)
            rows = conn.execute(
                f"""
                SELECT * FROM loop_tasks
                WHERE status = 'active' AND next_run_at <= ? AND expires_at > ?{thread_clause}
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            for row in rows:
                conn.execute(
                    """
                    UPDATE loop_tasks
                    SET status = 'running', lease_until = ?, updated_at = ?
                    WHERE id = ? AND status = 'active'
                    """,
                    (lease_until, current, row["id"]),
                )
                updated = conn.execute("SELECT * FROM loop_tasks WHERE id = ?", (row["id"],)).fetchone()
                acquired.append(self._row_to_task(updated))
            conn.execute("COMMIT")
        return acquired

    def complete_iteration(
        self,
        task_id: str,
        *,
        status: CompletionStatus,
        summary: str = "",
        next_delay_seconds: int | None = None,
        next_delay_reason: str | None = None,
        thread_id: str | None = None,
        now=None,
    ) -> LoopTask:
        current_dt = now or utcnow()
        current = iso(current_dt)
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM loop_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError(task_id)
            task = self._row_to_task(row)

            run_count = task.run_count + 1
            failure_count = task.failure_count
            final_status = "active"
            cancel_requested = False

            if task.cancel_requested:
                final_status = "cancelled"
            elif status == "pause":
                final_status = "paused"
            elif status == "done":
                final_status = "done"
            elif status == "failed":
                failure_count += 1
                final_status = "paused" if failure_count >= 3 else "active"

            if task.max_runs is not None and run_count >= task.max_runs and final_status == "active":
                final_status = "done"

            if task.schedule_kind == "fixed":
                base_delay = int(task.fixed_interval_seconds or 60)
                delay = base_delay + deterministic_jitter_seconds(task.jitter_seed, base_delay)
            else:
                delay = int(next_delay_seconds or 10 * 60)
                delay = max(60, min(delay, 60 * 60))
            next_run_at = iso(current_dt + timedelta(seconds=delay))

            if final_status != "active":
                next_run_at = task.next_run_at

            conn.execute(
                """
                UPDATE loop_tasks
                SET status = ?,
                    thread_id = COALESCE(?, thread_id),
                    updated_at = ?,
                    next_run_at = ?,
                    last_run_at = ?,
                    run_count = ?,
                    last_result_summary = ?,
                    last_next_delay_reason = ?,
                    failure_count = ?,
                    lease_until = NULL,
                    cancel_requested = ?
                WHERE id = ?
                """,
                (
                    final_status,
                    thread_id,
                    current,
                    next_run_at,
                    current,
                    run_count,
                    summary,
                    next_delay_reason,
                    failure_count,
                    int(cancel_requested),
                    task_id,
                ),
            )
            conn.execute("COMMIT")
        updated = self.get_task(task_id)
        if not updated:
            raise KeyError(task_id)
        return updated

    def replace_task_thread_id(self, task_id: str, thread_id: str) -> LoopTask:
        now = iso(utcnow())
        with self.connect() as conn:
            conn.execute(
                "UPDATE loop_tasks SET thread_id = ?, updated_at = ? WHERE id = ?",
                (thread_id, now, task_id),
            )
        task = self.get_task(task_id)
        if not task:
            raise KeyError(task_id)
        return task


def summarize_tasks(tasks: Iterable[LoopTask]) -> list[dict[str, Any]]:
    return [
        {
            "id": task.id,
            "thread_id": task.thread_id,
            "cwd": task.cwd,
            "schedule_kind": task.schedule_kind,
            "interval_seconds": task.fixed_interval_seconds,
            "status": task.status,
            "next_run_at": task.next_run_at,
            "last_run_at": task.last_run_at,
            "run_count": task.run_count,
            "failure_count": task.failure_count,
            "prompt_kind": task.prompt_kind,
            "summary": task.last_result_summary,
        }
        for task in tasks
    ]
