from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, repository_runtime


ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"


REPORT_MODEL = {
    "title": "连接模块全量测试报告",
    "summary": "验证连接建立、异常恢复和资源回落。",
    "code_map": [{"title": "连接处理", "test_explanation": "主机连接由建立进入业务就绪。",
                  "source_evidence": "connection.c: connection state"}],
    "flows": [{"title": "连接主流程", "test_explanation": "主机建立连接后发送业务报文。",
               "steps": ["建立连接", "进入业务就绪", "发送业务"], "source_evidence": "connection.c: establish path"}],
    "branches": [{"title": "资源不足", "test_explanation": "额度不足时拒绝新业务并在压力解除后恢复。",
                  "source_evidence": "resource.c: quota branch"}],
    "risks": [{"artifact_type": "risk_card", "schema_version": "1.0",
               "risk_id": "R-1", "title": "压力解除后额度不恢复", "severity": "High",
               "confidence": "high", "dfx": ["资源与规格"], "translation_status": "Graybox-ready",
               "test_explanation": "压力降回规格内后新业务仍可能无法建立。", "trigger": "连接数超过规格后回落",
               "propagation": "可申请额度持续减少", "external_impact": "新业务无法建立且 IOPS 不恢复",
               "observation": "IOPS、连接状态和资源计数", "recovery": "断开连接后应自动恢复",
               "instrumentation_request": None, "evidence": [{"location": "resource.c:42 配额回收分支",
                                                                  "observation": "资源计数与恢复日志"}],
               "status": "open"}],
    "scenarios": [{"scenario_id": "SC-1", "title": "超规格回落", "risk_ids": ["R-1"],
                   "description": "超过规格后逐步回落并观察恢复。", "trigger": "连接压力超过上限",
                   "expected": "回落后业务能力恢复"}],
    "test_cases": [{"case_id": "TC-1", "title": "连接压力回落", "risk_ids": ["R-1"],
                    "preconditions": "可控制连接压力", "steps": ["将连接压力提升到规格上限以上，再降回规格范围内。",
                    "持续观察新连接和业务 IOPS 是否自行恢复。"], "expected": "无需重启即可恢复",
                    "observation": "IOPS 和资源诊断计数", "cleanup": "停止压力并释放连接"}],
    "unresolved": [], "next_steps": []
}
MR_STAGES = ("code_map", "impact_chain", "mr_baseline", "dfx_route", "branches", "risk_ledger", "sfmea", "test_design")
DFX = ("功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性")


