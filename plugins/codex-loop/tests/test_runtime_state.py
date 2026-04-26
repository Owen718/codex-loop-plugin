from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from codex_loop.runtime_state import apply_active_runtime_to_env, clear_active_runtime, read_active_runtime, write_active_runtime


class RuntimeStateTests(unittest.TestCase):
    def test_write_read_apply_and_clear_active_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "active-runtime.json"
            runtime_dir = Path(tmp) / "runtime"
            source_env = {
                "CODEX_LOOP_RUNTIME_DIR": str(runtime_dir),
                "CODEX_LOOP_DB": str(runtime_dir / "loop.sqlite3"),
                "CODEX_LOOP_APP_SERVER": "ws://127.0.0.1:4555",
                "CODEX_LOOP_APP_SERVER_TOKEN_FILE": str(runtime_dir / "ws-token"),
            }
            target_env: dict[str, str] = {}

            with mock.patch.dict("os.environ", {"CODEX_LOOP_ACTIVE_RUNTIME": str(state_path)}, clear=True):
                write_active_runtime(source_env)
                self.assertEqual(read_active_runtime()["CODEX_LOOP_DB"], source_env["CODEX_LOOP_DB"])
                apply_active_runtime_to_env(target_env)
                self.assertEqual(target_env["CODEX_LOOP_APP_SERVER"], "ws://127.0.0.1:4555")
                clear_active_runtime(str(runtime_dir))

            self.assertFalse(state_path.exists())


if __name__ == "__main__":
    unittest.main()
