from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from .models import ParsedLoop
from .prompts import ResolvedPrompt, resolve_prompt


_DURATION_RE = re.compile(
    r"^(?P<n>\d+)\s*(?P<unit>s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$",
    re.IGNORECASE,
)
_MANAGEMENT = {
    "list": "list",
    "ls": "list",
    "status": "list",
    "cancel": "cancel",
    "delete": "delete",
    "del": "delete",
    "rm": "delete",
    "pause": "pause",
    "resume": "resume",
}


@dataclass(frozen=True)
class ParsedDuration:
    seconds: int
    source: str


def parse_duration(token: str) -> ParsedDuration | None:
    match = _DURATION_RE.match(token.strip())
    if not match:
        return None
    amount = int(match.group("n"))
    unit = match.group("unit").lower()
    if unit.startswith("s"):
        seconds = amount
    elif unit.startswith("m"):
        seconds = amount * 60
    elif unit.startswith("h"):
        seconds = amount * 60 * 60
    else:
        seconds = amount * 24 * 60 * 60
    return ParsedDuration(seconds=max(60, seconds), source=token)


def _split_once(raw: str) -> tuple[str, str]:
    stripped = raw.strip()
    if not stripped:
        return "", ""
    lexer = shlex.shlex(stripped, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        first = next(lexer)
    except StopIteration:
        return "", ""
    first_end = stripped.find(first) + len(first)
    return first, stripped[first_end:].lstrip()


def parse_loop_args(raw: str, *, cwd: str | None = None) -> ParsedLoop:
    raw = (raw or "").strip()
    first, rest = _split_once(raw)
    lower = first.lower()

    if lower in _MANAGEMENT:
        action = _MANAGEMENT[lower]
        target_id = rest.split()[0] if rest.strip() else None
        return ParsedLoop(action=action, raw_user_input=raw, target_id=target_id)

    duration = parse_duration(first) if first else None
    if duration:
        schedule_kind = "fixed"
        fixed_interval_seconds = duration.seconds
        prompt_raw = rest
    else:
        schedule_kind = "dynamic"
        fixed_interval_seconds = None
        prompt_raw = raw

    resolved: ResolvedPrompt = resolve_prompt(prompt_raw, cwd=cwd)
    return ParsedLoop(
        action="create",
        raw_user_input=raw,
        prompt=resolved.prompt,
        prompt_kind=resolved.prompt_kind,
        default_prompt_path=resolved.default_prompt_path,
        schedule_kind=schedule_kind,
        fixed_interval_seconds=fixed_interval_seconds,
    )
