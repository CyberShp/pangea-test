from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".opencode" / "agents"
COMMANDS = ROOT / ".opencode" / "commands"


class PrimaryAgentIdentityTests(unittest.TestCase):
    def test_primary_agent_is_pangea_test(self) -> None:
        primary = AGENTS / "pangea-test.md"
        self.assertTrue(primary.exists())
        self.assertFalse((AGENTS / "dispatcher.md").exists())
        text = primary.read_text(encoding="utf-8")
        self.assertIn("mode: primary", text)

    def test_all_commands_bind_to_pangea_test(self) -> None:
        command_files = sorted(COMMANDS.glob("*.md"))
        self.assertTrue(command_files)
        for path in command_files:
            text = path.read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^agent: pangea-test$")
            self.assertNotRegex(text, r"(?m)^agent: dispatcher$")

    def test_active_opencode_identity_has_no_legacy_name(self) -> None:
        # Doctor intentionally mentions the legacy filename so it can verify that
        # the old Agent no longer exists. Only user-facing active configuration
        # and the README must be free of the old identity.
        paths = [ROOT / "README.md"]
        paths.extend(sorted((ROOT / ".opencode").rglob("*.md")))
        pattern = re.compile(r"\b[Dd]ispatcher\b")
        offenders = [str(path.relative_to(ROOT)) for path in paths if pattern.search(path.read_text(encoding="utf-8"))]
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
