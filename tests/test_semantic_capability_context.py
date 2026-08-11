from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout

from runtime import semantic_analysis
from tests.test_semantic_analysis import SemanticAnalysisTests
from tooling.pangea_cli import semanticctl


class SemanticCapabilityContextTests(unittest.TestCase):
    def test_unit_context_injects_shared_and_only_assigned_dfx_capabilities(self) -> None:
        helper = SemanticAnalysisTests()
        holder, root, run = helper.run_fixture()
        try:
            plan = helper.plan()
            plan["units"] = [
                {
                    **plan["units"][0],
                    "source_ranges": [{"path": "driver.c", "line_start": 1, "line_end": 2}],
                    "dfx": ["功能与状态", "资源与规格"],
                },
                {
                    **plan["units"][0],
                    "unit_id": "U02",
                    "title": "成功返回路径",
                    "source_ranges": [{"path": "driver.c", "line_start": 3, "line_end": 4}],
                    "focus": ["dfx"],
                    "dfx": ["性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性"],
                },
            ]
            semantic_analysis.stage_plan(root, "semantic-run", plan)
            with redirect_stdout(io.StringIO()):
                semanticctl.unit_context(argparse.Namespace(
                    root=str(root), run_id="semantic-run", unit_id="U01"
                ))

            context = json.loads(
                (run / "internal/semantic-analysis/contexts/U01.json").read_text(encoding="utf-8")
            )
            paths = [row["path"] for row in context["capabilities"]]
            self.assertEqual([
                "core/capabilities/shared-cpp-evidence.md",
                "core/capabilities/test-semantic-translation.md",
                "core/capabilities/conditional-knowledge.md",
                "core/capabilities/dfx/功能与状态.md",
                "core/capabilities/dfx/资源与规格.md",
            ], paths)
            self.assertNotIn("core/capabilities/dfx/性能与压力.md", paths)
            self.assertIn("厂商方法仅在当前源码存在对应", context["instructions"])
        finally:
            holder.cleanup()


if __name__ == "__main__":
    unittest.main()