class WorkflowV2Tests(unittest.TestCase):
    @staticmethod
    def report_model(run_dir: str | Path, **overrides: object) -> dict:
        model = json.loads(json.dumps(REPORT_MODEL, ensure_ascii=False))
        model["task_contract"] = json.loads((Path(run_dir) / "internal" / "task-contract.json").read_text(encoding="utf-8"))
        model.update(overrides)
        return model

    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PANGEA_VALIDATOR"] = os.environ.get("PANGEA_VALIDATOR", "stdlib")
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, env=env,
                              text=True, capture_output=True, check=False)

    def cli_result_backend(self, backend: str, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PANGEA_VALIDATOR"] = backend
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, env=env,
                              text=True, capture_output=True, check=False)

    def cli(self, *args: str, expect: int = 0) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(expect, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    @staticmethod
    def opinion(path: Path, model: Path, verdict: str, required_actions: list[dict[str, object]],
                audited_artifact: str = "internal/report-model.json") -> Path:
        checks = {
            name: {
                "verdict": verdict if verdict != "PASS" and name == "coverage" else "PASS",
                "violations": [{"anchor": "report-model.risks", "issue": "需要补充覆盖事实",
                                "impact": "风险覆盖不可验证", "verification": "复核新增用例与风险映射"}]
                if verdict != "PASS" and name == "coverage" else [],
                "gaps": [],
            }
            for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")
        }
        digest = hashlib.sha256(model.read_bytes()).hexdigest()
        path.write_text(json.dumps({"artifact_type": "audit_opinion", "schema_version": "2.0",
                                    "audited_artifact": audited_artifact, "audited_sha256": digest, "verdict": verdict,
                                    "checks": checks, "required_actions": required_actions}, ensure_ascii=False), encoding="utf-8")
        return path

    @staticmethod
    def checkpoint(root: Path, run_id: str, stage: str, status: str = "completed", reason: str | None = None) -> None:
        facts: list[dict[str, str]] = [{"summary": f"{stage} 阶段已完成针对连接恢复路径的分析。",
                                        "evidence": f"internal/{stage}.json: 已记录对应分析证据。"}]
        if stage == "mr_baseline" and status == "completed":
            facts = [{"baseline": name, "verification": f"已验证{name}", "evidence": f"{name}-evidence"}
                     for name in ("原场景回归", "改动功能验证", "影响链回归", "异常与恢复验证")]
        if stage == "dfx_scan" and status == "completed":
            facts = [{"dfx": name, "conclusion": f"{name}风险已完成具体扫描", "evidence": f"{name}源码和运行证据已复核"}
                     for name in DFX]
        payload = {"stage": stage, "status": status, "facts": facts, "open_items": [],
                   "next_step": "继续下一阶段"}
        if reason:
            payload["skip_reason"] = reason
        data_runtime.append_checkpoint(root, run_id, payload)
        if stage == "sfmea" and status == "completed":
            data_runtime.upsert_risk(root, run_id, REPORT_MODEL["risks"][0])

    def complete_mr_stages(self, root: Path, run_id: str) -> None:
        for stage in MR_STAGES:
            self.checkpoint(root, run_id, stage)

    @staticmethod
    def registered_repository(root: Path, name: str = "driver") -> Path:
        repository = data_runtime.ensure_layout(root) / "repositories" / name
        repository.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repository)], check=True, capture_output=True, text=True)
        (repository / "README.md").write_text("PANGEA test repository\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repository), "add", "README.md"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.email=test@example.invalid",
                        "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "initial"],
                       check=True, capture_output=True, text=True)
        return repository

    @staticmethod
    def repository_commit(root: Path, name: str) -> str:
        return subprocess.run(["git", "-C", str(root / "pangea-data" / "repositories" / name), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()

    @staticmethod
    def add_gitlink(repository: Path, linked_commit: str, path: str = "vendor/linked") -> str:
        subprocess.run(["git", "-C", str(repository), "update-index", "--add", "--cacheinfo",
                        f"160000,{linked_commit},{path}"], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(repository), "-c", "user.email=test@example.invalid",
                        "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "add gitlink"],
                       check=True, capture_output=True, text=True)
        return subprocess.run(["git", "-C", str(repository), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()

    def record_rework(self, root: Path, run_id: str, action_count: int) -> dict:
        payload = {
            "action_closures": [
                {"action_index": index, "closure": f"已在报告模型补全第 {index} 项审计要求的恢复判据",
                 "evidence": {"artifact": "internal/report-model.json", "location": f"risks[{index - 1}]",
                              "verification": f"已复核第 {index} 项风险的恢复判据已写入固定报告模型。"}}
                for index in range(1, action_count + 1)
            ]
        }
        path = root / "rework.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.cli("record-rework-v2", "--root", str(root), "--run-id", run_id, "--file", str(path))

    def test_mr_contract_routes_only_signalled_dfx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            payload = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression",
                               "--target", "iscsi", "--repository", "driver", "--mr-url", "https://mr/42",
                               "--repository-commit", f"driver={self.repository_commit(root, 'driver')}",
                               "--run-id", "mr-42", "--version", "v1", "--topology", "双控",
                               "--test-focus", "恢复", "--exclude", "安全", "--tool-gap", "GitNexus",
                               "--signal", "queue counter allocation")
            self.assertEqual("focused", payload["contract"]["analysis_depth"])
            self.assertEqual(["原场景回归", "改动功能验证", "影响链回归", "异常与恢复验证"], payload["plan"]["baseline_verification"])
            self.assertIn("branches", payload["plan"]["stages"])
            self.assertIn("资源与规格", payload["plan"]["dfx_agents"])
            self.assertNotIn("升级与兼容", payload["plan"]["dfx_agents"])
            persisted = json.loads((Path(payload["run_dir"]) / "internal" / "task-contract.json").read_text(encoding="utf-8"))
            self.assertEqual(["queue counter allocation"], persisted["signals"])
            self.assertFalse(persisted["resource_emphasis"])

    def test_create_rejects_missing_or_unexpected_repository_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            missing = self.cli_result("create-v2", "--root", tmp, "--scenario", "mr-regression",
                                      "--target", "iscsi", "--repository", "driver", "--mr-url", "https://mr/commit")
            self.assertEqual(2, missing.returncode)
            self.assertIn("--repository-commit", missing.stderr)
            unexpected = self.cli_result("create-v2", "--root", tmp, "--scenario", "module-analysis",
                                         "--target", "iscsi", "--repository", "driver", "--analysis-depth", "fast",
                                         "--repository-commit", f"driver={self.repository_commit(root, 'driver')}")
            self.assertEqual(2, unexpected.returncode)
            self.assertIn("模块分析不得携带", unexpected.stderr)

    def test_completed_fact_rework_and_dfx_semantics_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                               "--target", "connection", "--repository", "driver", "--run-id", "fact-gate",
                               "--analysis-depth", "fast")
            with self.assertRaises(data_runtime.DataRuntimeError):
                data_runtime.append_checkpoint(root, "fact-gate", {"stage": "code_map", "status": "completed",
                                                                      "facts": [{}], "open_items": [], "next_step": "继续"})
            with self.assertRaises(data_runtime.DataRuntimeError):
                data_runtime.append_checkpoint(root, "fact-gate", {"stage": "code_map", "status": "completed",
                                                                      "facts": [{"detail": "   "}], "open_items": [], "next_step": "继续"})
            with self.assertRaises(data_runtime.DataRuntimeError):
                data_runtime.append_checkpoint(root, "fact-gate", {"stage": "code_map", "status": "completed",
                                                                      "facts": [{"x": True}], "open_items": [], "next_step": "继续"})
            with self.assertRaises(data_runtime.DataRuntimeError):
                data_runtime.append_checkpoint(root, "fact-gate", {"stage": "code_map", "status": "completed",
                                                                      "facts": [{"summary": "aaaaaaaaaaaa", "evidence": "源码证据已复核。"}],
                                                                      "open_items": [], "next_step": "继续"})
            with self.assertRaises(data_runtime.DataRuntimeError):
                data_runtime.append_checkpoint(root, "fact-gate", {"stage": "code_map", "status": "completed",
                                                                      "facts": [{"summary": "aaaa", "evidence": "bbbb"}],
                                                                      "open_items": [], "next_step": "继续"})
            for summary, evidence in (
                ("事实事实事实结论", "证据证据证据位置"),
                ("结论事实事实事实", "位置证据证据证据"),
                ("事实，事实，事实，结论", "证据；证据；证据；位置"),
            ):
                with self.assertRaises(data_runtime.DataRuntimeError):
                    data_runtime.append_checkpoint(root, "fact-gate", {"stage": "code_map", "status": "completed",
                        "facts": [{"summary": summary, "evidence": evidence}], "open_items": [], "next_step": "继续"})
            for stage in ("code_map", "flow", "branches"):
                self.checkpoint(root, "fact-gate", stage)
            data_runtime.append_checkpoint(root, "fact-gate", {"stage": "dfx_scan", "status": "completed",
                                                                  "facts": [{"dfx": "功能与状态", "conclusion": "状态结论", "evidence": "state evidence"}],
                                                                  "open_items": [], "next_step": "继续"})
            for stage in ("specialist", "sfmea", "test_design"):
                self.checkpoint(root, "fact-gate", stage)
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            rejected_dfx = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "fact-gate",
                                           "--file", str(self.opinion(root / "pass.json", model, "PASS", [])))
            self.assertEqual(2, rejected_dfx.returncode)
            self.assertIn("六个 canonical DFX", rejected_dfx.stderr)

            manifest = json.loads((Path(created["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
            manifest["audit"].update({"status": "FAIL", "rounds": 1, "required_actions": [{"reason": "补充证据"}],
                                      "rework": {"audit_round": 1, "status": "required", "checkpoint_file": None}})
            (Path(created["run_dir"]) / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            rework = root / "weak-rework.json"
            rework.write_text(json.dumps({"action_closures": [{"action_index": 1, "closure": "bbbbbbbbbbbb",
                                                                  "evidence": {"artifact": "../report-model.json", "location": "risks[0]",
                                                                               "verification": "bbbbbbbbbbbb"}}]}), encoding="utf-8")
            rejected_rework = self.cli_result("record-rework-v2", "--root", tmp, "--run-id", "fact-gate", "--file", str(rework))
            self.assertEqual(2, rejected_rework.returncode)
            self.assertIn("具体 closure", rejected_rework.stderr)

            missing_artifact = root / "missing-artifact-rework.json"
            missing_artifact.write_text(json.dumps({"action_closures": [{"action_index": 1,
                "closure": "已补充恢复路径的实际业务验证条件。",
                "evidence": {"artifact": "evidence/not-created.json", "location": "risks[0].recovery",
                             "verification": "已复核恢复条件与风险卡中的观察指标一致。"}}]}, ensure_ascii=False), encoding="utf-8")
            rejected_artifact = self.cli_result("record-rework-v2", "--root", tmp, "--run-id", "fact-gate",
                                                "--file", str(missing_artifact))
            self.assertEqual(2, rejected_artifact.returncode)
            self.assertIn("安全相对 artifact", rejected_artifact.stderr)

            valid_rework = root / "valid-rework.json"
            valid_rework.write_text(json.dumps({"action_closures": [{"action_index": 1,
                                                                        "closure": "已补充恢复路径的实际业务验证条件。",
                                                                        "evidence": {"artifact": "internal/report-model.json", "location": "risks[0].recovery",
                                                                                     "verification": "已复核恢复条件与风险卡中的观察指标一致。"}}]}, ensure_ascii=False), encoding="utf-8")
            accepted_rework = self.cli("record-rework-v2", "--root", tmp, "--run-id", "fact-gate", "--file", str(valid_rework))
            self.assertEqual(1, accepted_rework["closed_actions"])

    def test_report_risks_and_mr_snapshot_commit_must_match_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = self.registered_repository(root, "repo")
            expected = self.repository_commit(root, "repo")
            created = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression", "--target", "iscsi",
                               "--repository", "repo", "--repository-commit", f"repo={expected}",
                               "--mr-url", "https://mr/old", "--run-id", "commit-gate")
            self.complete_mr_stages(root, "commit-gate")
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            (repo / "new.txt").write_text("new commit\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "new.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid", "-c", "user.name=PANGEA Test",
                            "commit", "--quiet", "-m", "new"], check=True)
            repository_runtime.create_snapshot(root, "commit-gate", "repo")
            rejected_commit = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "commit-gate",
                                              "--file", str(self.opinion(root / "pass.json", model, "PASS", [])))
            self.assertEqual(2, rejected_commit.returncode)
            self.assertIn("精确绑定任务契约 commit", rejected_commit.stderr)

            ledger = Path(created["run_dir"]) / "internal" / "risk-ledger.json"
            payload = json.loads(ledger.read_text(encoding="utf-8"))
            original_risks = payload["risks"]
            payload["risks"] = []
            ledger.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            repository_runtime.create_snapshot(root, "commit-gate", "repo", expected, "expected-commit")
            rejected_duplicate = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "commit-gate",
                                                 "--file", str(self.opinion(root / "pass-duplicate.json", model, "PASS", [])))
            self.assertEqual(2, rejected_duplicate.returncode)
            self.assertIn("恰好一个权威快照", rejected_duplicate.stderr)
            repository_runtime.cleanup_snapshot(root, "commit-gate", "repo")
            rejected_ledger = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "commit-gate",
                                              "--file", str(self.opinion(root / "pass-empty-ledger.json", model, "PASS", [])))
            self.assertEqual(2, rejected_ledger.returncode)
            self.assertIn("risk_id 集合不一致", rejected_ledger.stderr)

            payload["risks"] = original_risks
            ledger.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tampered_model = self.report_model(created["run_dir"])
            tampered_model["risks"][0]["evidence"] = [{"location": "forged.c:999 伪造位置", "observation": "伪造观察"}]
            model.write_text(json.dumps(tampered_model, ensure_ascii=False), encoding="utf-8")
            rejected_evidence = self.cli_result(
                "apply-audit-v2", "--root", tmp, "--run-id", "commit-gate",
                "--file", str(self.opinion(root / "pass-forged-evidence.json", model, "PASS", [])),
            )
            self.assertEqual(2, rejected_evidence.returncode)
            self.assertIn("完整 canonical 内容不一致", rejected_evidence.stderr)

    def test_report_risk_binding_validates_ledger_schema_and_run_provenance_with_both_backends(self) -> None:
        for backend in ("stdlib", "jsonschema"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.registered_repository(root)
                run_id = f"ledger-binding-{backend}"
                created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                                   "--target", "connection", "--repository", "driver", "--run-id", run_id,
                                   "--analysis-depth", "fast")
                for stage in ("code_map", "flow", "branches", "dfx_scan", "specialist", "sfmea", "test_design"):
                    self.checkpoint(root, run_id, stage)
                run_dir = Path(created["run_dir"])
                model = run_dir / "internal" / "report-model.json"
                model.write_text(json.dumps(self.report_model(run_dir), ensure_ascii=False), encoding="utf-8")
                ledger_path = run_dir / "internal" / "risk-ledger.json"
                canonical = json.loads(ledger_path.read_text(encoding="utf-8"))

                wrong_run = {**canonical, "run_id": "another-run"}
                ledger_path.write_text(json.dumps(wrong_run, ensure_ascii=False), encoding="utf-8")
                rejected_run = self.cli_result_backend(
                    backend, "apply-audit-v2", "--root", tmp, "--run-id", run_id,
                    "--file", str(self.opinion(root / f"wrong-run-{backend}.json", model, "PASS", [])),
                )
                self.assertEqual(2, rejected_run.returncode, rejected_run.stderr)
                self.assertIn("risk-ledger.run_id", rejected_run.stderr)

                malformed = {**canonical, "unexpected": True}
                ledger_path.write_text(json.dumps(malformed, ensure_ascii=False), encoding="utf-8")
                rejected_schema = self.cli_result_backend(
                    backend, "apply-audit-v2", "--root", tmp, "--run-id", run_id,
                    "--file", str(self.opinion(root / f"bad-schema-{backend}.json", model, "PASS", [])),
                )
                self.assertEqual(2, rejected_schema.returncode, rejected_schema.stderr)
                self.assertIn("unexpected", rejected_schema.stderr)

    def test_finalize_requires_all_stages_and_passed_reaudit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                               "--target", "connection", "--repository", "driver", "--run-id", "module-fast",
                               "--analysis-depth", "fast", "--signal", "memory pool")
            self.assertEqual(6, len(created["plan"]["dfx_agents"]))
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")

            bypass = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
            self.assertEqual(2, bypass.returncode)
            self.assertIn("mandatory stages", bypass.stderr)

            for stage in ("code_map", "code_map", "flow", "branches", "dfx_scan"):
                self.checkpoint(root, "module-fast", stage)
            self.checkpoint(root, "module-fast", "specialist", "skipped", "无专项")
            invalid_skip = self.cli_result("resume-v2", "--root", tmp, "--run-id", "module-fast")
            self.assertEqual(2, invalid_skip.returncode)
            self.assertIn("未命中专项", invalid_skip.stderr)
            self.checkpoint(root, "module-fast", "specialist", "skipped", "快速模式未命中专项深挖信号")
            for stage in ("sfmea", "test_design"):
                self.checkpoint(root, "module-fast", stage)
            resumed = self.cli("resume-v2", "--root", tmp, "--run-id", "module-fast")
            self.assertEqual(["report"], resumed["pending_stages"])
            self.assertEqual("audit", resumed["next_stage"])

            action = [{"action_type": "rewrite_case", "playbook": None, "target": None, "lens": None,
                       "reason": "补充异常场景的业务恢复判据", "anchor": "test_cases[0].expected",
                       "verification": "复核用例包含外部可观察的恢复结果", "ref_violation": "coverage"}]
            absolute = self.opinion(root / "audit-absolute.json", model, "FAIL", action, str(model))
            rejected_absolute = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "module-fast", "--file", str(absolute))
            self.assertEqual(2, rejected_absolute.returncode)
            self.assertIn("internal/report-model.json", rejected_absolute.stderr)
            traversal = self.opinion(root / "audit-traversal.json", model, "FAIL", action, "../report-model.json")
            rejected_traversal = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "module-fast", "--file", str(traversal))
            self.assertEqual(2, rejected_traversal.returncode)
            self.assertIn("internal/report-model.json", rejected_traversal.stderr)
            failed = self.opinion(root / "audit-fail.json", model, "FAIL", action)
            first = self.cli("apply-audit-v2", "--root", tmp, "--run-id", "module-fast", "--file", str(failed))
            self.assertEqual(action, first["required_actions"])
            blocked = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
            self.assertEqual(2, blocked.returncode)
            self.assertIn("尚未 PASS", blocked.stderr)

            passed = self.opinion(root / "audit-pass.json", model, "PASS", [])
            bypass_rework = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "module-fast", "--file", str(passed))
            self.assertEqual(2, bypass_rework.returncode)
            self.assertIn("rework", bypass_rework.stderr)
            needs_rework = self.cli("resume-v2", "--root", tmp, "--run-id", "module-fast")
            self.assertEqual("rework", needs_rework["next_stage"])
            manifest_path = root / "pangea-data" / "runs" / "module-fast" / "manifest.json"
            before_rework = json.loads(manifest_path.read_text(encoding="utf-8"))["checkpoint_count"]
            self.record_rework(root, "module-fast", len(action))
            after_rework = json.loads(manifest_path.read_text(encoding="utf-8"))["checkpoint_count"]
            self.assertEqual(before_rework + 1, after_rework)
            rework_checkpoint = root / "pangea-data" / "runs" / "module-fast" / "checkpoints" / f"{after_rework:03d}-rework.json"
            self.assertTrue(rework_checkpoint.exists())
            self.checkpoint(root, "module-fast", "test_design")
            after_followup = json.loads(manifest_path.read_text(encoding="utf-8"))["checkpoint_count"]
            self.assertEqual(after_rework + 1, after_followup)
            self.assertTrue(rework_checkpoint.exists(), "后续检查点不得覆盖 rework 检查点")
            self.assertTrue((rework_checkpoint.parent / f"{after_followup:03d}-test_design.json").exists())
            same_hash = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "module-fast", "--file", str(passed))
            self.assertEqual(2, same_hash.returncode)
            self.assertIn("不同的 report-model SHA-256", same_hash.stderr)
            model.write_text(json.dumps(self.report_model(created["run_dir"], summary="整改后补充异常恢复判据"), ensure_ascii=False), encoding="utf-8")
            passed = self.opinion(root / "audit-pass-after-rework.json", model, "PASS", [])
            self.cli("apply-audit-v2", "--root", tmp, "--run-id", "module-fast", "--file", str(passed))
            ready = self.cli("resume-v2", "--root", tmp, "--run-id", "module-fast")
            self.assertEqual("report", ready["next_stage"])
            external_model = root / "external-model.json"
            external_model.write_bytes(model.read_bytes())
            rejected_external = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(external_model))
            self.assertEqual(2, rejected_external.returncode)
            self.assertIn("固定文件", rejected_external.stderr)
            model.write_text(json.dumps(self.report_model(created["run_dir"], summary="审计通过后被篡改"), ensure_ascii=False), encoding="utf-8")
            tampered = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
            self.assertEqual(2, tampered.returncode)
            self.assertIn("变更", tampered.stderr)
            model.write_text(json.dumps(self.report_model(created["run_dir"], summary="整改后补充异常恢复判据"), ensure_ascii=False), encoding="utf-8")
            repository = root / "pangea-data" / "repositories" / "driver"
            (repository / "snapshot-input.txt").write_text("MR snapshot\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "snapshot-input.txt"], check=True)
            subprocess.run(["git", "-C", str(repository), "-c", "user.email=test@example.invalid",
                            "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "snapshot input"], check=True)
            source_before = hashlib.sha256((repository / "snapshot-input.txt").read_bytes()).hexdigest()
            snapshot = repository_runtime.create_snapshot(root, "module-fast", "driver", "HEAD", "mr-revision")
            before_finalize = self.cli("resume-v2", "--root", tmp, "--run-id", "module-fast")
            snapshot_commits = {item["commit_sha"] for item in before_finalize["snapshots"]["snapshots"]}
            self.assertIn(snapshot["manifest"]["commit_sha"], snapshot_commits)
            reports_root = root / "pangea-data" / "reports"
            reports_root.mkdir()
            report_dir = reports_root / "module-fast"
            external = root / "external-final"
            external.mkdir()
            marker = external / "report.md"
            marker.write_text("outside\n", encoding="utf-8")
            report_dir.symlink_to(external, target_is_directory=True)
            rejected_final_link = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
            self.assertEqual(2, rejected_final_link.returncode)
            self.assertIn("正式报告目录已存在", rejected_final_link.stderr)
            self.assertEqual("outside\n", marker.read_text(encoding="utf-8"))
            report_dir.unlink()
            final = self.cli("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
            self.assertTrue(Path(final["report_md"]).exists())
            self.assertTrue(Path(final["report_html"]).exists())
            manifest = json.loads((root / "pangea-data" / "runs" / "module-fast" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("completed", manifest["status"])
            self.assertEqual("PASS", manifest["audit"]["status"])
            self.assertFalse((root / "pangea-data" / "runs" / "module-fast" / "tmp").exists())
            self.assertEqual(source_before, hashlib.sha256((repository / "snapshot-input.txt").read_bytes()).hexdigest())

    def test_audit_requires_canonical_task_contract_and_nonempty_branches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                               "--target", "connection", "--repository", "driver", "--run-id", "contract-gate",
                               "--analysis-depth", "complete")
            for stage in ("code_map", "flow", "branches", "dfx_scan", "specialist", "sfmea", "test_design"):
                self.checkpoint(root, "contract-gate", stage)
            model_path = Path(created["run_dir"]) / "internal" / "report-model.json"

            missing_contract = self.report_model(created["run_dir"])
            missing_contract.pop("task_contract")
            model_path.write_text(json.dumps(missing_contract, ensure_ascii=False), encoding="utf-8")
            rejected_missing = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "contract-gate",
                                               "--file", str(self.opinion(root / "missing-contract.json", model_path, "PASS", [])))
            self.assertEqual(2, rejected_missing.returncode)
            self.assertIn("缺少 task_contract", rejected_missing.stderr)

            mismatched_contract = self.report_model(created["run_dir"])
            mismatched_contract["task_contract"]["target"] = "different-target"
            model_path.write_text(json.dumps(mismatched_contract, ensure_ascii=False), encoding="utf-8")
            rejected_mismatch = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "contract-gate",
                                                "--file", str(self.opinion(root / "mismatched-contract.json", model_path, "PASS", [])))
            self.assertEqual(2, rejected_mismatch.returncode)
            self.assertIn("canonical 内容不一致", rejected_mismatch.stderr)

            blank_branches = self.report_model(created["run_dir"], branches=[])
            model_path.write_text(json.dumps(blank_branches, ensure_ascii=False), encoding="utf-8")
            rejected_branches = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "contract-gate",
                                                "--file", str(self.opinion(root / "blank-branches.json", model_path, "PASS", [])))
            self.assertEqual(2, rejected_branches.returncode)
            self.assertIn("branches", rejected_branches.stderr)

            canonical_path = Path(created["run_dir"]) / "internal" / "task-contract.json"
            blank_contract = json.loads(canonical_path.read_text(encoding="utf-8"))
            blank_contract["goal"] = "  "
            canonical_path.write_text(json.dumps(blank_contract, ensure_ascii=False), encoding="utf-8")
            model_path.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            rejected_blank = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "contract-gate",
                                             "--file", str(self.opinion(root / "blank-contract.json", model_path, "PASS", [])))
            self.assertEqual(2, rejected_blank.returncode)
            self.assertIn("空白或占位值", rejected_blank.stderr)

    def test_audit_round_limit_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root, "repo")
            created = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression", "--target", "iscsi",
                               "--repository", "repo", "--repository-commit", f"repo={self.repository_commit(root, 'repo')}",
                               "--mr-url", "https://mr/1", "--run-id", "limited", "--max-audit-rounds", "1")
            self.complete_mr_stages(root, "limited")
            action = [{"action_type": "add_evidence", "reason": "补充连接恢复路径的可复核证据",
                       "anchor": "risks[0].evidence", "verification": "复核源码锚点与风险结论能够相互印证"}]
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            repository_runtime.create_snapshot(root, "limited", "repo")
            failed = self.opinion(root / "fail.json", model, "CONCERNS", action)
            self.cli("apply-audit-v2", "--root", tmp, "--run-id", "limited", "--file", str(failed))
            self.record_rework(root, "limited", len(action))
            model.write_text(json.dumps(self.report_model(created["run_dir"], summary="整改后版本"), ensure_ascii=False), encoding="utf-8")
            failed = self.opinion(root / "fail-after-rework.json", model, "CONCERNS", action)
            again = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "limited", "--file", str(failed))
            self.assertEqual(2, again.returncode)
            self.assertIn("最大审计轮数", again.stderr)

    def test_audit_rejects_v1_and_inconsistent_pass_opinions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                               "--target", "connection", "--repository", "driver", "--run-id", "audit-gate",
                               "--analysis-depth", "fast")
            for stage in ("code_map", "flow", "branches", "dfx_scan", "specialist", "sfmea", "test_design"):
                self.checkpoint(root, "audit-gate", stage)
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            digest = hashlib.sha256(model.read_bytes()).hexdigest()
            v1 = root / "v1-opinion.json"
            v1.write_text(json.dumps({
                "artifact_type": "audit_opinion", "schema_version": "1.0",
                "audited_artifact": "internal/report-model.json", "audited_sha256": digest,
                "verdict": "PASS", "required_actions": [],
                "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                           for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")},
            }), encoding="utf-8")
            rejected_v1 = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "audit-gate", "--file", str(v1))
            self.assertEqual(2, rejected_v1.returncode)
            self.assertIn("schema_version", rejected_v1.stderr)

            forged = root / "forged-pass.json"
            forged.write_text(json.dumps({
                "artifact_type": "audit_opinion", "schema_version": "2.0",
                "audited_artifact": "internal/report-model.json", "audited_sha256": digest,
                "verdict": "PASS", "required_actions": [],
                "checks": {
                    "traceability": {"verdict": "FAIL", "violations": [{"anchor": "report", "issue": "缺失追踪", "impact": "无法复核", "verification": "补齐锚点"}], "gaps": []},
                    "blackbox_executability": {"verdict": "FAIL", "violations": [{"anchor": "report", "issue": "缺失可执行性", "impact": "无法执行", "verification": "补齐步骤"}], "gaps": []},
                    "coverage": {"verdict": "FAIL", "violations": [{"anchor": "report", "issue": "缺失覆盖", "impact": "风险遗漏", "verification": "补齐用例"}], "gaps": []},
                    "format_compliance": {"verdict": "FAIL", "violations": [{"anchor": "report", "issue": "格式缺陷", "impact": "无法消费", "verification": "复核输出"}], "gaps": []},
                },
            }), encoding="utf-8")
            rejected_forged = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "audit-gate", "--file", str(forged))
            self.assertEqual(2, rejected_forged.returncode)
            self.assertIn("四维 checks 不一致", rejected_forged.stderr)

            inflated = root / "inflated-concerns.json"
            inflated.write_text(json.dumps({
                "artifact_type": "audit_opinion", "schema_version": "2.0",
                "audited_artifact": "internal/report-model.json", "audited_sha256": digest,
                "verdict": "CONCERNS", "required_actions": [{"action_type": "add_evidence",
                    "reason": "补充关注项对应的可复核证据", "anchor": "risks[0].evidence",
                    "verification": "复核新增证据能够支持对应风险结论"}],
                "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                           for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")},
            }, ensure_ascii=False), encoding="utf-8")
            rejected_inflated = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "audit-gate", "--file", str(inflated))
            self.assertEqual(2, rejected_inflated.returncode)
            self.assertIn("四维 checks 不一致", rejected_inflated.stderr)

    def test_audit_rejects_check_evidence_inconsistent_with_its_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                               "--target", "connection", "--repository", "driver", "--run-id", "check-gate",
                               "--analysis-depth", "fast")
            for stage in ("code_map", "flow", "branches", "dfx_scan", "specialist", "sfmea", "test_design"):
                self.checkpoint(root, "check-gate", stage)
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            forged = self.opinion(root / "forged-check.json", model, "PASS", [])
            payload = json.loads(forged.read_text(encoding="utf-8"))
            payload["checks"]["coverage"]["violations"] = [{"detail": "PASS 仍有缺陷"}]
            forged.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "check-gate", "--file", str(forged))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("violations", rejected.stderr)

    def test_mr_requires_snapshot_for_audit_and_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root, "repo")
            created = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression",
                               "--target", "iscsi", "--repository", "repo", "--mr-url", "https://mr/9",
                               "--repository-commit", f"repo={self.repository_commit(root, 'repo')}",
                               "--run-id", "mr-snapshot")
            self.complete_mr_stages(root, "mr-snapshot")
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            passed = self.opinion(root / "pass.json", model, "PASS", [])
            missing = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "mr-snapshot", "--file", str(passed))
            self.assertEqual(2, missing.returncode)
            self.assertIn("恰好一个权威快照", missing.stderr)

            repository_runtime.create_snapshot(root, "mr-snapshot", "repo")
            self.cli("apply-audit-v2", "--root", tmp, "--run-id", "mr-snapshot", "--file", str(passed))
            snapshot_file = root / "pangea-data" / "runs" / "mr-snapshot" / "tmp" / "snapshots" / "repo" / "README.md"
            snapshot_file.chmod(0o644)
            snapshot_file.write_text("tampered snapshot\n", encoding="utf-8")
            snapshot_file.chmod(0o444)
            tampered_after_audit = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "mr-snapshot", "--model", str(model))
            self.assertEqual(2, tampered_after_audit.returncode)
            self.assertIn("权威快照", tampered_after_audit.stderr)

    def test_mr_stages_reject_empty_facts_and_incomplete_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root, "repo")
            created = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression",
                               "--target", "iscsi", "--repository", "repo", "--mr-url", "https://mr/10",
                               "--repository-commit", f"repo={self.repository_commit(root, 'repo')}",
                               "--run-id", "mr-stage-gate")
            for stage in MR_STAGES:
                if stage == "mr_baseline":
                    payload = {"stage": stage, "status": "completed", "facts": [{"baseline": "原场景回归"}],
                               "open_items": [], "next_step": "继续"}
                    with self.assertRaises(data_runtime.DataRuntimeError):
                        data_runtime.append_checkpoint(root, "mr-stage-gate", payload)
                else:
                    self.checkpoint(root, "mr-stage-gate", stage)

    def test_mr_tampered_snapshot_is_rejected_before_audit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root, "repo")
            created = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression",
                               "--target", "iscsi", "--repository", "repo", "--mr-url", "https://mr/11",
                               "--repository-commit", f"repo={self.repository_commit(root, 'repo')}",
                               "--run-id", "mr-snapshot-audit")
            self.complete_mr_stages(root, "mr-snapshot-audit")
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            snapshot = repository_runtime.create_snapshot(root, "mr-snapshot-audit", "repo")
            snapshot_file = Path(snapshot["snapshot_dir"]) / "README.md"
            snapshot_file.chmod(0o644)
            snapshot_file.write_text("audit must reject this\n", encoding="utf-8")
            snapshot_file.chmod(0o444)
            rejected = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "mr-snapshot-audit",
                                       "--file", str(self.opinion(root / "pass.json", model, "PASS", [])))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("权威快照", rejected.stderr)

    def test_mr_rejects_snapshot_and_manifest_tampered_to_match_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root, "repo")
            created = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression",
                               "--target", "iscsi", "--repository", "repo", "--mr-url", "https://mr/12",
                               "--repository-commit", f"repo={self.repository_commit(root, 'repo')}",
                               "--run-id", "mr-source-binding")
            self.complete_mr_stages(root, "mr-source-binding")
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            snapshot = repository_runtime.create_snapshot(root, "mr-source-binding", "repo")
            snapshot_dir = Path(snapshot["snapshot_dir"])
            snapshot_file = snapshot_dir / "README.md"
            manifest_path = snapshot_dir / repository_runtime.MANIFEST_NAME
            snapshot_file.chmod(0o644); manifest_path.chmod(0o644)
            snapshot_file.write_text("not the archived commit\n", encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["content_sha256"] = repository_runtime._snapshot_content_sha256(snapshot_dir)
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            snapshot_file.chmod(0o444); manifest_path.chmod(0o444)
            rejected = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "mr-source-binding",
                                       "--file", str(self.opinion(root / "pass.json", model, "PASS", [])))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("权威快照", rejected.stderr)

    def test_mr_rejects_snapshot_for_uncontracted_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root, "driver")
            self.registered_repository(root, "extra")
            created = self.cli("create-v2", "--root", tmp, "--scenario", "mr-regression", "--target", "iscsi",
                               "--repository", "driver", "--repository-commit", f"driver={self.repository_commit(root, 'driver')}",
                               "--mr-url", "https://mr/extra", "--run-id", "mr-extra")
            self.complete_mr_stages(root, "mr-extra")
            repository_runtime.create_snapshot(root, "mr-extra", "driver")
            repository_runtime.create_snapshot(root, "mr-extra", "extra")
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "mr-extra",
                                       "--file", str(self.opinion(root / "extra.json", model, "PASS", [])))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("未契约仓快照", rejected.stderr)

    def test_mr_gitlink_is_closed_by_other_authoritative_contract_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            driver = self.registered_repository(root, "driver")
            self.registered_repository(root, "linked")
            linked_commit = self.repository_commit(root, "linked")
            driver_commit = self.add_gitlink(driver, linked_commit)
            created = self.cli(
                "create-v2", "--root", tmp, "--scenario", "mr-regression", "--target", "iscsi",
                "--repository", "driver", "--repository", "linked",
                "--repository-commit", f"driver={driver_commit}", "--repository-commit", f"linked={linked_commit}",
                "--mr-url", "https://mr/gitlink-closed", "--run-id", "gitlink-closed",
            )
            self.complete_mr_stages(root, "gitlink-closed")
            repository_runtime.create_snapshot(root, "gitlink-closed", "driver")
            repository_runtime.create_snapshot(root, "gitlink-closed", "linked")
            model = Path(created["run_dir"]) / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(created["run_dir"]), ensure_ascii=False), encoding="utf-8")
            audited = self.cli("apply-audit-v2", "--root", tmp, "--run-id", "gitlink-closed",
                               "--file", str(self.opinion(root / "gitlink-closed.json", model, "PASS", [])))
            self.assertEqual("PASS", audited["verdict"])

    def test_mr_unlinked_gitlink_requires_contract_and_report_gap_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            driver = self.registered_repository(root, "driver")
            self.registered_repository(root, "linked")
            linked_commit = self.repository_commit(root, "linked")
            driver_commit = self.add_gitlink(driver, linked_commit)
            gap = f"gitlink coverage gap: repository=driver; path=vendor/linked; commit_sha={linked_commit}"
            created = self.cli(
                "create-v2", "--root", tmp, "--scenario", "mr-regression", "--target", "iscsi",
                "--repository", "driver", "--repository-commit", f"driver={driver_commit}",
                "--mr-url", "https://mr/gitlink-gap", "--run-id", "gitlink-gap",
            )
            self.complete_mr_stages(root, "gitlink-gap")
            repository_runtime.create_snapshot(root, "gitlink-gap", "driver")
            run_dir = Path(created["run_dir"])
            model = run_dir / "internal" / "report-model.json"
            model.write_text(json.dumps(self.report_model(run_dir), ensure_ascii=False), encoding="utf-8")
            missing_contract = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "gitlink-gap",
                                               "--file", str(self.opinion(root / "missing-known-gap.json", model, "PASS", [])))
            self.assertEqual(2, missing_contract.returncode)
            self.assertIn("known_gaps", missing_contract.stderr)

            contract_path = run_dir / "internal" / "task-contract.json"
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["known_gaps"] = [gap]
            contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            model.write_text(json.dumps(self.report_model(run_dir), ensure_ascii=False), encoding="utf-8")
            missing_report = self.cli_result("apply-audit-v2", "--root", tmp, "--run-id", "gitlink-gap",
                                             "--file", str(self.opinion(root / "missing-report-gap.json", model, "PASS", [])))
            self.assertEqual(2, missing_report.returncode)
            self.assertIn("coverage_gaps 或 unresolved", missing_report.stderr)

            model.write_text(json.dumps(self.report_model(run_dir, unresolved=[gap]), ensure_ascii=False), encoding="utf-8")
            audited = self.cli("apply-audit-v2", "--root", tmp, "--run-id", "gitlink-gap",
                               "--file", str(self.opinion(root / "mapped-gap.json", model, "PASS", [])))
            self.assertEqual("PASS", audited["verdict"])

    def test_v2_operations_reject_empty_or_registry_drifted_workflow_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                               "--target", "connection", "--repository", "driver", "--run-id", "plan-gate",
                               "--analysis-depth", "fast")
            plan_path = Path(created["run_dir"]) / "internal" / "workflow-plan.json"
            canonical = created["plan"]
            drifted_dfx = json.loads(json.dumps(canonical, ensure_ascii=False))
            drifted_dfx["dfx_agents"] = ["功能与状态"]
            plan_path.write_text(json.dumps(drifted_dfx, ensure_ascii=False), encoding="utf-8")
            rejected_dfx = self.cli_result("resume-v2", "--root", tmp, "--run-id", "plan-gate")
            self.assertEqual(2, rejected_dfx.returncode)
            self.assertIn("canonical 计划", rejected_dfx.stderr)
            drifted_signals = json.loads(json.dumps(canonical, ensure_ascii=False))
            drifted_signals["signals"] = ["forged"]
            plan_path.write_text(json.dumps(drifted_signals, ensure_ascii=False), encoding="utf-8")
            rejected_signals = self.cli_result("resume-v2", "--root", tmp, "--run-id", "plan-gate")
            self.assertEqual(2, rejected_signals.returncode)
            self.assertIn("canonical 计划", rejected_signals.stderr)
            plan_path.write_text("{}\n", encoding="utf-8")
            resumed = self.cli_result("resume-v2", "--root", tmp, "--run-id", "plan-gate")
            self.assertEqual(2, resumed.returncode)
            self.assertIn("workflow plan", resumed.stderr)
            plan_path.write_text(json.dumps({"workflow": "unknown", "stages": []}), encoding="utf-8")
            unknown = self.cli_result("resume-v2", "--root", tmp, "--run-id", "plan-gate")
            self.assertEqual(2, unknown.returncode)
            self.assertIn("workflow", unknown.stderr)
            plan_path.write_text(json.dumps({"workflow": "module-analysis", "stages": ["code_map"]}), encoding="utf-8")
            finalized = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "plan-gate",
                                        "--model", str(Path(created["run_dir"]) / "internal" / "report-model.json"))
            self.assertEqual(2, finalized.returncode)
            self.assertIn("canonical 计划", finalized.stderr)

    def test_checkpoint_provenance_rejects_cross_run_sequence_manifest_and_order_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            first = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "first",
                             "--repository", "driver", "--run-id", "checkpoint-first", "--analysis-depth", "fast")
            second = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "second",
                              "--repository", "driver", "--run-id", "checkpoint-second", "--analysis-depth", "fast")
            self.checkpoint(root, "checkpoint-first", "code_map")
            self.checkpoint(root, "checkpoint-second", "code_map")
            source = Path(first["run_dir"]) / "checkpoints" / "001-code_map.json"
            copied = Path(second["run_dir"]) / "checkpoints" / "001-code_map.json"
            copied.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            rejected_cross_run = self.cli_result("resume-v2", "--root", tmp, "--run-id", "checkpoint-second")
            self.assertEqual(2, rejected_cross_run.returncode)
            self.assertIn("run_id 与当前 Run 不一致", rejected_cross_run.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "sequence",
                               "--repository", "driver", "--run-id", "checkpoint-sequence", "--analysis-depth", "fast")
            self.checkpoint(root, "checkpoint-sequence", "code_map")
            checkpoint_path = Path(created["run_dir"]) / "checkpoints" / "001-code_map.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["sequence"] = 2
            checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
            rejected_sequence = self.cli_result("resume-v2", "--root", tmp, "--run-id", "checkpoint-sequence")
            self.assertEqual(2, rejected_sequence.returncode)
            self.assertIn("sequence 与文件名不一致", rejected_sequence.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "manifest",
                               "--repository", "driver", "--run-id", "checkpoint-manifest", "--analysis-depth", "fast")
            self.checkpoint(root, "checkpoint-manifest", "code_map")
            manifest_path = Path(created["run_dir"]) / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["checkpoint_count"] = 2
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            rejected_manifest = self.cli_result("resume-v2", "--root", tmp, "--run-id", "checkpoint-manifest")
            self.assertEqual(2, rejected_manifest.returncode)
            self.assertIn("checkpoint_count", rejected_manifest.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "order",
                     "--repository", "driver", "--run-id", "checkpoint-order", "--analysis-depth", "fast")
            self.checkpoint(root, "checkpoint-order", "flow")
            self.checkpoint(root, "checkpoint-order", "code_map")
            rejected_order = self.cli_result("resume-v2", "--root", tmp, "--run-id", "checkpoint-order")
            self.assertEqual(2, rejected_order.returncode)
            self.assertIn("越过未完成阶段", rejected_order.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.registered_repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "unknown",
                               "--repository", "driver", "--run-id", "checkpoint-unknown", "--analysis-depth", "fast")
            self.checkpoint(root, "checkpoint-unknown", "code_map")
            checkpoint_path = Path(created["run_dir"]) / "checkpoints" / "001-code_map.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["stage"] = "unknown"
            checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False), encoding="utf-8")
            rejected_unknown = self.cli_result("resume-v2", "--root", tmp, "--run-id", "checkpoint-unknown")
            self.assertEqual(2, rejected_unknown.returncode)
            self.assertIn("未知或非本工作流", rejected_unknown.stderr)

    def test_create_v2_only_accepts_registered_git_worktree_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True, text=True)
            plain = data_runtime.ensure_layout(root) / "repositories" / "plain"
            plain.mkdir()
            self.registered_repository(root, "driver")
            rejected_path = self.cli_result("create-v2", "--root", tmp, "--scenario", "module-analysis",
                                            "--target", "connection", "--repository", "repositories/driver")
            self.assertEqual(2, rejected_path.returncode)
            self.assertIn("工作树名称", rejected_path.stderr)
            missing = self.cli_result("create-v2", "--root", tmp, "--scenario", "module-analysis",
                                      "--target", "connection", "--repository", "missing")
            self.assertEqual(2, missing.returncode)
            self.assertIn("未登记", missing.stderr)
            inherited_parent = self.cli_result("create-v2", "--root", tmp, "--scenario", "module-analysis",
                                               "--target", "connection", "--repository", "plain")
            self.assertEqual(2, inherited_parent.returncode)
            self.assertIn("独立 Git 工作树根目录", inherited_parent.stderr)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                               "--target", "connection", "--repository", "driver", "--run-id", "driver-valid")
            self.assertEqual(["driver"], created["contract"]["repositories"])

            source = root / "linked-source"
            source.mkdir()
            subprocess.run(["git", "init", "--quiet", str(source)], check=True, capture_output=True, text=True)
            subprocess.run(["git", "-C", str(source), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(source), "config", "user.name", "PANGEA Test"], check=True)
            (source / "tracked.txt").write_text("linked worktree\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(source), "commit", "--quiet", "-m", "initial"], check=True)
            linked = root / "pangea-data" / "repositories" / "linked"
            subprocess.run(["git", "-C", str(source), "worktree", "add", "--quiet", str(linked)], check=True)
            self.assertTrue((linked / ".git").is_file())
            linked_created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis",
                                      "--target", "connection", "--repository", "linked", "--run-id", "linked-valid")
            self.assertEqual(["linked"], linked_created["contract"]["repositories"])

            alias = root / "pangea-data" / "repositories" / "driver-alias"
            alias.symlink_to(root / "pangea-data" / "repositories" / "driver", target_is_directory=True)
            alias_rejected = self.cli_result("create-v2", "--root", tmp, "--scenario", "module-analysis",
                                             "--target", "connection", "--repository", "driver-alias")
            self.assertEqual(2, alias_rejected.returncode)
            self.assertIn("未登记", alias_rejected.stderr)


if __name__ == "__main__":
    unittest.main()
