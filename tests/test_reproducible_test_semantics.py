from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReproducibleTestSemanticsTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_worker_reads_frozen_source_text_before_inventories(self):
        text = self.read(".opencode/agents/analysis-worker.md")
        self.assertIn("必须完整读取 `text`", text)
        self.assertIn("function_inventory`、`branch_inventory`、code map 只能作为索引", text)
        self.assertIn("line_start + 文本内行偏移", text)
        self.assertNotIn("sources[].lines[].line", text)

    def test_risk_contract_is_reproduce_observe_exclude(self):
        worker = self.read(".opencode/agents/analysis-worker.md")
        risk = self.read(".opencode/skills/risk-card/SKILL.md")
        auditor = self.read(".opencode/agents/auditor.md")
        for text in (worker, risk, auditor):
            self.assertIn("复现条件", text)
            self.assertIn("排除条件", text)
        self.assertIn("TSan、ASan、UBSan、Valgrind", auditor)
        self.assertIn("不得以“攻击者”“恶意用户”“利用漏洞”", worker)
        self.assertIn("Sanitizer 只能辅助", risk)

    def test_scenarios_require_source_backed_parameter_matrix(self):
        worker = self.read(".opencode/agents/analysis-worker.md")
        skill = self.read(".opencode/skills/storage-iscsi/SKILL.md")
        checklist = self.read(".opencode/skills/storage-iscsi/references/analysis-checklist.md")
        auditor = self.read(".opencode/agents/auditor.md")
        self.assertIn("参数维度：<名称>=<值1>|<值2>|...", worker)
        self.assertIn("每个保留组合必须至少对应一个独立 `test_cases` 项", worker)
        self.assertIn("认证方向", skill)
        self.assertIn("DH/group/key/secret length", skill)
        self.assertIn("每个保留组合必须映射到至少一个明确的测试用例", checklist)
        self.assertIn("只有一个“单向 CHAP 成功”用例不能证明该场景覆盖完成", auditor)

    def test_translation_capability_rejects_happy_path_only(self):
        text = self.read("core/capabilities/test-semantic-translation.md")
        self.assertIn("先提取参数空间，再写用例", text)
        self.assertIn("默认展开有效全组合", text)
        self.assertIn("不在 `complete` 分析中只输出 Happy Path", text)


if __name__ == "__main__":
    unittest.main()
