"""Architecture v2 end-to-end contract tests using only real local CLIs.

The test root is disposable.  The product repository is never used as a data
workspace and the temporary source repository is fingerprinted before and
after each workflow to enforce PANGEA's read-only analysis boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any

from tests.test_analysis_depth_contract import AnalysisDepthContractTests


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "v2-office" / "word" / "document.xml"
RUNCTL = ROOT / "runtime" / "runctl.py"
DFX = ["功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性"]
REPORT_CHAPTERS = {
    "任务契约与覆盖边界", "代码地图", "关键业务流程", "异常分支及进入方式", "全量风险账本",
    "测试场景", "测试用例", "风险与用例覆盖映射", "代码证据附录", "未闭环项与下一步建议",
}


class ArchitectureV2EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "registry").mkdir()
        for name in ("workflows.json", "scenarios.json"):
            shutil.copy2(ROOT / "registry" / name, self.root / "registry" / name)
        self.repo = self.root / "pangea-data" / "repositories" / "driver"
        self.repo.mkdir(parents=True)
        (self.repo / "driver.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
        self.git("init")
        self.git("config", "user.email", "pangea-e2e@example.invalid")
        self.git("config", "user.name", "PANGEA E2E")
        self.git("add", "driver.c")
        self.git("commit", "-m", "initial")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, expect: int = 0) -> dict[str, Any]:
        env = os.environ.copy()
        env["PANGEA_VALIDATOR"] = os.environ.get("PANGEA_VALIDATOR", "stdlib")
        result = subprocess.run(
            [sys.executable, "-m", "tooling.pangea_cli", *args], cwd=ROOT, env=env,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(expect, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def runctl(self, *args: str) -> dict[str, Any]:
        env = os.environ.copy()
        env["PANGEA_VALIDATOR"] = os.environ.get("PANGEA_VALIDATOR", "stdlib")
        result = subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, env=env,
                                text=True, capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def create_v2(self, scenario: str, target: str, run_id: str, *extra: str) -> dict[str, Any]:
        commits: tuple[str, ...] = ()
        if scenario == "mr-regression":
            commits = ("--repository-commit", f"driver={self.git('rev-parse', 'HEAD')}")
        return self.runctl(
            "create-v2", "--root", str(self.root), "--scenario", scenario,
            "--target", target, "--repository", "driver", *commits, "--run-id", run_id, *extra,
        )

    def git(self, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(self.repo), *args], text=True,
                                capture_output=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def repo_fingerprint(self) -> dict[str, str]:
        return {
            "head": self.git("rev-parse", "HEAD"),
            "status": self.git("status", "--porcelain"),
            "driver_sha256": hashlib.sha256((self.repo / "driver.c").read_bytes()).hexdigest(),
        }

    def make_office_input(self) -> Path:
        inbox = self.root / "pangea-data" / "inbox"
        inbox.mkdir(parents=True)
        document = inbox / "requirements.docx"
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", FIXTURE.read_bytes())
        return document

    @staticmethod
    def checkpoint(stage: str, fact: str) -> dict[str, Any]:
        if stage == "dfx_scan":
            return {"stage": stage, "facts": [
                {"dfx": dfx, "conclusion": f"{dfx}维度已完成风险结论", "evidence": f"driver.c:1 对应{dfx}证据"}
                for dfx in DFX
            ], "open_items": [], "next_step": "继续下一阶段"}
        return {"stage": stage, "facts": [{"summary": fact, "evidence": f"driver.c:1 支持{fact}的源码证据"}],
                "open_items": [], "next_step": "继续下一阶段"}

    @staticmethod
    def risk(risk_id: str, translation: str) -> dict[str, Any]:
        return {
            "artifact_type": "risk_card",
            "schema_version": "1.0",
            "risk_id": risk_id,
            "title": "压力回落后业务未恢复" if risk_id == "R-RECOVER" else "关联微码仓缺失",
            "dfx": ["资源与规格"], "severity": "Critical" if risk_id == "R-RECOVER" else "High",
            "confidence": "high" if risk_id == "R-RECOVER" else "medium",
            "trigger": "并发请求超过规格后逐步回落", "propagation": "可申请额度或状态未同步恢复",
            "external_impact": "新业务不能建立或 IOPS 长时间无法恢复",
            "observation": "观察 IOPS、连接状态和资源诊断计数", "recovery": "停止压力后应在线恢复",
            "translation_status": translation,
            "test_explanation": "以业务恢复、连接状态和资源诊断计数验证风险，不暴露源码实现细节。",
            "instrumentation_request": None,
            "evidence": [{"location": "driver.c:1 主流程入口", "observation": "连接状态与资源诊断计数"}],
            "status": "open",
        }

    def test_v2_cli_end_to_end(self) -> None:
        office = self.make_office_input()
        before = self.repo_fingerprint()

        prepared = self.cli("data", "--root", str(self.root), "session-prepare")
        self.assertTrue((self.root / "pangea-data" / "repositories").is_dir())
        self.assertEqual(1, prepared["inbox"]["added"])
        self.assertEqual(1, prepared["document_import"]["converted"])
        catalog = (self.root / "pangea-data" / "library" / "catalog.jsonl").read_text(encoding="utf-8")
        self.assertIn("requirements.docx", catalog)
        self.assertIn("converted", catalog)
        self.assertTrue(office.exists())

        module = self.create_v2(
            "module-analysis", "iscsi", "module-complete", "--analysis-depth", "complete",
            "--signal", "queue counter allocation",
        )
        self.assertEqual("complete", module["contract"]["analysis_depth"])
        self.assertEqual(DFX, module["plan"]["dfx_agents"])
        run_dir = Path(module["run_dir"])
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        contract = json.loads((run_dir / "internal" / "task-contract.json").read_text(encoding="utf-8"))
        self.assertEqual("active", manifest["status"])
        self.assertEqual("module_analysis", contract["mode"])
        self.assertEqual(["queue counter allocation"], contract["signals"])
        self.assertFalse(contract["resource_emphasis"])

        fast = self.create_v2(
            "module-analysis", "iscsi-fast", "module-fast", "--analysis-depth", "fast",
            "--signal", "state transition",
        )
        self.assertEqual("fast", fast["contract"]["analysis_depth"])
        self.assertEqual(DFX, fast["plan"]["dfx_agents"])
        self.assertIn("fast", fast["contract"]["analysis_depth"])

        mr = self.create_v2(
            "mr-regression", "iscsi", "mr-resource",
            "--mr-url", "https://git.example.invalid/storage/merge_requests/42",
            "--signal", "queue counter allocation",
        )
        self.assertEqual(["原场景回归", "改动功能验证", "影响链回归", "异常与恢复验证"], mr["plan"]["baseline_verification"])
        self.assertEqual(["功能与状态", "资源与规格"], mr["plan"]["dfx_agents"])
        self.assertLess(len(mr["plan"]["dfx_agents"]), len(DFX))
        snapshot = module["source_snapshots"]["snapshots"][0]
        self.assertEqual("driver", snapshot["manifest"]["repository"])
        self.assertEqual(module["contract"]["repository_commits"]["driver"], snapshot["manifest"]["commit_sha"])

        for stage, fact in (("code_map", "入口已定位"), ("flow", "关键流程已展开")):
            self.cli("data", "--root", str(self.root), "checkpoint", "--run-id", "module-complete",
                     "--json", json.dumps(self.checkpoint(stage, fact), ensure_ascii=False))
        resumed = self.runctl("resume-v2", "--root", str(self.root), "--run-id", "module-complete")
        self.assertEqual("branches", resumed["next_stage"])
        self.assertEqual("002-flow.json", resumed["last_checkpoint"])
        self.assertEqual(["branches", "dfx_scan", "specialist", "sfmea", "test_design", "report"], resumed["pending_stages"])

        for stage, fact in (
            ("branches", "异常分支已枚举"), ("dfx_scan", "六个 DFX 已路由"),
            ("specialist", "资源专项已审阅"), ("sfmea", "风险影响已定级"),
            ("test_design", "黑盒场景已设计"),
        ):
            self.cli("data", "--root", str(self.root), "checkpoint", "--run-id", "module-complete",
                     "--json", json.dumps(self.checkpoint(stage, fact), ensure_ascii=False))

        canonical_risks = [self.risk("R-RECOVER", "Graybox-ready"), self.risk("R-CONFIRM", "Developer-confirm")]
        for risk in canonical_risks:
            self.cli("data", "--root", str(self.root), "upsert-risk", "--run-id", "module-complete",
                     "--json", json.dumps(risk, ensure_ascii=False))
        ledger = json.loads((run_dir / "internal" / "risk-ledger.json").read_text(encoding="utf-8"))
        self.assertEqual(["R-CONFIRM", "R-RECOVER"], [item["risk_id"] for item in ledger["risks"]])

        model = {
            "task_contract": contract,
            "title": "iSCSI 模块测试分析", "summary": "以黑盒业务恢复为主，灰盒插桩用于制造协议时序窗口。",
            "code_map": [{"title": "连接处理", "test_explanation": "主机连接进入接收就绪后才能稳定处理业务报文。", "source_evidence": "driver.c:1"}],
            "flows": [{"title": "连接恢复", "test_explanation": "压力解除后，新连接和 I/O 应在线恢复。", "source_evidence": "driver.c:1"}],
            "branches": [{"title": "额度回落", "test_explanation": "超过规格后回落时，系统不能永久拒绝新业务。", "source_evidence": "driver.c:1"}],
            "risks": canonical_risks,
            "test_cases": [{
                "case_id": "TC-RECOVER", "title": "规格压力回落后的在线恢复", "risk_ids": ["R-RECOVER"],
                "preconditions": "具备可控并发负载和资源诊断计数。",
                "steps": [
                    "将并发请求提升到规格上限以上，再逐步降回规格范围内。",
                    "在压力解除后持续建立新业务并发送 I/O。",
                    "观察 IOPS、连接状态和资源诊断计数是否在线恢复。",
                ],
                "expected": "无需断开全部连接或重启，业务能力和可申请额度均恢复正常。",
                "observation": "IOPS、连接状态、日志和资源诊断计数。", "cleanup": "停止负载并确认资源稳定。",
                "instrumentation": "控制语义：可将接收就绪状态延迟 0 至 5 秒；记录启停与报文到达时间；关闭后不残留连接状态。",
            }],
        }
        self.assertTrue(all("R-CONFIRM" not in case["risk_ids"] for case in model["test_cases"]))
        analysis_path = self.root / "analysis-model.json"
        complete_analysis = AnalysisDepthContractTests.model(run_dir)
        for scenario in complete_analysis["test_scenarios"]:
            scenario["risk_ids"] = ["R-RECOVER"]
        for case in complete_analysis["test_cases"]:
            case["risk_ids"] = ["R-RECOVER"]
        analysis_path.write_text(json.dumps(complete_analysis, ensure_ascii=False), encoding="utf-8")
        self.runctl("stage-analysis-v2", "--root", str(self.root), "--run-id", "module-complete",
                    "--file", str(analysis_path))
        draft_path = self.root / "report-model-draft.json"
        draft_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        self.runctl("stage-report-v2", "--root", str(self.root), "--run-id", "module-complete",
                    "--file", str(draft_path))
        model_path = run_dir / "internal" / "report-model.json"
        model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
        audit_path = self.root / "audit.json"
        audit_path.write_text(json.dumps({
            "artifact_type": "audit_opinion", "schema_version": "2.0",
            "audited_artifact": "internal/report-model.json", "audited_sha256": model_sha256,
            "verdict": "PASS", "required_actions": [],
            "checks": {
                name: {"verdict": "PASS", "violations": [], "gaps": []}
                for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")
            },
        }, ensure_ascii=False), encoding="utf-8")
        audited = self.runctl("apply-audit-v2", "--root", str(self.root), "--run-id", "module-complete", "--file", str(audit_path))
        self.assertEqual("PASS", audited["verdict"])
        final = self.runctl("finalize-v2", "--root", str(self.root), "--run-id", "module-complete", "--model", str(model_path))
        markdown = Path(final["report_md"]).read_text(encoding="utf-8")
        page = Path(final["report_html"]).read_text(encoding="utf-8")
        self.assertIn("风险 R-RECOVER](#risk-R-RECOVER)", markdown)
        self.assertIn('href="#case-TC-1"', page)
        self.assertIn('href="#risk-R-RECOVER"', page)
        self.assertIn("全部严重度", page)
        self.assertNotIn("https://", page)
        self.assertNotIn("cdn", page.lower())
        present = {re.sub(r"^[0-9]+[.] ", "", line[3:]) for line in markdown.splitlines() if line.startswith("## ")}
        self.assertSetEqual(REPORT_CHAPTERS - present, set(), "报告必须包含十个正式章节")

        self.assertEqual(before, self.repo_fingerprint(), "分析和报告流程不得写入目标源码仓")


if __name__ == "__main__":
    unittest.main()
