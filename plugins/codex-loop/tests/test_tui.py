from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_loop.cli import build_arg_parser
from codex_loop.tui import build_codex_tui_command, build_runtime, build_runtime_env, ensure_token_file


class TuiLauncherTests(unittest.TestCase):
    def test_build_runtime_env_wires_visible_loop_app_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                app_server="ws://127.0.0.1:4555",
                host="127.0.0.1",
                port=None,
                runtime_dir=tmp,
                token_file=None,
                token_env="CODEX_WS_TOKEN",
                db=str(Path(tmp) / "loop.sqlite3"),
            )
            runtime = build_runtime(args)
            token = ensure_token_file(runtime.token_file)
            env = build_runtime_env({}, runtime, token)

        self.assertEqual(env["CODEX_LOOP_APP_SERVER"], "ws://127.0.0.1:4555")
        self.assertEqual(env["CODEX_LOOP_APP_SERVER_TOKEN_ENV"], "CODEX_WS_TOKEN")
        self.assertEqual(env["CODEX_LOOP_RUNNER"], "app-server")
        self.assertEqual(env["CODEX_LOOP_VISIBILITY_POLICY"], "visible_only")
        self.assertEqual(env["CODEX_WS_TOKEN"], token)
        self.assertTrue(env["CODEX_LOOPD_PID_PATH"].endswith("loopd.pid"))
        self.assertTrue(env["CODEX_LOOPD_LOG_PATH"].endswith("loopd.log"))

    def test_codex_tui_command_uses_remote_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                app_server="ws://127.0.0.1:4555",
                host="127.0.0.1",
                port=None,
                runtime_dir=tmp,
                token_file=None,
                token_env="CODEX_WS_TOKEN",
                db=None,
                codex_bin="codex-test",
                cwd="/repo",
                codex_args=["--", "--model", "gpt-test"],
            )
            runtime = build_runtime(args)
            command = build_codex_tui_command(args, runtime)

        self.assertEqual(
            command,
            [
                "codex-test",
                "--remote",
                "ws://127.0.0.1:4555",
                "--remote-auth-token-env",
                "CODEX_WS_TOKEN",
                "--cd",
                "/repo",
                "--model",
                "gpt-test",
            ],
        )

    def test_cli_has_tui_subcommand(self) -> None:
        parser = build_arg_parser()
        args = parser.parse_args(["tui", "--app-server", "ws://127.0.0.1:4555", "--no-loopd", "--", "--model", "x"])
        self.assertEqual(args.command, "tui")
        self.assertEqual(args.app_server, "ws://127.0.0.1:4555")
        self.assertEqual(args.codex_args, ["--", "--model", "x"])

    def test_launch_tui_against_existing_app_server_calls_remote_codex(self) -> None:
        parser = build_arg_parser()
        with tempfile.TemporaryDirectory() as tmp:
            args = parser.parse_args(
                [
                    "tui",
                    "--app-server",
                    "ws://127.0.0.1:4555",
                    "--runtime-dir",
                    tmp,
                    "--db",
                    str(Path(tmp) / "loop.sqlite3"),
                    "--no-loopd",
                    "--codex-bin",
                    "codex-test",
                ]
            )
            with mock.patch.dict("os.environ", {"CODEX_WS_TOKEN": "existing-token"}, clear=False):
                with mock.patch("codex_loop.tui.wait_for_app_server") as wait:
                    with mock.patch("codex_loop.tui.subprocess.call", return_value=0) as call:
                        status = args.func(args)

        self.assertEqual(status, 0)
        wait.assert_called_once()
        command = call.call_args.args[0]
        self.assertEqual(command[:5], ["codex-test", "--remote", "ws://127.0.0.1:4555", "--remote-auth-token-env", "CODEX_WS_TOKEN"])
        env = call.call_args.kwargs["env"]
        self.assertEqual(env["CODEX_LOOP_APP_SERVER"], "ws://127.0.0.1:4555")
        self.assertEqual(env["CODEX_LOOP_RUNNER"], "app-server")
        self.assertEqual(env["CODEX_WS_TOKEN"], "existing-token")


if __name__ == "__main__":
    unittest.main()
