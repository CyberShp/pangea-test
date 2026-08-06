from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import runctl

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"


class RunCtlTests(unittest.TestCase):
    def run_cli(self, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PANGEA_VALIDATOR"] = "stdlib"
        result = subprocess.run(
            [sys.executable, str(RUNCTL), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(expect, result.returncode, msg=result.stderr or result.stdout)
        return result

    def test_v1_run_protocol_commands_are_not_executable(self) -> None:
        help_result = self.run_cli("--help")
        for retired in ("init", "put-artifact", "apply-audit", "resume"):
            self.assertNotIn(f"{retired} ", help_result.stdout)
            result = self.run_cli(retired, expect=2)
            self.assertIn("invalid choice", result.stderr)
        for active in ("create-v2", "resume-v2", "record-rework-v2", "apply-audit-v2", "finalize-v2"):
            self.assertIn(active, help_result.stdout)

    def test_stdlib_validator_rejects_invalid_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "invalid.json"
            invalid.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
            result = self.run_cli(
                "validate",
                "--file", str(invalid),
                "--schema", "task-envelope.schema.json",
                expect=2,
            )
            self.assertIn("缺少必填字段", result.stderr)

    def test_audit_opinion_requires_actionable_findings_and_nonblank_action_reason(self) -> None:
        opinion = {
            "artifact_type": "audit_opinion", "schema_version": "2.0",
            "audited_artifact": "internal/report-model.json", "audited_sha256": "0" * 64,
            "verdict": "CONCERNS",
            "checks": {
                name: {"verdict": "PASS", "violations": [], "gaps": []}
                for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")
            },
            "required_actions": [{
                "action_type": "add_evidence",
                "reason": "补充可复核的连接恢复日志证据",
                "anchor": "risks[0].evidence",
                "verification": "复核新增日志与连接恢复结论一致",
            }],
        }
        opinion["checks"]["coverage"] = {
            "verdict": "CONCERNS",
            "violations": [{"anchor": "risks[0]", "issue": "缺少外部观测", "impact": "无法确认恢复", "verification": "补充日志并复跑"}],
            "gaps": [],
        }
        for mode in ("stdlib", "jsonschema"):
            with self.subTest(mode=mode, condition="valid"), patch.dict(os.environ, {"PANGEA_VALIDATOR": mode}):
                try:
                    runctl.validate(opinion, "audit-opinion.schema.json")
                except runctl.RunCtlError as exc:
                    if mode == "jsonschema" and "未安装" in str(exc):
                        self.skipTest("jsonschema is not installed")
                    raise
            for mutation in (
                lambda value: value["required_actions"][0].__setitem__("reason", " \t "),
                lambda value: value["required_actions"][0].__setitem__("reason", "x"),
                lambda value: value["required_actions"][0].__setitem__("reason", "x       "),
                lambda value: value["required_actions"][0].__setitem__("action_type", "x"),
                lambda value: value["required_actions"][0].pop("anchor"),
                lambda value: value["required_actions"][0].__setitem__("verification", "x"),
                lambda value: value["checks"]["coverage"].__setitem__("violations", [{}]),
                lambda value: value["checks"]["coverage"]["violations"][0].__setitem__("verification", ""),
                lambda value: value["checks"]["coverage"].update({
                    "violations": [],
                    "gaps": [{"anchor": "risks[0]", "issue": "覆盖范围不明", "impact": "恢复结论不可验证", "verification": " "}],
                }),
                lambda value: value["checks"]["coverage"].update({"violations": [], "gaps": []}),
            ):
                invalid = json.loads(json.dumps(opinion))
                mutation(invalid)
                with self.subTest(mode=mode, condition=mutation), patch.dict(os.environ, {"PANGEA_VALIDATOR": mode}):
                    try:
                        with self.assertRaises(runctl.RunCtlError):
                            runctl.validate(invalid, "audit-opinion.schema.json")
                    except runctl.RunCtlError as exc:
                        if mode == "jsonschema" and "未安装" in str(exc):
                            self.skipTest("jsonschema is not installed")
                        raise

        for field, invalid in (("action_type", "x"), ("reason", "x"), ("anchor", "x"), ("verification", "x")):
            inconsistent = json.loads(json.dumps(opinion))
            inconsistent["required_actions"][0][field] = invalid
            with self.subTest(consistency_field=field), self.assertRaises(runctl.RunCtlError):
                runctl._assert_audit_consistency(inconsistent)
        missing_anchor = json.loads(json.dumps(opinion))
        missing_anchor["required_actions"][0].pop("anchor")
        with self.assertRaises(runctl.RunCtlError):
            runctl._assert_audit_consistency(missing_anchor)


if __name__ == "__main__":
    unittest.main()
