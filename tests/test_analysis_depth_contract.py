from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"
DFX = ("功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性")


class AnalysisDepthContractTests(unittest.TestCase):
    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, text=True,
                              capture_output=True, check=False)

    def cli(self, *args: str) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    @staticmethod
    def repository(root: Path) -> None:
        repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        (repo / "driver.c").write_text("int entry(void) { return 0; }\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "driver.c"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid",
                        "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "initial"], check=True)

    @staticmethod
    def complete_checkpoints(root: Path, run_id: str) -> None:
        run_dir = data_runtime.ensure_layout(root) / "runs" / run_id
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        lifecycle = manifest.get("contract_record_file") == "internal/contract-record.json"

        def bindings(stage: str) -> list[dict[str, str]]:
            if not lifecycle:
                return []
            artifact = run_dir / "internal" / "stages" / f"{stage}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            data_runtime.atomic_write_json(artifact, {
                "artifact_type": "stage_artifact", "schema_version": "1.0", "run_id": run_id,
                "stage": stage, "summary": f"{stage} 阶段已形成可复核结构化工件",
                "evidence_ids": ["EV-1"], "item_ids": [stage.upper()], "open_items": [],
            })
            return [{"path": f"internal/stages/{stage}.json", "sha256": data_runtime.sha256_file(artifact)}]

        for stage in ("code_map", "flow", "branches"):
            data_runtime.append_checkpoint(root, run_id, {"stage": stage, "status": "completed",
                "facts": [{"summary": f"{stage} 已建立具体实现模型", "evidence": f"driver.c: {stage} evidence"}],
                "artifact_bindings": bindings(stage), "open_items": [], "next_step": "继续"})
        data_runtime.append_checkpoint(root, run_id, {"stage": "dfx_scan", "status": "completed",
            "facts": [{"dfx": item, "conclusion": f"{item}已形成具体结论", "evidence": f"driver.c: {item}"} for item in DFX],
            "artifact_bindings": bindings("dfx_scan"), "open_items": [], "next_step": "继续"})
        for stage in ("specialist", "sfmea", "test_design"):
            data_runtime.append_checkpoint(root, run_id, {"stage": stage, "status": "completed",
                "facts": [{"summary": f"{stage} 已形成具体分析工件", "evidence": f"internal/{stage}.json"}],
                "artifact_bindings": bindings(stage), "open_items": [], "next_step": "继续"})

    @staticmethod
    def model(run_dir: Path, depth: str = "complete") -> dict:
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        evidence = [{"path": "driver.c", "line": 1, "fact": "entry is externally registered"}]
        disposition = {"status": "analyzed", "disposition_reason": "已读取直接源码并完成外部行为分析"}
        return {
            "artifact_type": "analysis_model", "schema_version": "1.0", "run_id": run_dir.name,
            "analysis_depth": depth, "source_commits": contract["repository_commits"],
            "evidence_consumption": [{"evidence_id": "E-1", "source_ref": "driver.c", "status": "parsed",
                "parser": "source reader", "consumed_ranges": ["driver.c:1"], "conclusions": ["入口可达"],
                "used_by": ["EP-1", "FLOW-1"], "unread_ranges": [], "limitations": []}],
            "entrypoints": [{"entrypoint_id": "EP-1", "title": "外部入口", "external_trigger": "发送业务请求",
                "registration": "启动时登记入口", "preconditions": "模块已初始化", "flow_ids": ["FLOW-1"],
                "source_evidence": evidence, **disposition}],
            "flows": [{"flow_id": "FLOW-1", "title": "业务主流程", "priority": "P0",
                "external_trigger": "发送业务请求", "entrypoint_id": "EP-1", "registration": "启动时登记",
                "preconditions": "模块正常运行", "normal_path": ["接收请求", "校验状态", "返回结果"],
                "decisions": ["BR-1"], "abnormal_paths": ["非法请求返回错误"], "state_changes": ["STATE-1"],
                "resource_lifecycle": ["RES-1"], "timeout_retry_recovery": ["超时后返回并允许重试"],
                "concurrency": ["CON-1"], "error_propagation": ["ERR-1"],
                "latent_or_secondary_failures": ["连续失败可能造成状态残留"],
                "blackbox_controls": ["构造非法请求和超时"], "oracles": ["返回码、日志和后续业务恢复"],
                "source_evidence": evidence, **disposition}],
            "branches": [{"branch_id": "BR-1", "flow_id": "FLOW-1", "condition": "请求字段是否合法",
                "true_path": "继续处理", "false_path": "返回错误", "external_effect": "请求成功或明确失败",
                "controllability": "可修改请求字段", "observability": "返回码和日志", "source_evidence": evidence, **disposition}],
            "states": [{"state_id": "STATE-1", "title": "运行状态", "initial_state": "READY",
                "transitions": ["READY->BUSY->READY"], "illegal_transitions": ["ERROR->BUSY"],
                "external_controls": ["发送请求或触发恢复"], "observables": ["业务结果和状态日志"],
                "source_evidence": evidence, **disposition}],
            "resources": [{"resource_id": "RES-1", "title": "请求额度", "acquire": "接收请求时占用",
                "owner": "当前请求", "release": "请求完成时归还", "abnormal_cleanup": "异常出口统一归还",
                "invariant": "占用数不超过上限且完成后回落", "limits": ["N-1", "N", "N+1"],
                "recovery": "压力解除后自动恢复", "source_evidence": evidence, **disposition}],
            "concurrency": [{"concurrency_id": "CON-1", "title": "请求与恢复并发", "actors": ["请求线程", "恢复线程"],
                "shared_state": ["运行状态", "请求额度"], "ordering": ["状态检查先于资源占用"],
                "race_windows": ["恢复与新请求同时发生"], "cancellation": ["取消后释放额度"],
                "recovery": "并发结束后状态和额度恢复", "source_evidence": evidence, **disposition}],
            "error_chains": [{"chain_id": "ERR-1", "title": "非法请求传播", "trigger": "字段非法",
                "propagation": ["校验失败", "错误返回", "记录日志"], "masking": "不得转换为成功",
                "terminal_effect": "当前请求失败但后续业务可继续", "recovery": "修正请求后重试",
                "source_evidence": evidence, **disposition}],
            "model_applicability": [{"dfx": item, "applicable": True, "reason": f"{item}与该流程相关",
                "evidence": f"driver.c: {item}"} for item in DFX],
            "scenario_candidates": [{"candidate_id": "CAND-1", "title": "非法字段后恢复", "drivers": ["分支", "状态", "异常传播"],
                "source_refs": ["BR-1", "STATE-1", "ERR-1"], "failure_mechanism": "错误路径可能残留状态",
                "external_construction": "发送非法请求后立即发送正常请求", "injection": "无需内部注入",
                "oracle": "非法请求失败且正常请求成功", "disposition": "retained", "target_ids": ["SC-1", "TC-1"]}],
            "sfmea": [{"sfmea_id": "SF-1", "title": "错误后状态残留", "source_refs": ["ERR-1", "STATE-1"],
                "failure_mode": "非法请求后状态未恢复", "cause": "异常出口遗漏恢复", "local_effect": "状态保持异常",
                "external_effect": "后续正常请求失败", "detection": "返回码、日志、后续业务", "recovery": "重新发起恢复或重连",
                "severity": "High", "scenario_ids": ["SC-1"], "test_case_ids": ["TC-1"]}],
            "test_scenarios": [{"scenario_id": "SC-1", "title": "非法请求后正常业务恢复", "source_candidate_ids": ["CAND-1"],
                "risk_ids": ["R-1"], "preconditions": "模块正常运行", "trigger": "先非法后正常请求",
                "expected": "非法请求失败且正常请求成功", "observations": ["返回码", "日志", "业务状态"],
                "cleanup": "结束请求并确认资源释放"}],
            "test_flows": [{"test_flow_id": "TF-1", "title": "错误恢复测试流程", "scenario_id": "SC-1",
                "steps": ["建立正常基线", "发送非法请求", "发送正常请求", "检查资源和状态"],
                "oracles": ["错误可见且后续业务成功"], "cleanup": "释放会话", "test_case_ids": ["TC-1"]}],
            "test_cases": [{"case_id": "TC-1", "title": "非法字段恢复", "scenario_id": "SC-1", "risk_ids": ["R-1"],
                "preconditions": "模块正常运行", "steps": ["发送非法请求", "随后发送正常请求"],
                "expected": "非法请求失败且正常请求成功", "observation": "返回码、日志、资源计数",
                "cleanup": "释放会话", "source_refs": ["BR-1", "ERR-1"]}],
            "traceability": [{"trace_id": "TR-1", "source_ids": ["BR-1", "ERR-1", "CAND-1"],
                "target_ids": ["SF-1", "SC-1", "TF-1", "TC-1"], "rationale": "分支和错误传播推导测试"}],
            "coverage_dispositions": [
                {"item_type": kind, "item_id": item_id, "outcome": "analyzed", "evidence": "已形成直接源码分析工件",
                 "covered_by": ["TC-1"], "missing_work": []}
                for kind, item_id in (("entrypoint", "EP-1"), ("flow", "FLOW-1"), ("branch", "BR-1"),
                                      ("state", "STATE-1"), ("resource", "RES-1"), ("concurrency", "CON-1"),
                                      ("error_chain", "ERR-1"), ("candidate", "CAND-1"))
            ],
            "depth_limitations": [] if depth == "complete" else ["快速模式只展开一个 P0 流程，其他 P1 流程待补充"],
            "unresolved": [],
        }

    def test_complete_run_requires_staged_analysis_model_before_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "driver",
                               "--repository", "driver", "--run-id", "depth", "--analysis-depth", "complete")
            run_dir = Path(created["run_dir"]); self.complete_checkpoints(root, "depth")
            report = {"title": "报告", "task_contract": json.loads((run_dir / "internal/task-contract.json").read_text()),
                      "code_map": [{"title": "入口", "test_explanation": "外部请求进入模块", "source_evidence": "driver.c:1"}],
                      "flows": [{"title": "流程", "test_explanation": "请求进入后返回结果", "steps": ["发送请求", "观察结果"], "source_evidence": "driver.c:1"}],
                      "branches": [{"title": "分支", "test_explanation": "非法输入返回错误", "source_evidence": "driver.c:1"}],
                      "risks": [], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            report_path = root / "report.json"; report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("stage-report-v2", "--root", tmp, "--run-id", "depth", "--file", str(report_path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("analysis-model.json", rejected.stderr)

            model_path = root / "analysis.json"; model_path.write_text(json.dumps(self.model(run_dir), ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-analysis-v2", "--root", tmp, "--run-id", "depth", "--file", str(model_path))
            self.assertEqual("internal/analysis-model.json", staged["analysis_artifact"])
            self.assertEqual(hashlib.sha256((run_dir / "internal/analysis-model.json").read_bytes()).hexdigest(), staged["sha256"])

    def test_shallow_flow_card_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "driver",
                               "--repository", "driver", "--run-id", "shallow", "--analysis-depth", "complete")
            run_dir = Path(created["run_dir"]); self.complete_checkpoints(root, "shallow")
            model = self.model(run_dir); model["flows"][0].pop("resource_lifecycle")
            path = root / "shallow.json"; path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            result = self.cli_result("stage-analysis-v2", "--root", tmp, "--run-id", "shallow", "--file", str(path))
            self.assertEqual(2, result.returncode)
            self.assertIn("resource_lifecycle", result.stderr)

    def test_fast_model_requires_explicit_depth_limitations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "driver",
                               "--repository", "driver", "--run-id", "fast", "--analysis-depth", "fast")
            run_dir = Path(created["run_dir"]); self.complete_checkpoints(root, "fast")
            model = self.model(run_dir, "fast"); model["depth_limitations"] = []
            path = root / "fast.json"; path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            result = self.cli_result("stage-analysis-v2", "--root", tmp, "--run-id", "fast", "--file", str(path))
            self.assertEqual(2, result.returncode)
            self.assertIn("depth_limitations", result.stderr)


if __name__ == "__main__":
    unittest.main()
