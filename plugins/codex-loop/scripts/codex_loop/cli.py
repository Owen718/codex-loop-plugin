from __future__ import annotations

import argparse
import json
import os
from typing import Any

from .parser import parse_loop_args
from .prompts import resolve_default_prompt
from .scheduler import build_arg_parser as build_loopd_parser
from .scheduler import main as loopd_main
from .store import LoopStore, summarize_tasks
from .tui import add_tui_parser


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_create(args: argparse.Namespace) -> int:
    raw = " ".join(args.raw)
    parsed = parse_loop_args(raw, cwd=args.cwd)
    store = LoopStore(args.db)
    task = store.create_task(
        parsed,
        thread_id=args.thread_id,
        cwd=args.cwd,
        approval_policy=args.approval_policy,
        sandbox=args.sandbox,
        model=args.model,
        max_runs=args.max_runs,
        visibility_policy=args.visibility_policy,
        runner=args.runner,
    )
    _print_json(task.to_dict())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = LoopStore(args.db)
    _print_json(
        {
            "tasks": summarize_tasks(
                store.list_tasks(thread_id=args.thread_id, include_inactive=args.include_inactive)
            )
        }
    )
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    task = LoopStore(args.db).request_cancel(args.job_id)
    _print_json(task.to_dict())
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    task = LoopStore(args.db).update_status(args.job_id, args.status)
    _print_json(task.to_dict())
    return 0


def cmd_bind(args: argparse.Namespace) -> int:
    task = LoopStore(args.db).bind_task_thread(args.job_id, args.thread_id, resume=not args.no_resume)
    _print_json(task.to_dict())
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    task = LoopStore(args.db).complete_iteration(
        args.job_id,
        run_id=args.run_id,
        status=args.status,
        summary=args.summary or "",
        next_delay_seconds=args.next_delay_seconds,
        next_delay_reason=args.next_delay_reason,
        thread_id=args.thread_id,
        completion_source="cli",
    )
    _print_json(task.to_dict())
    return 0


def cmd_default_prompt(args: argparse.Namespace) -> int:
    prompt, path = resolve_default_prompt(args.cwd)
    _print_json({"prompt": prompt, "path": path})
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Codex Loop tasks.")
    parser.add_argument("--db", default=None, help="SQLite DB path. Defaults to $CODEX_LOOP_DB or ~/.codex-loop/loop.sqlite3.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create a loop task from /loop-style arguments.")
    create.add_argument("raw", nargs="*", help="Raw loop args, e.g. 5m check deploy")
    create.add_argument("--thread-id", default=None)
    create.add_argument("--cwd", default=os.getcwd())
    create.add_argument("--approval-policy", default=None)
    create.add_argument("--sandbox", default=None)
    create.add_argument("--model", default=None)
    create.add_argument("--max-runs", type=int, default=None)
    create.add_argument("--visibility-policy", choices=["visible_only", "thread_only", "background_ok"], default=None)
    create.add_argument("--runner", choices=["app-server", "codex-mcp", "exec", "dry-run"], default=None)
    create.set_defaults(func=cmd_create)

    list_cmd = sub.add_parser("list", help="List loop tasks.")
    list_cmd.add_argument("--thread-id", default=None)
    list_cmd.add_argument("--include-inactive", action="store_true")
    list_cmd.set_defaults(func=cmd_list)

    cancel = sub.add_parser("cancel", help="Cancel a loop task.")
    cancel.add_argument("job_id")
    cancel.set_defaults(func=cmd_cancel)

    pause = sub.add_parser("pause", help="Pause a loop task.")
    pause.add_argument("job_id")
    pause.set_defaults(func=cmd_update, status="paused")

    resume = sub.add_parser("resume", help="Resume a paused loop task.")
    resume.add_argument("job_id")
    resume.set_defaults(func=cmd_update, status="active")

    bind = sub.add_parser("bind", help="Bind a task to a concrete Codex session/thread id.")
    bind.add_argument("job_id")
    bind.add_argument("thread_id")
    bind.add_argument("--no-resume", action="store_true")
    bind.set_defaults(func=cmd_bind)

    complete = sub.add_parser("complete", help="Complete a loop iteration.")
    complete.add_argument("job_id")
    complete.add_argument("--run-id", default=None)
    complete.add_argument("--status", choices=["continue", "pause", "done", "failed"], required=True)
    complete.add_argument("--summary", default="")
    complete.add_argument("--next-delay-seconds", type=int, default=None)
    complete.add_argument("--next-delay-reason", default=None)
    complete.add_argument("--thread-id", default=None)
    complete.set_defaults(func=cmd_complete)

    default_prompt = sub.add_parser("default-prompt", help="Resolve default maintenance prompt.")
    default_prompt.add_argument("--cwd", default=os.getcwd())
    default_prompt.set_defaults(func=cmd_default_prompt)

    loopd = sub.add_parser("loopd", help="Run scheduler daemon.")
    for action in build_loopd_parser()._actions:
        if action.dest == "help":
            continue
        option_strings = action.option_strings
        kwargs = {
            "dest": action.dest,
            "default": action.default,
            "help": action.help,
        }
        if isinstance(action, argparse._StoreTrueAction):
            kwargs["action"] = "store_true"
        elif action.choices:
            kwargs["choices"] = action.choices
        elif action.type:
            kwargs["type"] = action.type
        if option_strings:
            loopd.add_argument(*option_strings, **kwargs)
    loopd.set_defaults(func=lambda ns: loopd_main(_loopd_argv(ns)))
    add_tui_parser(sub)
    return parser


def _loopd_argv(ns: argparse.Namespace) -> list[str]:
    argv: list[str] = []
    for key, value in vars(ns).items():
        if key in {"command", "func"} or value in (None, False):
            continue
        opt = "--" + key.replace("_", "-")
        if value is True:
            argv.append(opt)
        else:
            argv.extend([opt, str(value)])
    return argv


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
