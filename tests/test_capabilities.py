from __future__ import annotations

import subprocess
import unittest
from typing import Optional
from unittest.mock import patch

from runtime import capabilities


def completed(command: list[str], stdout: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr="")


class CapabilityTests(unittest.TestCase):
    def test_gitnexus_incremental_capability_is_derived_from_help_not_version(self) -> None:
        calls: list[list[str]] = []

        def which(name: str) -> Optional[str]:
            return f"/tools/{name}" if name in {"git", "python3", "gitnexus"} else None

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            joined = " ".join(command)
            if command[-1] == "--version":
                return completed(command, "GitNexus 1.6.4")
            if joined.endswith("gitnexus --help"):
                return completed(command, "Commands: analyze list detect-changes")
            if joined.endswith("detect-changes --help"):
                return completed(command, "--scope <scope> --base-ref <ref> --repo <name>")
            if joined.endswith("analyze --help"):
                return completed(command, "--skip-agents-md --no-stats --skip-git --force")
            if joined.endswith("gitnexus list"):
                return completed(command, "spdk\nbmcweb")
            return completed(command, "git version 2.45.0")

        report = capabilities.probe_capabilities(runner=runner, which=which)
        nexus = next(item for item in report["tools"] if item["name"] == "gitnexus")
        self.assertEqual("1.6.4", nexus["version"])
        self.assertTrue(nexus["incremental_detection"]["available"])
        self.assertEqual(["spdk", "bmcweb"], nexus["registered_indexes"]["entries"])
        self.assertIn("--skip-agents-md", nexus["analyze"]["safe_arguments"])
        self.assertFalse(any(" analyze /" in " ".join(call) for call in calls))

    def test_missing_tools_and_mr_mcp_are_honestly_reported(self) -> None:
        report = capabilities.probe_capabilities(which=lambda _: None)
        tools = {item["name"]: item for item in report["tools"]}
        self.assertFalse(tools["git"]["available"])
        self.assertFalse(tools["gitnexus"]["incremental_detection"]["available"])
        self.assertIsNone(tools["mr_mcp"]["available"])
        self.assertEqual("agent_runtime_manual_check", tools["mr_mcp"]["source"])

    def test_document_capabilities_only_claim_the_enabled_pdf_converter(self) -> None:
        report = capabilities.probe_capabilities(
            runner=lambda command: completed(command, "tool 1.0.0"),
            which=lambda name: f"/tools/{name}" if name in {"git", "python3", "pandoc", "pdftotext", "libreoffice"} else None,
        )
        tools = {item["name"]: item for item in report["tools"]}
        self.assertEqual(["pdf_text_extraction"], tools["pdftotext"]["capabilities"])
        self.assertEqual([], tools["pandoc"]["capabilities"])
        self.assertEqual([], tools["libreoffice"]["capabilities"])

    def test_gitnexus_registered_indexes_are_bounded(self) -> None:
        entries = "\n".join(f"repository-{index}" for index in range(254))

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            joined = " ".join(command)
            if joined.endswith("gitnexus list"):
                return completed(command, entries)
            if command[-1] == "--version":
                return completed(command, "GitNexus 1.6.4")
            return completed(command, "Commands: analyze list")

        report = capabilities.probe_capabilities(
            runner=runner,
            which=lambda name: f"/tools/{name}" if name in {"git", "python3", "gitnexus"} else None,
        )
        nexus = next(item for item in report["tools"] if item["name"] == "gitnexus")
        registered = nexus["registered_indexes"]
        self.assertEqual([f"repository-{index}" for index in range(20)], registered["entries"])
        self.assertLessEqual(len(registered["entries"]), 20)
        self.assertEqual(254, registered["total_count"])
        self.assertTrue(registered["truncated"])

    def test_setup_plan_is_explicit_and_never_runs_installs_or_git_mutations(self) -> None:
        with patch("runtime.capabilities.subprocess.run") as run:
            empty = capabilities.setup_plan(None)
            plan = capabilities.setup_plan(["semgrep", "unknown-tool"])
        self.assertEqual([], empty["requested"])
        self.assertTrue(plan["requested"][0]["recognized"])
        self.assertFalse(plan["requested"][1]["recognized"])
        run.assert_not_called()
        serialized = str(plan).lower()
        self.assertNotIn("pip install", serialized)
        self.assertNotIn("git pull", serialized)

    def test_probe_uses_only_allowlisted_read_only_commands(self) -> None:
        calls: list[list[str]] = []

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return completed(command, "tool 1.0.0")

        capabilities.probe_capabilities(runner=runner, which=lambda name: f"/bin/{name}")
        forbidden = {"install", "pull", "clone", "clean", "reset", "checkout"}
        self.assertFalse(any(forbidden & set(call) for call in calls))
        self.assertTrue(all(call[-1] in {"--version", "--help", "list"} for call in calls))


if __name__ == "__main__":
    unittest.main()
