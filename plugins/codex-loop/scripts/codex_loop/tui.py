from __future__ import annotations

import argparse
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .daemon import ensure_daemon_running, is_pid_running
from .runtime_state import clear_active_runtime, write_active_runtime


DEFAULT_HOST = "127.0.0.1"
DEFAULT_TOKEN_ENV = "CODEX_WS_TOKEN"


@dataclass(frozen=True)
class TuiRuntime:
    app_server_url: str
    runtime_dir: Path
    token_file: Path | None
    token_env: str
    db_path: Path
    app_server_pid_path: Path
    app_server_log_path: Path
    loopd_pid_path: Path
    loopd_log_path: Path


def find_free_port(host: str = DEFAULT_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _runtime_name(app_server_url: str) -> str:
    parsed = urlparse(app_server_url)
    host = parsed.hostname or DEFAULT_HOST
    port = parsed.port or 0
    return f"{host.replace('.', '-')}-{port}"


def build_runtime(args: argparse.Namespace) -> TuiRuntime:
    app_server_url = args.app_server
    if not app_server_url:
        port = args.port or find_free_port(args.host)
        app_server_url = f"ws://{args.host}:{port}"

    root = Path(args.runtime_dir or Path.home() / ".codex-loop" / "runtimes").expanduser().resolve()
    runtime_dir = root / _runtime_name(app_server_url)
    if args.token_file:
        token_file = Path(args.token_file).expanduser().resolve()
    elif args.app_server and os.environ.get(args.token_env):
        token_file = None
    elif args.app_server:
        token_file = None
    else:
        token_file = runtime_dir / "ws-token"
    db_path = Path(args.db).expanduser().resolve() if args.db else runtime_dir / "loop.sqlite3"
    return TuiRuntime(
        app_server_url=app_server_url,
        runtime_dir=runtime_dir,
        token_file=token_file,
        token_env=args.token_env,
        db_path=db_path,
        app_server_pid_path=runtime_dir / "app-server.pid",
        app_server_log_path=runtime_dir / "app-server.log",
        loopd_pid_path=runtime_dir / "loopd.pid",
        loopd_log_path=runtime_dir / "loopd.log",
    )


def ensure_token_file(path: Path, *, rotate: bool = False) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if rotate or not path.exists() or not path.read_text(encoding="utf-8").strip():
        token = secrets.token_urlsafe(32)
        path.write_text(token + "\n", encoding="utf-8")
        path.chmod(0o600)
        return token
    return path.read_text(encoding="utf-8").strip()


def build_runtime_env(base_env: dict[str, str], runtime: TuiRuntime, token: str) -> dict[str, str]:
    env = base_env.copy()
    if token:
        env[runtime.token_env] = token
    env["CODEX_LOOP_RUNTIME_DIR"] = str(runtime.runtime_dir)
    env["CODEX_LOOP_APP_SERVER"] = runtime.app_server_url
    env["CODEX_LOOP_APP_SERVER_TOKEN_ENV"] = runtime.token_env
    env.pop("CODEX_LOOP_APP_SERVER_TOKEN_FILE", None)
    if runtime.token_file is not None:
        env["CODEX_LOOP_APP_SERVER_TOKEN_FILE"] = str(runtime.token_file)
    env["CODEX_LOOP_RUNNER"] = "app-server"
    env["CODEX_LOOP_VISIBILITY_POLICY"] = "visible_only"
    env["CODEX_LOOP_DB"] = str(runtime.db_path)
    env["CODEX_LOOPD_PID_PATH"] = str(runtime.loopd_pid_path)
    env["CODEX_LOOPD_LOG_PATH"] = str(runtime.loopd_log_path)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return env


def build_app_server_command(codex_bin: str, runtime: TuiRuntime) -> list[str]:
    if runtime.token_file is None:
        raise ValueError("managed app-server startup requires a token file")
    return [
        codex_bin,
        "app-server",
        "--listen",
        runtime.app_server_url,
        "--ws-auth",
        "capability-token",
        "--ws-token-file",
        str(runtime.token_file),
    ]


def build_codex_tui_command(args: argparse.Namespace, runtime: TuiRuntime) -> list[str]:
    codex_args = list(args.codex_args or [])
    if codex_args and codex_args[0] == "--":
        codex_args = codex_args[1:]
    command = [
        args.codex_bin,
        "--remote",
        runtime.app_server_url,
        "--remote-auth-token-env",
        runtime.token_env,
    ]
    if args.cwd:
        command.extend(["--cd", args.cwd])
    command.extend(codex_args)
    return command


def _host_port(app_server_url: str) -> tuple[str, int]:
    parsed = urlparse(app_server_url)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError(f"app-server URL must be ws:// or wss://: {app_server_url}")
    if parsed.scheme == "wss":
        raise ValueError("codex-loop tui can only wait on local ws:// app-server URLs")
    host = parsed.hostname or DEFAULT_HOST
    if parsed.port is None:
        raise ValueError(f"app-server URL must include a port: {app_server_url}")
    return host, parsed.port


def wait_for_app_server(app_server_url: str, *, timeout_seconds: float) -> None:
    host, port = _host_port(app_server_url)
    deadline = time.monotonic() + timeout_seconds
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.1)
    raise TimeoutError(f"timed out waiting for {app_server_url}: {last_error}")


