from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"
MANAGED = ROOT / "runtime" / "managed.py"
FIXTURE = ROOT / "tests" / "fixtures" / "mini-storage-module"
GOLDEN = ROOT / "tests" / "golden" / "mini-storage-plan.json"


class ManagedWorkflowTests(unittest.TestCase):
    def run_cli(self, script: Path, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PANGEA_VALIDATOR"] = "stdlib"
        result = subprocess.run(
            [sys.executable, str(script), *args], cwd=ROOT, env=env,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(expect, result.returncode, msg=result.stderr or result.stdout)
        return result

    def init_run(self, runs: Path, task_id: str = "managed-test", max_rounds: int = 2) -> Path:
        result = self.run_cli(
            RUNCTL, "init", "--target", "mini-storage-module",
            "--source-path", str(FIXTURE), "--runs-root", str(runs),
            "--task-id", task_id, "--max-audit-rounds", str(max_rounds),
        )
        return Path(json.loads(result.stdout)["run_dir"])

    def audit_opinion(self, run_dir: Path) -> Path:
        opinion = {
            "artifact_type": "audit_opinion",
            "schema_version": "1.0",
            "audited_artifact": "final/report.md",
            "verdict": "FAIL",
            "checks": {
                "traceability": {"verdict": "PASS", "violations": []},
                "blackbox_executability": {"verdict": "PASS", "violations": []},
                "coverage": {"verdict": "FAIL", "violations": [], "gaps": [{"item": "state transition"}]},
                "format_compliance": {"verdict": "PASS", "violations": []}
            },
            "required_actions": [
                {
                    "action_type": "re_excavate", "playbook": "状态机提取",
                    "target": "mini-storage-module", "lens": None,
                    "reason": "补齐 RECOVERING 到 CLOSED 转换证据", "ref_violation": "coverage.gaps[0]"
                },
                {
                    "action_type": "rewrite_case", "playbook": None,
                    "target": None, "lens": None,
                    "reason": "补齐恢复失败用例", "ref_violation": "coverage.gaps[0]"
                }
            ]
        }
        path = run_dir / "audit-input.json"
        path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
        return path

    def test_fixture_plan_matches_golden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(Path(tmp) / "runs")
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
            actual = [[item["artifact_id"], item["playbook"], item["lens"]] for item in manifest["planned_artifacts"]]
            self.assertEqual(golden["tasks"], actual)

    def test_smoke_init_creates_unique_runs(self) -> None:
        created: list[Path] = []
        try:
            first = json.loads(self.run_cli(MANAGED, "smoke-init").stdout)
            second = json.loads(self.run_cli(MANAGED, "smoke-init").stdout)
            self.assertNotEqual(first["task_id"], second["task_id"])
            created = [Path(first["run_dir"]), Path(second["run_dir"])]
            self.assertTrue(all(path.exists() for path in created))
        finally:
            for path in created:
                shutil.rmtree(path, ignore_errors=True)

    def test_managed_admission_rejects_manifest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(Path(tmp) / "runs")
            evidence = {
                "artifact_type": "code_evidence", "schema_version": "1.0",
                "artifact_id": "structure-01", "playbook": "分支枚举",
                "target": "mini-storage-module", "lens": None,
                "source_ref": {"repo_or_path": str(FIXTURE), "commit_or_mr": None},
                "status": "complete",
                "progress": {"done_steps": [1], "pending_steps": [], "resume_hint": None},
                "coverage_note": None, "findings": {}, "inferences": [], "open_questions": []
            }
            path = Path(tmp) / "evidence.json"
            path.write_text(json.dumps(evidence, ensure_ascii=False), encoding="utf-8")
            result = self.run_cli(
                MANAGED, "put-artifact", "--run-dir", str(run_dir),
                "--artifact-id", "structure-01", "--file", str(path), expect=2,
            )
            self.assertIn("playbook", result.stderr)

    def test_rework_plan_is_safe_idempotent_and_separates_manual_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(Path(tmp) / "runs")
            opinion = self.audit_opinion(run_dir)
            self.run_cli(RUNCTL, "apply-audit", "--run-dir", str(run_dir), "--file", str(opinion))
            first = json.loads(self.run_cli(MANAGED, "plan-rework", "--run-dir", str(run_dir)).stdout)
            self.assertTrue(first["automatic_rework_allowed"])
            self.assertEqual(1, len(first["next_tasks"]))
            self.assertEqual("状态机提取", first["next_tasks"][0]["playbook"])
            self.assertEqual(1, len(first["manual_actions"]))
            second = json.loads(self.run_cli(MANAGED, "plan-rework", "--run-dir", str(run_dir)).stdout)
            self.assertEqual(first, second)
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            reworks = [item for item in manifest["planned_artifacts"] if item.get("origin_audit_round") == 1]
            self.assertEqual(1, len(reworks))

    def test_max_audit_round_blocks_automatic_rework(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = self.init_run(Path(tmp) / "runs", max_rounds=1)
            opinion = self.audit_opinion(run_dir)
            self.run_cli(RUNCTL, "apply-audit", "--run-dir", str(run_dir), "--file", str(opinion))
            plan = json.loads(self.run_cli(MANAGED, "plan-rework", "--run-dir", str(run_dir)).stdout)
            self.assertFalse(plan["automatic_rework_allowed"])
            self.assertEqual([], plan["next_tasks"])
            self.assertEqual(2, len(plan["manual_actions"]))


if __name__ == "__main__":
    unittest.main()
