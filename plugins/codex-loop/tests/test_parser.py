from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_loop.parser import parse_duration, parse_loop_args


class ParserTests(unittest.TestCase):
    def test_parse_duration_rounds_seconds_to_minute(self) -> None:
        self.assertEqual(parse_duration("30s").seconds, 60)
        self.assertEqual(parse_duration("5m").seconds, 300)
        self.assertEqual(parse_duration("2h").seconds, 7200)

    def test_fixed_prompt(self) -> None:
        parsed = parse_loop_args("5m check deploy", cwd=".")
        self.assertEqual(parsed.action, "create")
        self.assertEqual(parsed.schedule_kind, "fixed")
        self.assertEqual(parsed.fixed_interval_seconds, 300)
        self.assertEqual(parsed.prompt, "check deploy")
        self.assertEqual(parsed.prompt_kind, "explicit")

    def test_dynamic_prompt(self) -> None:
        parsed = parse_loop_args("check deploy", cwd=".")
        self.assertEqual(parsed.schedule_kind, "dynamic")
        self.assertIsNone(parsed.fixed_interval_seconds)
        self.assertEqual(parsed.prompt, "check deploy")

    def test_default_prompt_from_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".codex").mkdir()
            (root / ".codex" / "loop.md").write_text("local maintenance", encoding="utf-8")
            parsed = parse_loop_args("", cwd=str(root))
        self.assertEqual(parsed.prompt, "local maintenance")
        self.assertEqual(parsed.prompt_kind, "default")
        self.assertTrue(parsed.default_prompt_path.endswith(".codex/loop.md"))

    def test_management_actions(self) -> None:
        parsed = parse_loop_args("cancel a1b2c3d4")
        self.assertEqual(parsed.action, "cancel")
        self.assertEqual(parsed.target_id, "a1b2c3d4")

        parsed = parse_loop_args("status")
        self.assertEqual(parsed.action, "list")

    def test_prompt_command_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prompts = root / ".codex" / "prompts"
            prompts.mkdir(parents=True)
            (prompts / "foo.md").write_text("---\ndescription: x\n---\nHello $ARGUMENTS", encoding="utf-8")
            parsed = parse_loop_args("10m /prompts:foo world", cwd=str(root))
        self.assertEqual(parsed.prompt_kind, "command")
        self.assertEqual(parsed.prompt, "Hello world")


if __name__ == "__main__":
    unittest.main()
