from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .runtime_state import active_runtime_value


RUNTIME_DIR = Path.home() / ".codex-loop"
DEFAULT_PID_PATH = RUNTIME_DIR / "loopd.pid"
DEFAULT_LOG_PATH = RUNTIME_DIR / "loopd.log"
STARTUP_GRACE_SECONDS = 0.05


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
    if _linux_proc_state(pid) == "Z":
        return False
    return True


def _linux_proc_state(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None
    end = raw.rfind(")")
    if end == -1:
        return None
    fields = raw[end + 1 :].strip().split()
    return fields[0] if fields else None


def _read_pid(pid_path: Path) -> int | None:
    try:
        value = pid_path.read_text().strip()
    except FileNotFoundError:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _runtime_name(app_server_url: str) -> str | None:
    parsed = urlparse(app_server_url)
    if parsed.scheme not in {"ws", "wss"}:
        return None
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if port is None:
        return None
    return f"{host.replace('.', '-')}-{port}"


def _runtime_default_path(filename: str, *, app_server: str | None = None) -> Path | None:
    runtime_dir = active_runtime_value("CODEX_LOOP_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir).expanduser() / filename
    app_server = app_server or active_runtime_value("CODEX_LOOP_APP_SERVER")
    if not app_server:
        return None
    runtime_name = _runtime_name(app_server)
    if not runtime_name:
        return None
    return RUNTIME_DIR / "runtimes" / runtime_name / filename


def default_pid_path() -> Path:
    if os.environ.get("CODEX_LOOPD_PID_PATH"):
        return Path(os.environ["CODEX_LOOPD_PID_PATH"]).expanduser()
    return _runtime_default_path("loopd.pid") or DEFAULT_PID_PATH


def default_log_path() -> Path:
    if os.environ.get("CODEX_LOOPD_LOG_PATH"):
        return Path(os.environ["CODEX_LOOPD_LOG_PATH"]).expanduser()
    return _runtime_default_path("loopd.log") or DEFAULT_LOG_PATH


def daemon_status(*, pid_path: str | Path | None = None, log_path: str | Path | None = None) -> DaemonStatus:
    resolved_pid_path = Path(pid_path).expanduser() if pid_path is not None else default_pid_path()
    resolved_log_path = Path(log_path).expanduser() if log_path is not None else default_log_path()
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
    app_server: str | None = None,
    app_server_token_env: str | None = None,
    app_server_token_file: str | Path | None = None,
    codex_bin: str | None = None,
    extra_env: dict[str, str] | None = None,
) -> DaemonStatus:
    resolved_pid_path = Path(pid_path).expanduser() if pid_path is not None else default_pid_path()
    resolved_log_path = Path(log_path).expanduser() if log_path is not None else default_log_path()

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
    if app_server:
        command.extend(["--app-server", app_server])
    if app_server_token_env:
        command.extend(["--app-server-token-env", app_server_token_env])
    if runner == "app-server" and app_server_token_file is None:
        fallback_token_file = _runtime_default_path("ws-token", app_server=app_server)
        if fallback_token_file is not None and fallback_token_file.is_file():
            app_server_token_file = fallback_token_file
    if app_server_token_file:
        command.extend(["--app-server-token-file", str(Path(app_server_token_file).expanduser())])
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
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
    time.sleep(STARTUP_GRACE_SECONDS)
    returncode = proc.poll()
    if returncode is not None:
        return DaemonStatus(
            enabled=True,
            running=False,
            started=True,
            pid=proc.pid,
            reason=f"daemon exited immediately with code {returncode}",
            pid_path=str(resolved_pid_path),
            log_path=str(resolved_log_path),
            command=command,
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
