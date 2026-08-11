from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SemanticPlanContractHardeningTests(unittest.TestCase):
    def test_worker_uses_exclusive_plan_contract(self) -> None:
        text = (ROOT / ".opencode/agents/analysis-worker.md").read_text(encoding="utf-8")
        for phrase in (
            "top_keys` 是计划顶层**唯一允许**的字段集合",
            "禁止额外增加 `generated_at`",
            "`unit_id` 固定使用 `U` + 2~3 位数字",
            "`priority` 只能是 `P0`、`P1`、`P2`",
            "单元总数不得超过 64",
            "complete 模式下计划和每个 unit 的 `depth_limitations` 都必须为空",
            "不得读取 `runtime/*.py`",
            "完整替换计划",
        ):
            self.assertIn(phrase, text)

    def test_module_plan_failure_cannot_be_bypassed_or_hand_edited(self) -> None:
        text = (ROOT / ".opencode/commands/module-analysis.md").read_text(encoding="utf-8")
        for phrase in (
            "计划校验失败的唯一恢复方式",
            "原始 validator error",
            "主 Agent 不得手工修改 JSON",
            "不得 grep/read `runtime/semantic_analysis.py`",
            "不得跳过 `stage-semantic-plan-v2`",
            "最多允许两次 worker 修正",
            "未冻结合法 plan 时不存在可执行语义单元",
        ):
            self.assertIn(phrase, text)

    def test_semantic_cli_keeps_runtime_source_text_shape(self) -> None:
        text = (ROOT / "tooling/pangea_cli/semanticctl.py").read_text(encoding="utf-8")
        self.assertNotIn('source.pop("text")', text)
        self.assertNotIn('source["lines"] =', text)
        self.assertIn("必须完整读取每个 sources[].text", text)
        self.assertIn("line_start + text 内行偏移", text)

    def test_worker_and_command_agree_on_source_shape(self) -> None:
        worker = (ROOT / ".opencode/agents/analysis-worker.md").read_text(encoding="utf-8")
        command = (ROOT / ".opencode/commands/module-analysis.md").read_text(encoding="utf-8")
        for text in (worker, command):
            self.assertIn("sources[].text", text)
            self.assertIn("line_start", text)
        self.assertIn("不得再假设存在 `sources[].lines[]`", command)


if __name__ == "__main__":
    unittest.main()
