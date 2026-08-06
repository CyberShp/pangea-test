from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, repository_runtime
from tests.test_analysis_depth_contract import AnalysisDepthContractTests
from tests.test_contract_lifecycle import ContractLifecycleTests

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"
KINDS = ("entrypoint", "registration", "flow", "branch", "state", "resource", "concurrency", "error_path")


class EvidenceProvenanceTests(unittest.TestCase):
    def cli(self, root: Path, *args: str, expected: int = 0) -> dict:
        result = subprocess.run([sys.executable, str(RUNCTL), *args, "--root", str(root)], cwd=ROOT,
                                text=True, capture_output=True, check=False)
        if result.returncode != expected:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {"stderr": result.stderr}

    def activate(self, root: Path, *, contract_id: str = "evidence", depth: str = "complete",
                 input_refs: list[str] | None = None, materials_status: str = "confirmed_none") -> Path:
        ContractLifecycleTests().prepare(root)
        draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                         "--repository", "driver", "--analysis-depth", depth, "--contract-id", contract_id)
        if input_refs:
            contract = draft["task_contract"]; contract["input_refs"] = input_refs
            revised = root / "contract.json"; revised.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            self.cli(root, "revise-contract-v2", "--contract-id", contract_id, "--expected-revision", "1", "--file", str(revised))
            revision = "2"
        else:
            revision = "1"
        self.cli(root, "confirm-contract-v2", "--contract-id", contract_id, "--revision", revision,
                 "--source", "user_reply", "--materials-status", materials_status)
        activated = self.cli(root, "activate-contract-v2", "--contract-id", contract_id, "--run-id", contract_id + "-run")
        return Path(activated["run_dir"])

    @staticmethod
    def source_record(root: Path, run_dir: Path) -> dict:
        status = repository_runtime.snapshot_status(root, run_dir.name)
        binding = status["snapshots"][0]
        source = Path(binding["snapshot_dir"]) / "driver.c"
        content = source.read_bytes(); excerpt = b"".join(content.splitlines(keepends=True)[:1])
        return {"evidence_id": "EV-1", "repository": "driver", "commit_sha": binding["commit_sha"],
                "snapshot_id": binding["snapshot_id"], "path": "driver.c", "line_start": 1, "line_end": 1,
                "symbol": "entry", "claim": "外部入口函数在当前提交中真实存在",
                "file_sha256": hashlib.sha256(content).hexdigest(),
                "excerpt_sha256": hashlib.sha256(excerpt).hexdigest()}

    def payload(self, root: Path, run_dir: Path, *, materials: list[dict] | None = None,
                catalog: dict | None = None, mr_facts: dict | None = None) -> dict:
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        evidence = self.source_record(root, run_dir)
        discovery = [{"discovery_id": f"DISC-{index}", "target_kind": kind, "repository": "driver",
                      "commit_sha": evidence["commit_sha"], "method": "source_read", "query": kind,
                      "scope": "driver.c and registration tables", "candidate_ids": [f"CAND-{index}"],
                      "disposition": "expanded", "rationale": f"已从快照源码展开 {kind} 候选",
                      "evidence_ids": ["EV-1"], "limitations": []}
                     for index, kind in enumerate(KINDS, 1)]
        return {"artifact_type": "evidence_provenance", "schema_version": "1.0", "run_id": run_dir.name,
                "source_commits": contract["repository_commits"], "catalog": catalog,
                "material_selection": materials or [], "discovery": discovery,
                "source_evidence": [evidence], "mr_facts": mr_facts, "limitations": []}

    def stage(self, root: Path, run_dir: Path, payload: dict, *, expected: int = 0) -> dict:
        path = root / "evidence.json"; path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.cli(root, "stage-evidence-v2", "--run-id", run_dir.name, "--file", str(path), expected=expected)

    def test_valid_source_and_discovery_provenance_is_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root)
            staged = self.stage(root, run_dir, self.payload(root, run_dir))
            self.assertEqual("internal/evidence-provenance.json", staged["evidence_artifact"])
            self.assertTrue((run_dir / "internal/evidence-provenance.json").is_file())

    def test_forged_file_hash_and_line_range_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root, contract_id="forged")
            payload = self.payload(root, run_dir); payload["source_evidence"][0]["file_sha256"] = "0" * 64
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("SHA-256", rejected["stderr"])
            payload = self.payload(root, run_dir); payload["source_evidence"][0]["line_end"] = 99
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("行号越界", rejected["stderr"])

    def test_complete_discovery_requires_all_canonical_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root, contract_id="breadth")
            payload = self.payload(root, run_dir); payload["discovery"] = payload["discovery"][:-1]
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("error_path", rejected["stderr"])

    def test_selected_material_binds_catalog_markdown_and_anchor_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ContractLifecycleTests.marked_root(root); ContractLifecycleTests.repository(root)
            workspace = data_runtime.ensure_layout(root)
            (workspace / "inbox/design.md").write_text("# CHAP 设计\n认证失败必须释放会话资源。\n", encoding="utf-8")
            data_runtime.scan_inbox(root); data_runtime.convert_catalog(root); ContractLifecycleTests.receipt(root)
            run_dir = self.activate_existing(root, input_ref="pangea-data/inbox/design.md")
            catalog_path = workspace / "library/catalog.jsonl"
            record = data_runtime._read_jsonl(catalog_path)[0]
            markdown = workspace / record["markdown_path"]
            lines = markdown.read_bytes().splitlines(keepends=True)
            excerpt = b"".join(lines[:2])
            material = {"material_id": "MAT-1", "source_ref": "design.md", "source_sha256": record["sha256"],
                        "decision": "selected", "reason": "该设计定义认证失败后的资源恢复要求",
                        "markdown_path": record["markdown_path"], "markdown_sha256": hashlib.sha256(markdown.read_bytes()).hexdigest(),
                        "consumed_anchors": [{"start_line": 1, "end_line": 2,
                            "excerpt_sha256": hashlib.sha256(excerpt).hexdigest(),
                            "claim": "设计要求认证失败后释放会话资源"}], "limitations": []}
            catalog = {"path": "library/catalog.jsonl", "sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest()}
            self.stage(root, run_dir, self.payload(root, run_dir, materials=[material], catalog=catalog))

    def activate_existing(self, root: Path, input_ref: str) -> Path:
        draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                         "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "material")
        contract = draft["task_contract"]; contract["input_refs"] = [input_ref]
        path = root / "material-contract.json"; path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
        self.cli(root, "revise-contract-v2", "--contract-id", "material", "--expected-revision", "1", "--file", str(path))
        self.cli(root, "confirm-contract-v2", "--contract-id", "material", "--revision", "2",
                 "--source", "user_reply", "--materials-status", "provided")
        return Path(self.cli(root, "activate-contract-v2", "--contract-id", "material", "--run-id", "material-run")["run_dir"])

    def test_analysis_model_must_use_fixed_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root, contract_id="analysis")
            self.stage(root, run_dir, self.payload(root, run_dir))
            AnalysisDepthContractTests.complete_checkpoints(root, run_dir.name)
            model = AnalysisDepthContractTests.model(run_dir)
            model["evidence_consumption"][0]["source_ref"] = "EV-1"
            for collection in ("entrypoints", "flows", "branches", "states", "resources", "concurrency", "error_chains"):
                for item in model[collection]: item["source_evidence"] = ["EV-UNKNOWN"]
            path = root / "analysis.json"; path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("未知固定源码证据", rejected["stderr"])


if __name__ == "__main__":
    unittest.main()
