from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, runctl
from tests.test_analysis_depth_contract import AnalysisDepthContractTests, DFX
from tests.test_evidence_provenance import EvidenceProvenanceTests

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"
WORKERS = {
    "dfx-function-state": ["EP-1", "FLOW-1", "BR-1", "STATE-1", "ERR-1"],
    "dfx-resource-spec": ["RES-1"],
    "dfx-performance-pressure": ["CAND-1"],
    "dfx-concurrency-exception": ["CON-1"],
    "dfx-upgrade-compatibility": ["CAND-1"],
    "dfx-reliability-consistency": ["CAND-1"],
}


class WorkerAuditProvenanceTests(unittest.TestCase):
    def cli(self, root: Path, *args: str, expected: int = 0) -> dict:
        result = subprocess.run([sys.executable, str(RUNCTL), *args, "--root", str(root)], cwd=ROOT,
                                text=True, capture_output=True, check=False)
        if result.returncode != expected:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {"stderr": result.stderr}

    def prepare(self, root: Path, contract_id: str = "workers") -> Path:
        helper = EvidenceProvenanceTests()
        run_dir = helper.activate(root, contract_id=contract_id)
        helper.stage(root, run_dir, helper.payload(root, run_dir))
        return run_dir

    def stage_workers(self, root: Path, run_dir: Path, *, omit: str | None = None, unknown: bool = False) -> None:
        for worker, contributions in WORKERS.items():
            if worker == omit:
                continue
            payload = {"worker": worker, "invocation_id": f"invocation-{worker}",
                       "assigned_scope": [f"分析 {worker} 负责的 DFX 范围"],
                       "searched_scope": ["driver.c 及相关固定源码证据"],
                       "contribution_ids": (["UNKNOWN-ID"] if unknown and worker == "dfx-resource-spec" else contributions),
                       "risk_ids": [], "status": "completed", "remaining_scope": []}
            path = root / f"{worker}.json"; path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.cli(root, "stage-worker-receipt-v2", "--run-id", run_dir.name, "--file", str(path))

    def stage_artifacts_and_checkpoints(self, root: Path, run_dir: Path) -> None:
        for stage in ("code_map", "flow", "branches", "dfx_scan", "specialist", "sfmea", "test_design"):
            artifact = {"artifact_type": "stage_artifact", "schema_version": "1.0", "run_id": run_dir.name,
                        "stage": stage, "summary": f"{stage} 阶段已形成可复核结构化工件",
                        "evidence_ids": ["EV-1"], "item_ids": [stage.upper()], "open_items": []}
            source = root / f"stage-{stage}.json"; source.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
            binding = self.cli(root, "stage-work-product-v2", "--run-id", run_dir.name,
                               "--stage", stage, "--file", str(source))["artifact_binding"]
            if stage == "dfx_scan":
                facts = [{"dfx": item, "conclusion": f"{item}已形成具体结论", "evidence": "EV-1"} for item in DFX]
            else:
                facts = [{"summary": f"{stage} 已形成具体分析工件", "evidence": "EV-1"}]
            data_runtime.append_checkpoint(root, run_dir.name, {"stage": stage, "status": "completed",
                "facts": facts, "artifact_bindings": [binding], "open_items": [], "next_step": "继续"})

    def lifecycle_model(self, run_dir: Path) -> dict:
        model = AnalysisDepthContractTests.model(run_dir)
        model["evidence_consumption"][0]["source_ref"] = "EV-1"
        for collection in ("entrypoints", "flows", "branches", "states", "resources", "concurrency", "error_chains"):
            for item in model[collection]: item["source_evidence"] = ["EV-1"]
        return model

    def test_lifecycle_checkpoint_requires_current_stage_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "checkpoint")
            with self.assertRaises(data_runtime.DataRuntimeError) as context:
                data_runtime.append_checkpoint(root, run_dir.name, {"stage": "code_map", "status": "completed",
                    "facts": [{"summary": "代码地图已经完成具体入口分析", "evidence": "EV-1"}],
                    "open_items": [], "next_step": "继续"})
            self.assertIn("artifact_bindings", str(context.exception))

    def test_missing_worker_blocks_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "missing-worker")
            self.stage_artifacts_and_checkpoints(root, run_dir)
            self.stage_workers(root, run_dir, omit="dfx-upgrade-compatibility")
            model = self.lifecycle_model(run_dir); path = root / "model.json"
            path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("dfx-upgrade-compatibility", rejected["stderr"])

    def test_unknown_worker_contribution_blocks_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "unknown-worker")
            self.stage_artifacts_and_checkpoints(root, run_dir); self.stage_workers(root, run_dir, unknown=True)
            model = self.lifecycle_model(run_dir); path = root / "model.json"
            path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("UNKNOWN-ID", rejected["stderr"])

    def test_worker_index_explicitly_does_not_claim_identity_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "identity")
            self.stage_artifacts_and_checkpoints(root, run_dir); self.stage_workers(root, run_dir)
            index = json.loads((run_dir / "internal/worker-index.json").read_text(encoding="utf-8"))
            self.assertEqual("repository_declared", index["provenance_strength"])
            self.assertFalse(index["identity_verified"])
            for row in index["workers"]:
                receipt = json.loads((run_dir / row["receipt"]["path"]).read_text(encoding="utf-8"))
                self.assertFalse(receipt["identity_verified"])
                self.assertIsNone(receipt["identity_attestation"])


    @staticmethod
    def risk() -> dict:
        return {"artifact_type": "risk_card", "schema_version": "1.0", "risk_id": "R-1",
                "title": "错误后状态残留", "dfx": ["功能与状态"], "severity": "High", "confidence": "high",
                "trigger": "先发送非法请求", "propagation": "错误路径未恢复状态", "external_impact": "后续正常请求失败",
                "observation": "返回码、日志和后续业务", "recovery": "修正请求后业务应恢复",
                "translation_status": "Blackbox-ready", "test_explanation": "验证错误不影响后续正常业务。",
                "instrumentation_request": None, "evidence": [{"location": "EV-1", "observation": "固定源码证据"}],
                "status": "open"}

    def stage_analysis_and_report(self, root: Path, run_dir: Path) -> Path:
        self.stage_artifacts_and_checkpoints(root, run_dir); self.stage_workers(root, run_dir)
        risk = self.risk(); data_runtime.upsert_risk(root, run_dir.name, risk)
        model = self.lifecycle_model(run_dir)
        model_path = root / "analysis-model.json"; model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(model_path))
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        draft = {"title": "Worker provenance 报告", "summary": "验证固定 worker 与 auditor 输入绑定。",
                 "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                 "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
        draft_path = root / "report-draft.json"; draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        staged = self.cli(root, "stage-report-v2", "--run-id", run_dir.name, "--file", str(draft_path))
        return Path(staged["report_model"])

    def test_auditor_receipt_is_required_and_positive_audit_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "audit-positive")
            report_path = self.stage_analysis_and_report(root, run_dir)
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0",
                       "audited_artifact": "internal/report-model.json",
                       "audited_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                       "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            opinion_path = root / "audit.json"; opinion_path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "apply-audit-v2", "--run-id", run_dir.name, "--file", str(opinion_path), expected=2)
            self.assertIn("auditor-receipt", rejected["stderr"])
            receipt = self.cli(root, "stage-auditor-receipt-v2", "--run-id", run_dir.name,
                               "--producer-invocation-id", "producer-declared-01",
                               "--auditor-invocation-id", "auditor-declared-02")
            self.assertFalse(receipt["identity_verified"])
            audited = self.cli(root, "apply-audit-v2", "--run-id", run_dir.name, "--file", str(opinion_path))
            self.assertEqual("PASS", audited["verdict"])

    def test_tampered_auditor_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "audit-stale")
            report_path = self.stage_analysis_and_report(root, run_dir)
            self.cli(root, "stage-auditor-receipt-v2", "--run-id", run_dir.name,
                     "--producer-invocation-id", "producer-declared-01",
                     "--auditor-invocation-id", "auditor-declared-02")
            receipt_path = run_dir / "internal/auditor-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["audited_inputs"][0]["sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0",
                       "audited_artifact": "internal/report-model.json",
                       "audited_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                       "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            opinion_path = root / "audit-stale.json"; opinion_path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "apply-audit-v2", "--run-id", run_dir.name, "--file", str(opinion_path), expected=2)
            self.assertIn("输入绑定已过期", rejected["stderr"])


    def test_auditor_receipt_rejects_same_declared_invocation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "audit-id")
            result = self.cli(root, "stage-auditor-receipt-v2", "--run-id", run_dir.name,
                              "--producer-invocation-id", "same-invocation", "--auditor-invocation-id", "same-invocation",
                              expected=2)
            self.assertIn("必须不同", result["stderr"])


if __name__ == "__main__":
    unittest.main()
