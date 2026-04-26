from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .scheduler import build_iteration_prompt
from .store import LoopStore


def _load_event(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _find_created_task_id(value: Any) -> str | None:
    if isinstance(value, dict):
        created = value.get("created")
        if isinstance(created, dict) and isinstance(created.get("id"), str):
            return created["id"]
        task = value.get("task")
        if isinstance(task, dict) and isinstance(task.get("id"), str):
            return task["id"]
        for child in value.values():
            found = _find_created_task_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_created_task_id(child)
            if found:
                return found
    elif isinstance(value, str):
        try:
            return _find_created_task_id(json.loads(value))
        except Exception:
            return None
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stop hook fallback for Codex Loop.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--max-due", type=int, default=1)
    parser.add_argument("--bind-created-task", action="store_true")
    args = parser.parse_args(argv)

    try:
        raw_event = sys.stdin.read()
        event = _load_event(raw_event)
        store = LoopStore(args.db)
        if args.bind_created_task:
            task_id = _find_created_task_id(event)
            session_id = event.get("session_id") or event.get("thread_id")
            if task_id and session_id:
                task = store.bind_task_thread(task_id, session_id, resume=True)
                print(json.dumps({"decision": "approve", "reason": f"bound loop task {task.id} to session {session_id}"}))
            else:
                print(json.dumps({"decision": "approve", "reason": "codex-loop did not find a task id/session id to bind"}))
            return 0

        store.expire_due_tasks()
        thread_id = args.thread_id or event.get("session_id") or event.get("thread_id")
        tasks = store.acquire_due_tasks(limit=args.max_due, thread_id=thread_id)
        if not tasks:
            print(json.dumps({"decision": "approve"}))
            return 0
        task = tasks[0]
        prompt = build_iteration_prompt(task)
        print(json.dumps({"decision": "block", "reason": prompt}))
        return 0
    except Exception as exc:
        print(json.dumps({"decision": "approve", "reason": f"codex-loop hook ignored error: {exc}"}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
