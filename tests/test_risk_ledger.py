from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime


class RiskLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        data_runtime.create_run(self.root, "ledger-run", {"schema_version": "1.0", "mode": "module_analysis", "goal": "资源检查", "target": "iscsi", "repositories": ["driver"], "analysis_depth": "complete", "created_by": "pangea-test"})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def risk(self, risk_id: str = "R-1") -> dict[str, object]:
        return {"artifact_type": "risk_card", "schema_version": "1.0", "risk_id": risk_id, "title": "额度回落不恢复", "dfx": ["资源与规格", "性能与压力"], "severity": "High", "confidence": "medium", "trigger": "超过额度后回落", "propagation": "可申请额度减少", "external_impact": "业务 IOPS 未恢复", "observation": "IOPS 与资源计数", "recovery": "断连后恢复", "translation_status": "Graybox-ready", "evidence": [{"location": "resource.c:42 配额回收分支", "observation": "额度与 IOPS 恢复情况"}]}

    def test_checkpoint_and_risk_upsert_are_structured_and_idempotent(self) -> None:
        checkpoint = data_runtime.append_checkpoint(self.root, "ledger-run", {
            "stage": "code_map",
            "facts": [{"summary": "协议驱动入口已定位。", "evidence": "driver.c:1 记录入口源码锚点。"}],
            "open_items": ["调用链"], "next_step": "展开入口",
        })
        self.assertEqual(1, checkpoint["sequence"])
        risk = self.risk()
        data_runtime.upsert_risk(self.root, "ledger-run", risk)
        data_runtime.upsert_risk(self.root, "ledger-run", {**risk, "severity": "Critical"})
        ledger_path = self.root / "pangea-data" / "runs" / "ledger-run" / "internal" / "risk-ledger.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        self.assertEqual(1, len(ledger["risks"]))
        self.assertEqual("Critical", ledger["risks"][0]["severity"])

    def test_invalid_risk_is_rejected_before_write(self) -> None:
        with self.assertRaises(data_runtime.DataRuntimeError):
            data_runtime.upsert_risk(self.root, "ledger-run", {"risk_id": "bad"})

    def test_terminal_runs_reject_risk_updates_without_mutating_ledger(self) -> None:
        contract = {"schema_version": "1.0", "mode": "module_analysis", "goal": "资源检查", "target": "iscsi", "repositories": ["driver"], "analysis_depth": "complete", "created_by": "pangea-test"}
        for status in ("completed", "failed", "cancelled"):
            with self.subTest(status=status):
                run_id = f"terminal-{status}"
                created = data_runtime.create_run(self.root, run_id, contract)
                run_dir = Path(created["run_dir"])
                data_runtime.upsert_risk(self.root, run_id, self.risk())
                ledger_path = run_dir / "internal" / "risk-ledger.json"
                before = ledger_path.read_bytes()
                manifest = data_runtime.read_json(run_dir / "manifest.json")
                manifest["status"] = status
                data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)

                with self.assertRaisesRegex(data_runtime.DataRuntimeError, "已结束 Run"):
                    data_runtime.upsert_risk(self.root, run_id, {**self.risk(), "severity": "Critical"})

                self.assertEqual(before, ledger_path.read_bytes())

    def test_invalid_existing_ledger_is_rejected_without_rewrite(self) -> None:
        run_dir = self.root / "pangea-data" / "runs" / "ledger-run"
        ledger_path = run_dir / "internal" / "risk-ledger.json"
        valid = data_runtime.read_json(ledger_path)
        cases = []
        cases.append({**valid, "run_id": "other-run"})
        cases.append({**valid, "risks": [{**self.risk(), "unexpected": True}]})
        cases.append({**valid, "risks": [self.risk(), {**self.risk(), "severity": "Critical"}]})
        for ledger in cases:
            with self.subTest(ledger=ledger):
                data_runtime.atomic_write_json(ledger_path, ledger)
                before = ledger_path.read_bytes()
                with self.assertRaises(data_runtime.DataRuntimeError):
                    data_runtime.upsert_risk(self.root, "ledger-run", self.risk("R-2"))
                self.assertEqual(before, ledger_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
