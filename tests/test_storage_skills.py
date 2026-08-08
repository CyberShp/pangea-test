from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import unittest
from pathlib import Path

from runtime import fragment_runtime


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".opencode" / "skills"
VALIDATOR = Path("/Users/shepard/.codex/skills/.system/skill-creator/scripts/quick_validate.py")
NAMES = {
    "storage-spdk": ("reactor", "poller", "JSON-RPC", "bdev", "io_channel", "NVMf", "qpair", "DMA", "mempool", "init/fini/reset"),
    "storage-nvme-cli": ("ENTRY", "cmd_handler", "plugin", "alias", "prefix", "libnvme", "status/exit/output"),
    "storage-nvmeof": ("initiator", "target", "controller", "subsystem", "namespace", "RDMA", "TCP", "keepalive"),
    "storage-iscsi": ("login", "session", "connection", "PDU", "CmdSN", "StatSN", "ITT", "TTT", "CHAP", "digest"),
    "storage-resource-recovery": ("allocation", "ownership", "release/unwind", "refcount", "pool", "queue", "fd/socket/timer/poller", "failover"),
    "storage-destructive-cli": ("format", "sanitize", "namespace delete", "firmware", "reset", "confirmation", "exclusive", "rescan"),
}
REFERENCE_TERMS = {
    "storage-spdk": ("JSON-RPC", "spdk_thread_send_msg", "bdev", "mempool"),
    "storage-nvme-cli": ("ENTRY", "cmd_handler", "parse_and_open", "errno"),
    "storage-nvmeof": ("initiator", "target", "RDMA", "outstanding"),
    "storage-iscsi": ("ExpCmdSN", "MaxCmdSN", "CHAP", "ITT/TTT"),
    "storage-resource-recovery": ("resource ledger", "double-free", "long-run", "conservation"),
    "storage-destructive-cli": ("pure mock", "emulator", "confirmation", "admin submission"),
}


class StorageSkillTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")

    def test_exact_skill_set_and_minimal_layout(self) -> None:
        self.assertEqual(set(NAMES), {p.name for p in SKILLS.glob("storage-*") if p.is_dir()})
        for name in NAMES:
            files = {p.relative_to(SKILLS / name).as_posix() for p in (SKILLS / name).rglob("*") if p.is_file()}
            self.assertEqual({"SKILL.md", "references/analysis-checklist.md"}, files)

    def test_references_are_directly_routed_and_executable(self) -> None:
        for name, terms in REFERENCE_TERMS.items():
            with self.subTest(name=name):
                skill = self.read(name)
                reference = (SKILLS / name / "references" / "analysis-checklist.md").read_text(encoding="utf-8")
                self.assertIn("[references/analysis-checklist.md](references/analysis-checklist.md)", skill)
                self.assertGreaterEqual(len(reference.splitlines()), 80)
                self.assertLessEqual(len(reference.splitlines()), 180)
                for heading in ("## Boundary and trigger", "## Evidence order", "## Required mechanism translation", "## Per-obligation minimum", "## False-positive guards", "## 4096-token completion order"):
                    self.assertIn(heading, reference)
                for header in ("Source mechanism", "Must check invariant", "Constructible control", "External oracle", "Common misread"):
                    self.assertIn(header, reference)
                for required in ("N/A", "counterevidence", "need_verify", "exact", "control", "oracle"):
                    self.assertIn(required, reference)
                for term in terms:
                    self.assertIn(term, reference)

    def test_frontmatter_and_common_contract(self) -> None:
        forbidden = ("you are a persona", "spawn a subagent", "create a subagent", "write source", "modify source", "edit source", "task dispatch")
        for name in NAMES:
            with self.subTest(name=name):
                text = self.read(name)
                frontmatter = re.match(r"\A---\nname: ([\w-]+)\ndescription: (.+)\n---\n", text)
                self.assertIsNotNone(frontmatter)
                self.assertEqual(name, frontmatter.group(1))
                self.assertGreater(len(frontmatter.group(2)), 30)
                for required in ("inventory IDs", "obligation IDs", "source ranges", "N/A", "counterevidence", "need_verify", "4096", "analysis_fragment", "content hash", "receipt"):
                    self.assertIn(required, text)
                lower = text.lower()
                for word in forbidden:
                    self.assertNotIn(word, lower)

    def test_domain_terms_and_safety_boundaries(self) -> None:
        for name, terms in NAMES.items():
            with self.subTest(name=name):
                text = self.read(name)
                for term in terms:
                    self.assertIn(term, text)
        destructive = self.read("storage-destructive-cli").lower()
        self.assertIn("never execute", destructive)
        self.assertIn("real device", destructive)
        self.assertIn("mocks", destructive)

    def test_skill_hashes_are_available_for_trusted_receipts(self) -> None:
        trusted = {name: hashlib.sha256(self.read(name).encode()).hexdigest() for name in NAMES}
        self.assertEqual(6, len(trusted))
        self.assertTrue(all(re.fullmatch(r"[a-f0-9]{64}", value) for value in trusted.values()))
        for name in NAMES:
            original = self.read(name).encode()
            changed = original[:-1] + (b"X" if original[-1:] != b"X" else b"Y")
            self.assertNotEqual(trusted[name], hashlib.sha256(changed).hexdigest())

    def test_receipt_changes_when_skill_content_changes_by_one_byte(self) -> None:
        name = "storage-spdk"
        original = self.read(name)
        trusted = {name: {"version": "test", "content": original}}
        changed = original[:-1] + ("X" if original[-1] != "X" else "Y")
        changed_trusted = {name: {"version": "test", "content": changed}}
        args = (name, ["INV-0123456789abcdef"], ["OBL-fedcba9876543210"])
        first = fragment_runtime.skill_receipt(*args, trusted, "bounded test scope")
        second = fragment_runtime.skill_receipt(*args, changed_trusted, "bounded test scope")
        self.assertNotEqual(first["content_sha256"], second["content_sha256"])
        self.assertNotEqual(first["receipt_id"], second["receipt_id"])

    def test_references_cannot_contain_real_device_recipes(self) -> None:
        banned = ("nvme format /dev/", "nvme sanitize /dev/", "nvme delete-ns /dev/", "nvme fw-activate /dev/")
        for name in NAMES:
            with self.subTest(name=name):
                reference = (SKILLS / name / "references" / "analysis-checklist.md").read_text(encoding="utf-8").lower()
                self.assertTrue(all(recipe not in reference for recipe in banned))

    def test_quick_validate_each_skill(self) -> None:
        for name in NAMES:
            with self.subTest(name=name):
                result = subprocess.run([sys.executable, str(VALIDATOR), str(SKILLS / name)], text=True, capture_output=True, check=False)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
