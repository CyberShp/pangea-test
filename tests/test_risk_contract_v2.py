from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import runctl


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "risk-card.schema.json"
SKILL_PATH = ROOT / ".opencode" / "skills" / "risk-card" / "SKILL.md"
CAPABILITY_PATH = ROOT / "core" / "capabilities" / "risk-card-contract.md"
ARCHITECTURE_PATH = ROOT / "docs" / "architecture.md"

REQUIRED = {
    "artifact_type", "schema_version", "risk_id", "title", "dfx", "severity",
    "confidence", "trigger", "propagation", "external_impact", "observation",
    "recovery", "translation_status", "evidence",
}
CANONICAL_PROPERTIES = REQUIRED | {
    "test_explanation", "source_scope", "inference", "instrumentation_request",
    "coverage_gap", "related_risk_ids", "status",
}
TRANSLATIONS = ["Blackbox-ready", "Graybox-ready", "Developer-confirm"]
DFX_VALUES = ["功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性"]


def canonical_card() -> dict[str, object]:
    return {
        "artifact_type": "risk_card",
        "schema_version": "1.0",
        "risk_id": "R-RESOURCE-001",
        "title": "规格回落后业务能力未恢复",
        "dfx": ["资源与规格", "性能与压力"],
        "severity": "High",
        "confidence": "medium",
        "trigger": "并发请求超过规格上限后逐步降回规格内",
        "propagation": "可申请资源持续减少，后续请求无法重新获得业务能力",
        "external_impact": "新业务建立失败或 IOPS 长时间不能恢复",
        "observation": "通过 CLI、日志、指标和诊断计数确认业务能力与资源余量",
        "recovery": "压力解除后应自行恢复；否则记录断连或重拉进程的代价",
        "translation_status": "Graybox-ready",
        "test_explanation": "在压力回落后验证业务能力是否自行恢复。",
        "source_scope": {"repository": "driver", "ref": "abc123"},
        "inference": "计数路径可能未纳入回收；通过压力回落后的外部业务能力证伪。",
        "instrumentation_request": {
            "requested_point": "接收就绪状态切换的可控时窗",
            "control_semantics": "允许将就绪动作延后指定时长后恢复正常行为",
            "parameters": "延后 0 至 2 秒，可重复开关",
            "observation": "记录开关生效时间、报文到达时间和连接状态",
            "recovery_requirement": "关闭控制后不得残留连接、资源或业务状态",
        },
        "evidence": [{"location": "driver/resource.c:42", "observation": "回收路径与超规格路径不一致"}],
        "coverage_gap": None,
        "related_risk_ids": [],
        "status": "open",
    }


