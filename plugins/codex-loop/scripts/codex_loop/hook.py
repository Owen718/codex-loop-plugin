from __future__ import annotations

import argparse
import json
import sys

from .scheduler import build_iteration_prompt
from .store import LoopStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stop hook fallback for Codex Loop.")
    parser.add_argument("--db", default=None)
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--max-due", type=int, default=1)
    args = parser.parse_args(argv)

    try:
        _ = sys.stdin.read()
        store = LoopStore(args.db)
        store.expire_due_tasks()
        tasks = store.acquire_due_tasks(limit=args.max_due, thread_id=args.thread_id)
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
