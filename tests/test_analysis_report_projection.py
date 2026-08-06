from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime
from tests.test_analysis_depth_contract import AnalysisDepthContractTests, DFX

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"


class AnalysisReportProjectionTests(unittest.TestCase):
    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, text=True, capture_output=True, check=False)

    def cli(self, *args: str) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    @staticmethod
    def risk() -> dict:
        return {"artifact_type": "risk_card", "schema_version": "1.0", "risk_id": "R-1",
                "title": "错误后状态残留", "dfx": ["功能与状态"], "severity": "High", "confidence": "high",
                "trigger": "先发送非法请求", "propagation": "错误路径未恢复状态", "external_impact": "后续正常请求失败",
                "observation": "返回码、日志和后续业务", "recovery": "修正请求后业务应恢复",
                "translation_status": "Blackbox-ready", "test_explanation": "验证错误不影响后续正常业务。",
                "instrumentation_request": None, "evidence": [{"location": "driver.c:1", "observation": "error path"}],
                "status": "open"}

    def prepare(self, root: Path) -> tuple[Path, dict]:
        AnalysisDepthContractTests.repository(root)
        created = self.cli("create-v2", "--root", str(root), "--scenario", "module-analysis", "--target", "driver",
                           "--repository", "driver", "--run-id", "depth", "--analysis-depth", "complete")
        run_dir = Path(created["run_dir"])
        AnalysisDepthContractTests.complete_checkpoints(root, "depth")
        risk = self.risk(); data_runtime.upsert_risk(root, "depth", risk)
        analysis = AnalysisDepthContractTests.model(run_dir)
        path = root / "analysis.json"; path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        self.cli("stage-analysis-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
        return run_dir, risk

    def test_stage_report_overwrites_shallow_sections_from_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, risk = self.prepare(root)
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "深度报告", "summary": "固定分析模型生成。", "task_contract": contract,
                     "code_map": [{"title": "浅摘要", "test_explanation": "只有一句。", "source_evidence": "x"}],
                     "flows": [{"title": "浅流程", "test_explanation": "只有一句。", "steps": ["请求"], "source_evidence": "x"}],
                     "branches": [{"title": "浅分支", "test_explanation": "只有一句。", "source_evidence": "x"}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            draft_path = root / "draft.json"; draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(draft_path))
            model = json.loads(Path(staged["report_model"]).read_text(encoding="utf-8"))
            self.assertEqual("EP-1", model["code_map"][0]["analysis_id"])
            self.assertEqual("FLOW-1", model["flows"][0]["analysis_id"])
            self.assertIn("resource_lifecycle", model["flows"][0]["developer_detail"])
            self.assertEqual("SC-1", model["scenarios"][0]["scenario_id"])
            self.assertEqual("TF-1", model["analysis_details"]["test_flows"][0]["test_flow_id"])
            analysis_path = run_dir / "internal/analysis-model.json"
            self.assertEqual({"path": "internal/analysis-model.json", "sha256": hashlib.sha256(analysis_path.read_bytes()).hexdigest()},
                             model["analysis_artifact"])

    def test_render_contains_every_deep_analysis_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, risk = self.prepare(root)
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "深度报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            path = root / "draft.json"; path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
            model_path = Path(staged["report_model"])
            # Rendering is exercised directly before audit; finalization has the same renderer.
            from runtime import reporting
            md, page = reporting.render(json.loads(model_path.read_text(encoding="utf-8")))
            for token in ("FLOW-1", "STATE-1", "RES-1", "CON-1", "ERR-1", "CAND-1", "SF-1", "TF-1", "TR-1"):
                self.assertIn(token, md)
                self.assertIn(token, page)
            for title in ("开发实现讲解与完整 Flow Card", "状态、资源与并发模型", "场景推导与 SFMEA",
                          "黑盒测试流程", "追溯矩阵与 Coverage disposition"):
                self.assertIn(title, md)
                self.assertIn(title, page)

    def test_tampered_projection_is_rejected_even_with_matching_report_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, risk = self.prepare(root)
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "深度报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            path = root / "draft.json"; path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
            model_path = Path(staged["report_model"])
            model = json.loads(model_path.read_text(encoding="utf-8")); model["analysis_details"]["flows"] = []
            model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0", "audited_artifact": "internal/report-model.json",
                       "audited_sha256": digest, "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            opinion_path = root / "audit.json"; opinion_path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("apply-audit-v2", "--root", str(root), "--run-id", "depth", "--file", str(opinion_path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("完整消费固定分析模型", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
