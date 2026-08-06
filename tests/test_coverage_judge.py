from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_analysis_report_projection import AnalysisReportProjectionTests

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"


class CoverageJudgeTests(unittest.TestCase):
    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, text=True, capture_output=True, check=False)

    def cli(self, *args: str) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def stage_report(self, root: Path) -> tuple[Path, dict]:
        helper = AnalysisReportProjectionTests()
        helper.cli_result = self.cli_result
        helper.cli = self.cli
        run_dir, risk = helper.prepare(root)
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        draft = {"title": "独立覆盖审查报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                 "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
        path = root / "draft.json"; path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
        return run_dir, staged

    def test_stage_report_writes_passed_independent_judge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, staged = self.stage_report(root)
            judge_path = run_dir / "internal/coverage-judge.json"
            judge = json.loads(judge_path.read_text(encoding="utf-8"))
            self.assertEqual("PASS", judge["verdict"])
            self.assertTrue(all(check["verdict"] == "PASS" for check in judge["checks"].values()))
            self.assertEqual(hashlib.sha256((run_dir / "internal/analysis-model.json").read_bytes()).hexdigest(),
                             judge["analysis_artifact"]["sha256"])
            self.assertEqual(str(judge_path), staged["coverage_judge"])

    def test_unknown_coverage_target_blocks_report_before_auditor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = AnalysisReportProjectionTests(); helper.cli_result = self.cli_result; helper.cli = self.cli
            run_dir, risk = helper.prepare(root)
            analysis_path = run_dir / "internal/analysis-model.json"
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            analysis["coverage_dispositions"][0]["covered_by"] = ["TC-UNKNOWN"]
            source = root / "bad-analysis.json"; source.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            self.cli("stage-analysis-v2", "--root", str(root), "--run-id", "depth", "--file", str(source))
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            draft_path = root / "draft.json"; draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(draft_path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("Coverage Judge 未通过", rejected.stderr)
            judge = json.loads((run_dir / "internal/coverage-judge.json").read_text(encoding="utf-8"))
            self.assertEqual("FAIL", judge["verdict"])
            self.assertTrue(judge["checks"]["breadth_disposition"]["findings"])

    def test_report_change_expires_judge_even_when_auditor_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, _ = self.stage_report(root)
            report_path = run_dir / "internal/report-model.json"
            report = json.loads(report_path.read_text(encoding="utf-8")); report["summary"] = "Judge 之后被修改"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0", "audited_artifact": "internal/report-model.json",
                       "audited_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(), "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            path = root / "audit.json"; path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("apply-audit-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("已过期", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
