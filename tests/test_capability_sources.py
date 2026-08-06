from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "core" / "capabilities" / "sources.json"
CONDITIONAL = ROOT / "core" / "capabilities" / "conditional-knowledge.md"
URL_RE = re.compile(r"^https://(?:github\.com|skills\.sh)/[^\s]+$")
SOURCE_ID_RE = re.compile(r"source_id:([a-z0-9-]+)")


class CapabilitySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(SOURCES.read_text(encoding="utf-8"))
        cls.sources = cls.payload["sources"]

    def test_records_have_unique_ids_and_machine_contract(self) -> None:
        ids = [source["id"] for source in self.sources]
        self.assertEqual(len(ids), len(set(ids)))
        required = {
            "id", "category", "source_kind", "name", "url", "upstream", "license",
            "extracted_methods", "trigger_evidence", "prohibited_generalizations",
            "last_verified", "verification_status",
        }
        for source in self.sources:
            self.assertTrue(required <= source.keys(), source["id"])
            self.assertRegex(source["id"], r"^[a-z0-9-]+$")
            self.assertRegex(source["url"], URL_RE)
            self.assertRegex(source["last_verified"], r"^\d{4}-\d{2}-\d{2}$")
            self.assertTrue(source["upstream"])
            self.assertTrue(source["license"])
            for field in ("extracted_methods", "trigger_evidence", "prohibited_generalizations"):
                self.assertTrue(source[field], f"{source['id']} missing {field}")

    def test_conditional_document_references_all_registered_sources(self) -> None:
        referenced = set(SOURCE_ID_RE.findall(CONDITIONAL.read_text(encoding="utf-8")))
        registered = {source["id"] for source in self.sources}
        self.assertEqual(registered, referenced)

    def test_nvidia_intel_and_general_boundaries_exist(self) -> None:
        categories = {source["category"] for source in self.sources}
        self.assertTrue({"nvidia", "intel", "general"} <= categories)
        nvidia_skills = {source["id"] for source in self.sources if source["category"] == "nvidia"}
        self.assertTrue({
            "nvidia-doca-rdma", "nvidia-doca-upgrade", "nvidia-doca-debug",
            "nvidia-doca-hardware-safety", "nvidia-doca-telemetry", "nvidia-doca-flow-perf",
        } <= nvidia_skills)
        intel_negative = next(source for source in self.sources if source["id"] == "intel-agent-skill-negative-finding")
        self.assertEqual("negative_finding", intel_negative["source_kind"])
        self.assertEqual("negative_finding", intel_negative["verification_status"])


if __name__ == "__main__":
    unittest.main()
