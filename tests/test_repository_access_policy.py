from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RepositoryAccessPolicyTests(unittest.TestCase):
    def test_initial_and_primary_separate_access_from_update(self) -> None:
        initial = (ROOT / ".opencode" / "commands" / "initial.md").read_text(encoding="utf-8")
        primary = (ROOT / ".opencode" / "agents" / "pangea-test.md").read_text(encoding="utf-8")
        combined = initial + "\n" + primary
        for phrase in (
            "access_status: ready",
            "update_status",
            "index_eligible",
            "snapshot_eligible",
            "不得把“为保护用户工作区而不自动 pull”描述成“没有权限访问仓库”",
        ):
            self.assertIn(phrase, combined)
        self.assertIn("索引是否成功只以 `index all` 自身记录为准", initial)


if __name__ == "__main__":
    unittest.main()