def _read_pid(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except Exception:
        return None


def _terminate_pid(pid: int, *, timeout_seconds: float = 5.0) -> None:
    if not is_pid_running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not is_pid_running(pid):
            return
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _stop_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def launch_tui(args: argparse.Namespace) -> int:
    runtime = build_runtime(args)
    runtime.runtime_dir.mkdir(parents=True, exist_ok=True)
    if runtime.token_file is None:
        token = os.environ.get(runtime.token_env)
        if not token:
            raise SystemExit(
                f"--app-server requires either --token-file or an existing {runtime.token_env} environment variable"
            )
    else:
        token = ensure_token_file(runtime.token_file, rotate=args.rotate_token)
    env = build_runtime_env(os.environ, runtime, token)
    write_active_runtime(env)

    app_server_proc: subprocess.Popen | None = None
    loopd_pid: int | None = None
    loopd_started = False
    try:
        if args.app_server:
            wait_for_app_server(runtime.app_server_url, timeout_seconds=args.app_server_timeout)
        else:
            with runtime.app_server_log_path.open("a", encoding="utf-8") as log_file:
                app_server_proc = subprocess.Popen(
                    build_app_server_command(args.codex_bin, runtime),
                    stdin=subprocess.DEVNULL,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                    env=env,
                )
            runtime.app_server_pid_path.write_text(f"{app_server_proc.pid}\n", encoding="utf-8")
            wait_for_app_server(runtime.app_server_url, timeout_seconds=args.app_server_timeout)

        if not args.no_loopd:
            status = ensure_daemon_running(
                db_path=runtime.db_path,
                pid_path=runtime.loopd_pid_path,
                log_path=runtime.loopd_log_path,
                runner="app-server",
                app_server=runtime.app_server_url,
                app_server_token_env=runtime.token_env,
                app_server_token_file=runtime.token_file,
                codex_bin=args.codex_bin,
                extra_env=env,
            )
            loopd_pid = status.pid
            loopd_started = status.started

        command = build_codex_tui_command(args, runtime)
        return subprocess.call(command, env=env)
    except KeyboardInterrupt:
        return 130
    finally:
        if not args.keep_running:
            clear_active_runtime(str(runtime.runtime_dir))
            _stop_process(app_server_proc)
            if loopd_pid is None and loopd_started:
                loopd_pid = _read_pid(runtime.loopd_pid_path)
            if loopd_started and loopd_pid is not None:
                _terminate_pid(loopd_pid)


def add_tui_parser(sub: argparse._SubParsersAction) -> None:
    tui = sub.add_parser("tui", help="Launch Codex TUI on a shared app-server runtime for visible loop turns.")
    tui.add_argument("--codex-bin", default=os.environ.get("CODEX_LOOP_CODEX_BIN", "codex"))
    tui.add_argument("--cwd", default=os.getcwd())
    tui.add_argument("--db", default=None)
    tui.add_argument("--runtime-dir", default=None)
    tui.add_argument("--app-server", default=None, help="Use an already-running app-server websocket URL.")
    tui.add_argument("--host", default=DEFAULT_HOST)
    tui.add_argument("--port", type=int, default=None)
    tui.add_argument("--token-file", default=None)
    tui.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    tui.add_argument("--rotate-token", action="store_true")
    tui.add_argument("--app-server-timeout", type=float, default=15.0)
    tui.add_argument("--no-loopd", action="store_true")
    tui.add_argument("--keep-running", action="store_true")
    tui.add_argument("codex_args", nargs=argparse.REMAINDER, help="Extra args passed to codex after --.")
    tui.set_defaults(func=launch_tui)
