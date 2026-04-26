from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


RUNTIME_DIR = Path.home() / ".codex-loop"
DEFAULT_PID_PATH = RUNTIME_DIR / "loopd.pid"
DEFAULT_LOG_PATH = RUNTIME_DIR / "loopd.log"


@dataclass
class DaemonStatus:
    enabled: bool
    running: bool
    started: bool = False
    pid: int | None = None
    reason: str | None = None
    pid_path: str | None = None
    log_path: str | None = None
    command: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "started": self.started,
            "pid": self.pid,
            "reason": self.reason,
            "pid_path": self.pid_path,
            "log_path": self.log_path,
            "command": self.command,
        }


def autostart_enabled() -> bool:
    value = os.environ.get("CODEX_LOOP_AUTOSTART", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid(pid_path: Path) -> int | None:
    try:
        value = pid_path.read_text().strip()
    except FileNotFoundError:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def daemon_status(*, pid_path: str | Path | None = None, log_path: str | Path | None = None) -> DaemonStatus:
    resolved_pid_path = Path(pid_path or DEFAULT_PID_PATH).expanduser()
    resolved_log_path = Path(log_path or DEFAULT_LOG_PATH).expanduser()
    pid = _read_pid(resolved_pid_path)
    if pid is None:
        reason = "no pid file" if not resolved_pid_path.exists() else "invalid pid file"
        return DaemonStatus(
            enabled=autostart_enabled(),
            running=False,
            pid=None,
            reason=reason,
            pid_path=str(resolved_pid_path),
            log_path=str(resolved_log_path),
        )
    running = is_pid_running(pid)
    return DaemonStatus(
        enabled=autostart_enabled(),
        running=running,
        pid=pid,
        reason="running" if running else f"stale pid {pid}",
        pid_path=str(resolved_pid_path),
        log_path=str(resolved_log_path),
    )


def _loopd_script() -> Path:
    return Path(__file__).resolve().parents[1] / "codex-loopd"


def ensure_daemon_running(
    *,
    db_path: str | Path,
    pid_path: str | Path | None = None,
    log_path: str | Path | None = None,
    runner: str = "codex-mcp",
    codex_bin: str | None = None,
) -> DaemonStatus:
    resolved_pid_path = Path(pid_path or DEFAULT_PID_PATH).expanduser()
    resolved_log_path = Path(log_path or DEFAULT_LOG_PATH).expanduser()

    if not autostart_enabled():
        status = daemon_status(pid_path=resolved_pid_path, log_path=resolved_log_path)
        status.enabled = False
        status.reason = "autostart disabled"
        return status

    existing = daemon_status(pid_path=resolved_pid_path, log_path=resolved_log_path)
    if existing.running:
        return existing

    resolved_pid_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(_loopd_script()),
        "--db",
        str(Path(db_path).expanduser()),
        "--runner",
        runner,
        "--codex-bin",
        codex_bin or os.environ.get("CODEX_LOOP_CODEX_BIN", "codex"),
    ]
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    with resolved_log_path.open("a", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    resolved_pid_path.write_text(f"{proc.pid}\n")
    return DaemonStatus(
        enabled=True,
        running=True,
        started=True,
        pid=proc.pid,
        reason="started",
        pid_path=str(resolved_pid_path),
        log_path=str(resolved_log_path),
        command=command,
    )
