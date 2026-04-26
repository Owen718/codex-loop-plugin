from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal


ScheduleKind = Literal["fixed", "dynamic"]
PromptKind = Literal["explicit", "default", "command"]
BindingStatus = Literal["pending", "bound", "lost", "unbound"]
VisibilityPolicy = Literal["visible_only", "thread_only", "background_ok"]
RunnerKind = Literal["app-server", "codex-mcp", "exec", "dry-run"]
TaskStatus = Literal[
    "active",
    "running",
    "paused",
    "cancelled",
    "expired",
    "failed",
    "done",
]
CompletionStatus = Literal["continue", "pause", "done", "failed"]
RunStatus = Literal["running", "completed", "failed", "cancelled", "ignored"]


def utcnow() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(UTC).replace(microsecond=0)


def in_seconds(seconds: int, *, now: datetime | None = None) -> str:
    base = now or utcnow()
    return iso(base + timedelta(seconds=seconds))


@dataclass(frozen=True)
class ParsedLoop:
    action: Literal["create", "list", "cancel", "pause", "resume", "delete"]
    raw_user_input: str
    prompt: str | None = None
    prompt_kind: PromptKind | None = None
    default_prompt_path: str | None = None
    schedule_kind: ScheduleKind | None = None
    fixed_interval_seconds: int | None = None
    target_id: str | None = None


@dataclass(frozen=True)
class LoopTask:
    id: str
    thread_id: str
    binding_status: BindingStatus
    visibility_policy: VisibilityPolicy
    runner: RunnerKind
    current_run_id: str | None
    cwd: str
    raw_user_input: str
    prompt: str
    prompt_kind: PromptKind
    default_prompt_path: str | None
    schedule_kind: ScheduleKind
    fixed_interval_seconds: int | None
    cron: str | None
    created_at: str
    updated_at: str
    expires_at: str
    next_run_at: str
    last_run_at: str | None
    status: TaskStatus
    run_count: int
    max_runs: int | None
    jitter_seed: str
    no_catch_up: bool
    approval_policy_snapshot: str | None
    sandbox_snapshot: str | None
    model_snapshot: str | None
    last_result_summary: str | None
    last_next_delay_reason: str | None
    failure_count: int
    lease_until: str | None
    cancel_requested: bool
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunResult:
    status: Literal["completed", "failed", "interrupted"]
    summary: str
    thread_id: str | None = None
    output: str | None = None


@dataclass(frozen=True)
class LoopRun:
    run_id: str
    task_id: str
    status: RunStatus
    started_at: str
    completed_at: str | None
    completion_source: str | None
    summary: str | None
    next_delay_seconds: int | None
    next_delay_reason: str | None
    failure_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
