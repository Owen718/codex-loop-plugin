from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_loop.daemon import daemon_status, ensure_daemon_running


class DaemonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "loop.sqlite3"
        self.pid_path = self.root / "loopd.pid"
        self.log_path = self.root / "loopd.log"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ensure_daemon_running_starts_codex_mcp_daemon(self) -> None:
        with mock.patch("codex_loop.daemon.is_pid_running", return_value=False):
            with mock.patch("codex_loop.daemon.subprocess.Popen") as popen:
                popen.return_value.pid = 12345

                status = ensure_daemon_running(
                    db_path=self.db,
                    pid_path=self.pid_path,
                    log_path=self.log_path,
                    runner="codex-mcp",
                    codex_bin="codex-test",
                )

        self.assertTrue(status.enabled)
        self.assertTrue(status.running)
        self.assertTrue(status.started)
        self.assertEqual(status.pid, 12345)
        self.assertEqual(self.pid_path.read_text().strip(), "12345")

        args = popen.call_args.args[0]
        self.assertIn("--db", args)
        self.assertIn(str(self.db), args)
        self.assertIn("--runner", args)
        self.assertIn("codex-mcp", args)
        self.assertIn("--codex-bin", args)
        self.assertIn("codex-test", args)

    def test_ensure_daemon_running_reuses_existing_pid(self) -> None:
        self.pid_path.write_text("6789\n")
        with mock.patch("codex_loop.daemon.is_pid_running", return_value=True):
            with mock.patch("codex_loop.daemon.subprocess.Popen") as popen:
                status = ensure_daemon_running(
                    db_path=self.db,
                    pid_path=self.pid_path,
                    log_path=self.log_path,
                )

        self.assertTrue(status.running)
        self.assertFalse(status.started)
        self.assertEqual(status.pid, 6789)
        popen.assert_not_called()

    def test_daemon_status_reports_stale_pid(self) -> None:
        self.pid_path.write_text("6789\n")
        with mock.patch("codex_loop.daemon.is_pid_running", return_value=False):
            status = daemon_status(pid_path=self.pid_path)

        self.assertFalse(status.running)
        self.assertEqual(status.pid, 6789)
        self.assertIn("stale", status.reason)


if __name__ == "__main__":
    unittest.main()