class RiskCardContractV2Tests(unittest.TestCase):
    def test_schema_is_the_complete_flat_canonical_contract(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(REQUIRED, set(schema["required"]))
        self.assertEqual(CANONICAL_PROPERTIES, set(schema["properties"]))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("risk_card", schema["properties"]["artifact_type"]["const"])
        self.assertEqual("1.0", schema["properties"]["schema_version"]["const"])
        self.assertEqual("^[A-Za-z][A-Za-z0-9._:-]{0,127}$", schema["properties"]["risk_id"]["pattern"])
        self.assertEqual(TRANSLATIONS, schema["properties"]["translation_status"]["enum"])
        self.assertEqual(DFX_VALUES, schema["properties"]["dfx"]["items"]["enum"])
        self.assertTrue(schema["properties"]["dfx"]["uniqueItems"])
        self.assertEqual(["Low", "Medium", "High", "Critical"], schema["properties"]["severity"]["enum"])
        self.assertEqual(["low", "medium", "high"], schema["properties"]["confidence"]["enum"])

    def test_canonical_card_validates_in_all_supported_validator_modes(self) -> None:
        for mode in ("stdlib", "jsonschema"):
            with self.subTest(mode=mode), patch.dict(os.environ, {"PANGEA_VALIDATOR": mode}):
                try:
                    runctl.validate(canonical_card(), "risk-card.schema.json")
                except runctl.RunCtlError as exc:
                    if mode == "jsonschema" and "未安装" in str(exc):
                        self.skipTest("jsonschema is not installed")
                    raise

    def test_legacy_keys_and_nested_contract_are_rejected(self) -> None:
        for key, value in (
            ("id", "R-LEGACY"),
            ("translation", "Graybox-ready"),
            ("impact", "业务中断"),
            ("instrumentation_need", "延后 2 秒"),
            ("test_translation", {"readiness": "Graybox-ready"}),
        ):
            card = canonical_card()
            card[key] = value
            with self.subTest(key=key), patch.dict(os.environ, {"PANGEA_VALIDATOR": "stdlib"}):
                with self.assertRaises(runctl.RunCtlError):
                    runctl.validate(card, "risk-card.schema.json")

    def test_canonical_identifiers_are_required_and_immutable(self) -> None:
        for field in ("artifact_type", "schema_version"):
            card = canonical_card()
            del card[field]
            with self.subTest(field=field, condition="missing"), patch.dict(os.environ, {"PANGEA_VALIDATOR": "stdlib"}):
                with self.assertRaises(runctl.RunCtlError):
                    runctl.validate(card, "risk-card.schema.json")

    def test_risk_id_uses_safe_ascii_format_in_all_validator_modes(self) -> None:
        for mode in ("stdlib", "jsonschema"):
            for invalid in ("counter[index]++", "state = next", "__atomic_store(&state, next)", "风险-1", "1-RISK"):
                card = canonical_card()
                card["risk_id"] = invalid
                with self.subTest(mode=mode, invalid=invalid), patch.dict(os.environ, {"PANGEA_VALIDATOR": mode}):
                    try:
                        with self.assertRaises(runctl.RunCtlError):
                            runctl.validate(card, "risk-card.schema.json")
                    except runctl.RunCtlError as exc:
                        if mode == "jsonschema" and "未安装" in str(exc):
                            self.skipTest("jsonschema is not installed")
                        raise

            card = canonical_card()
            card["risk_id"] = "R.safe:1_2"
            with self.subTest(mode=mode, valid=True), patch.dict(os.environ, {"PANGEA_VALIDATOR": mode}):
                runctl.validate(card, "risk-card.schema.json")

        for field, invalid in (("artifact_type", "risk"), ("schema_version", "2.0")):
            card = canonical_card()
            card[field] = invalid
            with self.subTest(field=field, condition="invalid"), patch.dict(os.environ, {"PANGEA_VALIDATOR": "stdlib"}):
                with self.assertRaises(runctl.RunCtlError):
                    runctl.validate(card, "risk-card.schema.json")

    def test_unknown_dfx_is_rejected_by_all_supported_validator_modes(self) -> None:
        for mode in ("stdlib", "jsonschema"):
            card = canonical_card()
            card["dfx"] = ["资源与规格", "resource-spec"]
            with self.subTest(mode=mode), patch.dict(os.environ, {"PANGEA_VALIDATOR": mode}):
                with self.assertRaises(runctl.RunCtlError):
                    runctl.validate(card, "risk-card.schema.json")

    def test_evidence_requires_nonblank_location_and_observation_in_all_validator_modes(self) -> None:
        for mode in ("stdlib", "jsonschema"):
            for field, invalid in (("location", ""), ("location", "   "), ("observation", ""), ("observation", "\t")):
                card = canonical_card()
                card["evidence"] = [{"location": "driver/resource.c:42", "observation": "已观察到回收路径"}]
                card["evidence"][0][field] = invalid
                with self.subTest(mode=mode, field=field, invalid=repr(invalid)), patch.dict(os.environ, {"PANGEA_VALIDATOR": mode}):
                    try:
                        with self.assertRaises(runctl.RunCtlError):
                            runctl.validate(card, "risk-card.schema.json")
                    except runctl.RunCtlError as exc:
                        if mode == "jsonschema" and "未安装" in str(exc):
                            self.skipTest("jsonschema is not installed")
                        raise

    def test_human_contracts_reference_exact_canonical_names_and_safety_rules(self) -> None:
        texts = [path.read_text(encoding="utf-8") for path in (SKILL_PATH, CAPABILITY_PATH, ARCHITECTURE_PATH)]
        combined = "\n".join(texts)
        for field in REQUIRED | {"instrumentation_request", "test_explanation", "coverage_gap"}:
            self.assertRegex(combined, rf"(?m)^\s*{field}:")
        for value in TRANSLATIONS:
            self.assertIn(value, combined)
        self.assertIn("黑盒优先", combined)
        self.assertIn("少量灰盒", combined)
        self.assertIn("不生成插桩代码", combined)
        self.assertIn("控制语义", combined)
        self.assertIn("不得使用 `id`", combined)
        self.assertIn("不得使用 `causal_chain`", combined)
        for value in DFX_VALUES:
            self.assertIn(value, SKILL_PATH.read_text(encoding="utf-8"))
            self.assertIn(value, CAPABILITY_PATH.read_text(encoding="utf-8"))
        architecture = ARCHITECTURE_PATH.read_text(encoding="utf-8")
        self.assertIn("dfx: [资源与规格]", architecture)
        self.assertNotIn("dfx: [resource-spec]", architecture)

    def test_skill_states_the_enforced_evidence_shape(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("schema 强制", text)
        self.assertIn("`location` 和 `observation`", text)
        self.assertIn("必填非空字符串", text)


if __name__ == "__main__":
    unittest.main()
