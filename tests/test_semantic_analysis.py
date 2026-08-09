from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import analysis_pipeline, data_runtime, semantic_analysis
from tests import test_contract_lifecycle


ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"


class SemanticAnalysisTests(unittest.TestCase):
    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RUNCTL), *args], cwd=ROOT, text=True,
            capture_output=True, check=False,
        )

    def cli(self, *args: str) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def run_fixture(self, depth: str = "complete", *, line_mode: bool = False):
        holder = tempfile.TemporaryDirectory()
        root = Path(holder.name).resolve()
        helper = test_contract_lifecycle.ContractLifecycleTests()
        helper.prepare(root)
        repo = root / "pangea-data" / "repositories" / "driver"
        (repo / "driver.c").write_text(
            "int entry(int ready) {\n"
            "    if (!ready) return -1;\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(repo), "add", "driver.c"], check=True)
        subprocess.run([
            "git", "-C", str(repo), "-c", "user.email=test@example.invalid",
            "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "initial",
        ], check=True)
        args = [
            "draft-contract-v2", "--scenario", "module-analysis",
            "--target", "驱动入口", "--repository", "driver", "--source-scope",
            "driver=driver.c", "--contract-id", "semantic-contract", "--analysis-depth", depth,
        ]
        if line_mode:
            args.append("--line-obligation-mode")
        helper.cli(root, *args)
        helper.cli(root, "confirm-contract-v2", "--contract-id", "semantic-contract", "--revision", "1",
                   "--source", "user_reply", "--materials-status", "confirmed_none")
        activated = helper.cli(root, "activate-contract-v2", "--contract-id", "semantic-contract",
                               "--run-id", "semantic-run")
        return holder, root, Path(activated["run_dir"])

    @staticmethod
    def plan(depth: str = "complete") -> dict:
        return {
            "artifact_type": "semantic_analysis_plan", "schema_version": "1.0",
            "run_id": "semantic-run", "analysis_depth": depth,
            "units": [{
                "unit_id": "U01", "title": "入口处理与错误恢复", "repository": "driver",
                "priority": "P0", "source_ranges": [{"path": "driver.c", "line_start": 1, "line_end": 4}],
                "focus": sorted(semantic_analysis.FOCUS), "dfx": list(semantic_analysis.DFX),
                "depth_limitations": [] if depth == "complete" else ["仅深挖关键入口和错误恢复路径"],
            }],
            "mapped_only": [],
            "depth_limitations": [] if depth == "complete" else ["仅深挖关键入口和错误恢复路径"],
        }

    @staticmethod
    def unit(plan: dict) -> dict:
        evidence = [{"path": "driver.c", "line": 2, "fact": "未就绪时立即返回错误"}]
        return {
            "artifact_type": "semantic_analysis_unit", "schema_version": "1.0",
            "run_id": "semantic-run", "unit_id": "U01",
            "plan_sha256": semantic_analysis._digest(plan),
            "summary": "入口根据就绪状态选择成功路径或错误恢复路径。",
            "code_map": [{"title": "入口函数", "role": "接收状态并返回处理结果", "source_evidence": evidence}],
            "flows": [{
                "title": "请求处理主流程", "priority": "P0", "external_trigger": "上层调用入口函数",
                "registration": "模块初始化后由上层直接调用", "preconditions": "模块已经完成初始化",
                "normal_path": ["接收就绪状态", "校验当前状态", "返回成功结果"],
                "branches": [{"condition": "当前状态是否就绪", "true_path": "继续返回成功结果",
                    "false_path": "立即返回错误结果", "effect": "调用方看到成功或明确失败",
                    "controllability": "通过输入状态选择分支", "observability": "通过返回值观察结果",
                    "source_evidence": evidence}],
                "states": [{"title": "入口运行状态", "initial_state": "就绪",
                    "transitions": ["就绪状态进入处理并返回就绪"], "illegal_transitions": ["未就绪状态不得进入成功路径"],
                    "controls": ["改变入口状态参数"], "observables": ["检查函数返回结果"],
                    "source_evidence": evidence}],
                "resources": [{"title": "调用处理额度", "acquire": "进入函数时占用处理额度",
                    "owner": "当前调用持有额度", "release": "函数返回时释放额度",
                    "abnormal_cleanup": "错误返回同样结束当前调用", "invariant": "每次调用都必须得到唯一返回结果",
                    "limits": ["单次调用边界", "连续调用边界"], "recovery": "修正状态后重新调用",
                    "source_evidence": evidence}],
                "concurrency": [{"title": "并发入口调用", "actors": ["正常调用方", "恢复调用方"],
                    "shared_state": ["模块就绪状态"], "ordering": ["读取状态后再选择返回路径"],
                    "race_windows": ["状态变化与入口检查同时发生"], "cancellation": ["调用取消后不得遗留占用"],
                    "recovery": "状态稳定后重新发起调用", "source_evidence": evidence}],
                "errors": [{"title": "未就绪错误传播", "trigger": "输入状态表示当前未就绪",
                    "propagation": ["入口识别未就绪", "向调用方返回错误"], "masking": "不得把错误转换为成功",
                    "terminal_effect": "当前请求失败但后续可以恢复", "recovery": "状态恢复后重新调用",
                    "source_evidence": evidence}],
                "recovery": ["恢复就绪状态", "重新发送正常请求"],
                "controls": ["构造就绪和未就绪两类输入"], "oracles": ["错误可见且恢复后调用成功"],
                "source_evidence": evidence,
            }],
            "dfx": [{"dimension": name, "applicable": True,
                "conclusion": f"{name}需要验证错误返回和恢复行为", "source_evidence": evidence}
                for name in semantic_analysis.DFX],
            "specialist_findings": [{"title": "错误恢复专项", "conclusion": "错误返回后需要验证下一次调用可恢复",
                "severity": "High", "source_evidence": evidence}],
            "sfmea": [{"title": "错误状态残留", "failure_mode": "未就绪错误导致后续调用持续失败",
                "cause": "错误出口没有恢复调用条件", "local_effect": "入口持续返回错误",
                "external_effect": "上层业务无法继续处理", "detection": "观察返回值和后续请求结果",
                "recovery": "恢复就绪条件后重新调用", "severity": "High", "source_evidence": evidence}],
            "scenarios": [{"title": "错误后恢复场景", "drivers": ["异常分支", "状态恢复"],
                "failure_mechanism": "未就绪错误可能影响后续正常调用",
                "external_construction": "先传入未就绪状态再传入就绪状态", "injection": "通过入口参数控制状态",
                "oracle": "首次调用失败且第二次调用成功", "source_evidence": evidence}],
            "test_cases": [{"title": "未就绪后恢复用例", "scenario_title": "错误后恢复场景",
                "preconditions": "模块已经完成初始化", "steps": ["传入未就绪状态", "确认错误返回", "传入就绪状态"],
                "expected": "错误返回清晰且恢复后的调用成功", "observation": "观察两次调用的返回值",
                "cleanup": "恢复模块为就绪状态", "source_evidence": evidence}],
            "depth_limitations": plan["units"][0]["depth_limitations"], "unresolved": [],
        }

    def test_default_semantic_mode_assembles_full_model_and_blocks_line_pipeline(self) -> None:
        holder, root, run = self.run_fixture()
        try:
            contract = json.loads((run / "internal/task-contract.json").read_text(encoding="utf-8"))
            self.assertEqual("semantic", contract["analysis_execution_mode"])
            with self.assertRaisesRegex(analysis_pipeline.PipelineError, "hidden line-obligation"):
                analysis_pipeline.build_denominator(root, "semantic-run")
            context = semantic_analysis.planner_context(root, "semantic-run")
            self.assertIn("禁止逐行出题", context["instructions"])
            plan = semantic_analysis.stage_plan(root, "semantic-run", self.plan())
            self.assertEqual(1, plan["units"])
            resumed = self.cli("resume-v2", "--root", str(root), "--run-id", "semantic-run")
            self.assertEqual("semantic", resumed["analysis_execution_mode"])
            self.assertEqual(["U01"], resumed["semantic_progress"]["pending_units"])
            semantic_analysis.stage_unit(root, "semantic-run", self.unit(self.plan()))
            resumed = self.cli("resume-v2", "--root", str(root), "--run-id", "semantic-run")
            self.assertEqual("assemble-semantic-analysis-v2", resumed["semantic_progress"]["next_action"])
            assembled = self.cli("assemble-semantic-analysis-v2", "--root", str(root),
                                 "--run-id", "semantic-run")
            self.assertEqual("semantic", assembled["analysis_mode"])
            model = json.loads((run / "internal/analysis-model.json").read_text(encoding="utf-8"))
            self.assertEqual("analysis_model", model["artifact_type"])
            self.assertTrue(model["flows"] and model["sfmea"] and model["test_cases"])
            self.assertEqual(set(semantic_analysis.DFX), {row["dfx"] for row in model["model_applicability"]})
            ledger = json.loads((run / "internal/risk-ledger.json").read_text(encoding="utf-8"))
            self.assertEqual(["RISK-U01-M1"], [row["risk_id"] for row in ledger["risks"]])
            self.assertEqual(["RISK-U01-M1"], model["test_scenarios"][0]["risk_ids"])
            draft = {
                "title": "语义模块完整分析报告", "summary": "由冻结语义单元确定性生成。",
                "task_contract": contract,
                "code_map": [{"title": "入口", "test_explanation": "入口负责选择成功或错误路径。",
                              "source_evidence": "driver.c:2"}],
                "flows": [{"title": "入口处理", "test_explanation": "请求根据状态进入成功或错误路径。",
                           "steps": ["发送请求", "观察返回结果"], "source_evidence": "driver.c:2"}],
                "branches": [{"title": "状态分支", "test_explanation": "未就绪时返回明确错误。",
                              "source_evidence": "driver.c:2"}],
                "risks": ledger["risks"], "scenarios": [], "test_cases": [],
                "unresolved": [], "next_steps": [],
            }
            draft_path = root / "semantic-report.json"
            draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            report = self.cli("stage-report-v2", "--root", str(root), "--run-id", "semantic-run",
                              "--file", str(draft_path))
            judge = json.loads(Path(report["coverage_judge"]).read_text(encoding="utf-8"))
            self.assertEqual("PASS", judge["verdict"])
        finally:
            holder.cleanup()

    def test_fast_keeps_all_stages_and_requires_explicit_depth_limitations(self) -> None:
        holder, root, _run = self.run_fixture("fast")
        try:
            plan = self.plan("fast")
            semantic_analysis.validate_plan(root, "semantic-run", plan)
            missing = copy.deepcopy(plan); missing["depth_limitations"] = []
            with self.assertRaisesRegex(semantic_analysis.SemanticAnalysisError, "depth limitations"):
                semantic_analysis.validate_plan(root, "semantic-run", missing)
        finally:
            holder.cleanup()

    def test_hidden_line_mode_is_explicit_and_semantic_commands_reject_it(self) -> None:
        holder, root, run = self.run_fixture(line_mode=True)
        try:
            contract = json.loads((run / "internal/task-contract.json").read_text(encoding="utf-8"))
            self.assertEqual("line_obligation", contract["analysis_execution_mode"])
            with self.assertRaisesRegex(semantic_analysis.SemanticAnalysisError, "semantic"):
                semantic_analysis.planner_context(root, "semantic-run")
            self.assertGreater(analysis_pipeline.build_denominator(root, "semantic-run")["obligations"], 0)
        finally:
            holder.cleanup()

    def test_line_mode_switch_and_internal_commands_are_absent_from_public_help(self) -> None:
        top = self.cli_result("--help")
        self.assertEqual(0, top.returncode, top.stderr)
        for value in ("build-denominator-v2", "issue-context-v2", "execute-analysis-batches-v2",
                      "apply-fragment-v2"):
            self.assertNotIn(value, top.stdout)
        draft = self.cli_result("draft-contract-v2", "--help")
        self.assertEqual(0, draft.returncode, draft.stderr)
        self.assertNotIn("line-obligation", draft.stdout)


if __name__ == "__main__":
    unittest.main()
