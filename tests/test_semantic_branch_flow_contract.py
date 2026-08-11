from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime import analysis_reporting, semantic_analysis
from tests.test_contract_lifecycle import ContractLifecycleTests


class SemanticBranchFlowContractTests(unittest.TestCase):
    def prepare(self):
        holder = tempfile.TemporaryDirectory(); root = Path(holder.name).resolve()
        helper = ContractLifecycleTests(); helper.prepare(root)
        repo = root / "pangea-data/repositories/driver"
        (repo / "driver.c").write_text(
            "int entry(int ready, int mode) {\n"
            "    if (!ready) return -1;\n"
            "    if (mode == 0) return 1;\n"
            "    return 0;\n}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "driver.c"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid",
                        "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "branch-flow"], check=True)
        helper.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "入口",
                   "--repository", "driver", "--source-scope", "driver=driver.c",
                   "--contract-id", "branch-flow", "--analysis-depth", "complete")
        helper.cli(root, "confirm-contract-v2", "--contract-id", "branch-flow", "--revision", "1",
                   "--source", "user_reply", "--materials-status", "confirmed_none")
        helper.cli(root, "activate-contract-v2", "--contract-id", "branch-flow", "--run-id", "branch-flow-run")
        plan = {"artifact_type": "semantic_analysis_plan", "schema_version": "1.1", "run_id": "branch-flow-run",
                "target": "入口", "analysis_depth": "complete", "units": [{"unit_id": "U01", "title": "入口流程",
                "repository": "driver", "priority": "P0", "source_ranges": [{"path": "driver.c", "line_start": 1, "line_end": 5}],
                "focus": sorted(semantic_analysis.FOCUS), "dfx": list(semantic_analysis.DFX), "depth_limitations": []}],
                "mapped_only": [], "depth_limitations": []}
        semantic_analysis.stage_plan(root, "branch-flow-run", plan)
        return holder, root, plan

    @staticmethod
    def unit(plan):
        ev1 = [{"path": "driver.c", "line": 2, "fact": "未就绪时返回错误"}]
        ev2 = [{"path": "driver.c", "line": 3, "fact": "模式为零时返回特定结果"}]
        return {"artifact_type": "semantic_analysis_unit", "schema_version": "1.0", "run_id": "branch-flow-run",
                "unit_id": "U01", "plan_sha256": semantic_analysis._digest(plan), "summary": "入口根据状态和模式选择返回路径。",
                "code_map": [{"symbol": "entry", "title": "入口处理函数", "role": "根据状态和模式选择处理结果",
                    "inputs": "接收就绪状态和模式参数", "decision": "先判断就绪状态，再判断模式参数",
                    "success_result": "就绪时向调用方返回对应成功结果", "failure_result": "未就绪时返回明确错误",
                    "disposition": "core", "source_evidence": [{"path": "driver.c", "line": 1, "fact": "入口函数定义"}]}],
                "flows": [{"title": "入口请求处理", "priority": "P0", "external_trigger": "上层发起入口请求",
                    "registration": "模块初始化后由上层调用", "preconditions": "模块已经初始化",
                    "normal_path": ["接收上层请求", "读取状态和模式参数", "判断状态与模式", "执行对应处理并保持状态可用", "向上层返回处理结果"],
                    "branches": [
                        {"kind": "if", "condition": "当前是否未就绪", "true_path": "返回错误", "false_path": "继续模式判断",
                         "effect": "未就绪请求被拒绝且不进入成功处理", "controllability": "将模块置为未就绪后发起请求",
                         "observability": "观察错误返回并验证恢复后请求成功", "source_evidence": ev1},
                        {"kind": "if", "condition": "模式是否为零", "true_path": "返回特定成功结果", "false_path": "返回普通成功结果",
                         "effect": "不同模式得到可区分的成功响应结果", "controllability": "就绪状态下分别设置零和非零模式",
                         "observability": "比较两类返回结果确认模式选择生效", "source_evidence": ev2}],
                    "states": [{"title": "入口状态", "initial_state": "就绪", "transitions": ["处理后保持可用"],
                        "illegal_transitions": ["未就绪不得进入成功路径"], "controls": ["改变就绪状态"],
                        "observables": ["返回结果和后续恢复请求"], "source_evidence": ev1}],
                    "resources": [{"title": "调用额度", "acquire": "进入时占用", "owner": "当前调用持有", "release": "返回时释放",
                        "abnormal_cleanup": "错误返回同样结束调用", "invariant": "每次调用唯一结束", "limits": ["单次调用边界"],
                        "recovery": "修正状态后重试", "source_evidence": ev1}],
                    "concurrency": [],
                    "errors": [{"title": "未就绪错误", "trigger": "未就绪请求", "propagation": ["入口识别异常", "向上层返回错误"],
                        "masking": "不得转换为成功", "terminal_effect": "本次请求失败", "recovery": "恢复就绪后重试", "source_evidence": ev1}],
                    "recovery": ["恢复就绪状态", "重新发起请求"], "controls": ["控制状态和模式输入"],
                    "oracles": ["响应符合输入且错误后可恢复"], "source_evidence": ev1}],
                "dfx": [{"dimension": name, "applicable": True, "conclusion": f"{name}需要验证状态和返回行为", "source_evidence": ev1}
                        for name in semantic_analysis.DFX],
                "specialist_findings": [{"title": "入口恢复专项", "conclusion": "未就绪错误后恢复状态应能再次处理请求",
                    "severity": "High", "source_evidence": ev1}],
                "sfmea": [{"title": "错误状态残留", "failure_mode": "未就绪错误导致后续请求持续失败",
                    "cause": "错误路径后状态未恢复", "local_effect": "入口持续拒绝请求", "external_effect": "上层业务无法恢复",
                    "detection": "观察错误返回和后续请求结果", "recovery": "恢复就绪状态后重新请求", "severity": "High",
                    "source_evidence": ev1}],
                "scenarios": [{"title": "错误后恢复场景", "drivers": ["异常分支", "状态恢复"],
                    "failure_mechanism": "未就绪错误可能影响后续正常处理", "external_construction": "先在未就绪状态请求再恢复后请求",
                    "injection": "通过入口状态控制进入异常路径", "oracle": "首次失败且恢复后请求成功", "source_evidence": ev1}],
                "test_cases": [{"title": "未就绪后恢复用例", "scenario_title": "错误后恢复场景", "preconditions": "模块已初始化",
                    "steps": ["设置未就绪并发起请求", "确认错误返回", "恢复就绪后再次请求"],
                    "expected": "首次失败且恢复后请求成功", "observation": "观察两次请求返回结果", "cleanup": "保持模块就绪",
                    "source_evidence": ev1}],
                "depth_limitations": [], "unresolved": []}

    def test_branch_denominator_and_missing_branch_gate(self):
        holder, root, plan = self.prepare()
        try:
            context = semantic_analysis.unit_context(root, "branch-flow-run", "U01")
            self.assertEqual(2, context["branch_contract"]["required_count"])
            unit = self.unit(plan); semantic_analysis.validate_unit(root, "branch-flow-run", unit)
            unit["flows"][0]["branches"].pop()
            with self.assertRaisesRegex(semantic_analysis.SemanticAnalysisError, "branch mapping is incomplete"):
                semantic_analysis.validate_unit(root, "branch-flow-run", unit)
        finally: holder.cleanup()

    def test_p0_flow_depth_and_blackbox_projection(self):
        holder, root, plan = self.prepare()
        try:
            unit = self.unit(plan)
            shallow = copy.deepcopy(unit); shallow["flows"][0]["normal_path"] = ["接收请求", "判断状态", "返回结果"]
            with self.assertRaisesRegex(semantic_analysis.SemanticAnalysisError, "P0/P1 flow normal_path"):
                semantic_analysis.validate_unit(root, "branch-flow-run", shallow)
            semantic_analysis.stage_unit(root, "branch-flow-run", unit)
            model = semantic_analysis.assemble_model(root, "branch-flow-run")
            projected = analysis_reporting.projection(model)
            self.assertEqual(2, len(projected["branches"]))
            self.assertIn("测试构造：", projected["branches"][0]["test_explanation"])
            self.assertIn("业务结果：", projected["branches"][0]["test_explanation"])
            self.assertIn("关联状态：", projected["flows"][0]["test_explanation"])
            self.assertIn("失败传播：", projected["flows"][0]["test_explanation"])
        finally: holder.cleanup()

    def test_branch_scanner_includes_else_and_switch_arms(self):
        self.assertEqual([[1, "if"], [2, "else_if"], [3, "else"], [5, "case"], [6, "default"]],
                         semantic_analysis._branch_points(["if (a) {", "} else if (b) {", "} else {",
                                                          "switch (x) {", "case 1:", "default:", "}"]))


if __name__ == "__main__": unittest.main()
