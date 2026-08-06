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
            self.stage_workers(root, run_dir, omit="dfx-upgrade-compatibility")
            self.stage_artifacts_and_checkpoints(root, run_dir)
            model = self.lifecycle_model(run_dir); path = root / "model.json"
            path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("dfx-upgrade-compatibility", rejected["stderr"])

    def test_unknown_worker_contribution_blocks_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "unknown-worker")
            self.stage_workers(root, run_dir, unknown=True); self.stage_artifacts_and_checkpoints(root, run_dir)
            model = self.lifecycle_model(run_dir); path = root / "model.json"
            path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("UNKNOWN-ID", rejected["stderr"])

    def test_worker_index_explicitly_does_not_claim_identity_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "identity")
            self.stage_workers(root, run_dir)
            index = json.loads((run_dir / "internal/worker-index.json").read_text(encoding="utf-8"))
            self.assertEqual("repository_declared", index["provenance_strength"])
            self.assertFalse(index["identity_verified"])
            for row in index["workers"]:
                receipt = json.loads((run_dir / row["receipt"]["path"]).read_text(encoding="utf-8"))
                self.assertFalse(receipt["identity_verified"])
                self.assertIsNone(receipt["identity_attestation"])

    def test_auditor_receipt_rejects_same_declared_invocation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "audit-id")
            result = self.cli(root, "stage-auditor-receipt-v2", "--run-id", run_dir.name,
                              "--producer-invocation-id", "same-invocation", "--auditor-invocation-id", "same-invocation",
                              expected=2)
            self.assertIn("必须不同", result["stderr"])


if __name__ == "__main__":
    unittest.main()
