from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


BUILTIN_MAINTENANCE_PROMPT = """You are running a scheduled maintenance loop for this Codex thread.

Work in this order:
1. Continue unfinished work that is already authorized in the conversation.
2. If this branch has an associated PR, check review comments, CI failures, merge conflicts, or obvious follow-up work.
3. If nothing is pending, run a lightweight health check: git status, relevant tests or lints if cheap, and summarize that the workspace is quiet.

Do not start unrelated new features.
Do not push, delete, publish, force-push, or perform irreversible actions unless the existing conversation already clearly authorized that action.
If blocked, say what is needed and pause or lengthen the next interval.
"""


@dataclass(frozen=True)
class ResolvedPrompt:
    prompt: str
    prompt_kind: str
    default_prompt_path: str | None = None


def _candidate_repo_prompts(cwd: Path) -> list[Path]:
    prompts: list[Path] = []
    current = cwd.resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".codex" / "loop.md"
        prompts.append(candidate)
        if (parent / ".git").exists():
            break
    return prompts


def resolve_default_prompt(cwd: str | None = None) -> tuple[str, str | None]:
    start = Path(cwd or os.getcwd())
    for candidate in _candidate_repo_prompts(start):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8"), str(candidate)

    home_candidate = Path.home() / ".codex" / "loop.md"
    if home_candidate.is_file():
        return home_candidate.read_text(encoding="utf-8"), str(home_candidate)

    return BUILTIN_MAINTENANCE_PROMPT, None


def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    match = re.match(r"(?s)^---\s*\n.*?\n---\s*\n(.*)$", text)
    return match.group(1) if match else text


def _expand_prompt_command(command: str, cwd: str | None = None) -> str | None:
    if not command.startswith("/prompts:"):
        return None
    name_and_args = command[len("/prompts:") :].strip()
    if not name_and_args:
        return None
    name, _, args = name_and_args.partition(" ")
    if not re.match(r"^[A-Za-z0-9_.-]+$", name):
        return None

    candidates = [
        Path(cwd or os.getcwd()) / ".codex" / "prompts" / f"{name}.md",
        Path.home() / ".codex" / "prompts" / f"{name}.md",
    ]
    for candidate in candidates:
        if candidate.is_file():
            body = _strip_frontmatter(candidate.read_text(encoding="utf-8"))
            return body.replace("$ARGUMENTS", args)
    return None


def _review_adapter(command: str) -> str | None:
    if not command.startswith("/review"):
        return None
    suffix = command[len("/review") :].strip()
    target = suffix or "the current workspace changes"
    return f"""Review {target}.

Prioritize bugs, behavioral regressions, security or safety risks, and missing tests. Report findings first with file and line references where possible. If there are no findings, say that clearly and mention remaining test gaps.
"""


def resolve_prompt(raw_prompt: str, cwd: str | None = None) -> ResolvedPrompt:
    raw_prompt = (raw_prompt or "").strip()
    if not raw_prompt:
        prompt, path = resolve_default_prompt(cwd)
        return ResolvedPrompt(prompt=prompt, prompt_kind="default", default_prompt_path=path)

    if raw_prompt.startswith("/"):
        expanded = _expand_prompt_command(raw_prompt, cwd=cwd) or _review_adapter(raw_prompt)
        if expanded is not None:
            return ResolvedPrompt(prompt=expanded, prompt_kind="command")
        prompt = f"""This loop was created from a slash-style command that Codex Loop cannot safely dispatch outside the TUI:

{raw_prompt}

If the current Codex environment supports this command, perform the equivalent work. Otherwise, explain that an adapter is needed for this command and pause the loop.
"""
        return ResolvedPrompt(prompt=prompt, prompt_kind="command")

    return ResolvedPrompt(prompt=raw_prompt, prompt_kind="explicit")
