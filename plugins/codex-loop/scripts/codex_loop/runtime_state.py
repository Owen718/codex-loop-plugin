from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping


STATE_KEYS = (
    "CODEX_LOOP_RUNTIME_DIR",
    "CODEX_LOOP_APP_SERVER",
    "CODEX_LOOP_APP_SERVER_TOKEN_ENV",
    "CODEX_LOOP_APP_SERVER_TOKEN_FILE",
    "CODEX_LOOP_RUNNER",
    "CODEX_LOOP_VISIBILITY_POLICY",
    "CODEX_LOOP_DB",
    "CODEX_LOOPD_PID_PATH",
    "CODEX_LOOPD_LOG_PATH",
)


def state_path() -> Path:
    configured = os.environ.get("CODEX_LOOP_ACTIVE_RUNTIME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex-loop" / "active-runtime.json"


def runtime_state_from_env(env: Mapping[str, str] = os.environ) -> dict[str, str]:
    return {key: env[key] for key in STATE_KEYS if env.get(key)}


def write_active_runtime(env: Mapping[str, str]) -> None:
    state = {
        "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "env": runtime_state_from_env(env),
    }
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    path.chmod(0o600)


def read_active_runtime() -> dict[str, str]:
    try:
        raw = json.loads(state_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    env = raw.get("env") if isinstance(raw, dict) else None
    if not isinstance(env, dict):
        return {}
    return {key: value for key, value in env.items() if key in STATE_KEYS and isinstance(value, str) and value}


def apply_active_runtime_to_env(env: dict[str, str] = os.environ) -> dict[str, str]:
    state = read_active_runtime()
    for key, value in state.items():
        env[key] = value
    return state


def active_runtime_value(key: str) -> str | None:
    if os.environ.get(key):
        return os.environ[key]
    return read_active_runtime().get(key)


def clear_active_runtime(expected_runtime_dir: str | None = None) -> None:
    path = state_path()
    if expected_runtime_dir is not None:
        state = read_active_runtime()
        if state.get("CODEX_LOOP_RUNTIME_DIR") != expected_runtime_dir:
            return
    try:
        path.unlink()
    except FileNotFoundError:
        return
