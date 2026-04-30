from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class McpConfigTests(unittest.TestCase):
    def test_mcp_entrypoint_selects_highest_valid_plugin_version(self) -> None:
        plugin_root = Path(__file__).resolve().parents[1]
        config = json.loads((plugin_root / ".mcp.json").read_text(encoding="utf-8"))
        args = config["mcpServers"]["codex_loop"]["args"]
        self.assertEqual(args[0], "-c")

        with tempfile.TemporaryDirectory() as tmp:
            cache_root = Path(tmp) / ".codex" / "plugins" / "cache" / "codex-loop-plugin" / "codex-loop"
            self._write_fake_plugin(cache_root / "0.1.4", "0.1.4", "print('0.1.4')\n")
            self._write_fake_plugin(cache_root / "0.1.10", "0.1.10", "print('0.1.10')\n")
            self._write_broken_plugin(cache_root / "0.1.2", "0.1.2")
            os.utime(cache_root / "0.1.2", (4_000_000_000, 4_000_000_000))
            os.utime(cache_root / "0.1.4", (3_000_000_000, 3_000_000_000))
            os.utime(cache_root / "0.1.10", (2_000_000_000, 2_000_000_000))

            env = os.environ.copy()
            env["HOME"] = tmp
            proc = subprocess.run(
                [sys.executable, *args],
                text=True,
                capture_output=True,
                check=False,
                env=env,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0.1.10")

    def _write_fake_plugin(self, root: Path, version: str, script: str) -> None:
        self._write_broken_plugin(root, version)
        scripts = root / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "codex-loop-mcp").write_text(script, encoding="utf-8")

    def _write_broken_plugin(self, root: Path, version: str) -> None:
        manifest = root / ".codex-plugin"
        manifest.mkdir(parents=True, exist_ok=True)
        (manifest / "plugin.json").write_text(json.dumps({"version": version}), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
