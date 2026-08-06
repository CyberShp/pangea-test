from __future__ import annotations

import platform
import subprocess
import sys
import unittest

from runtime import capabilities


class RuntimeCapabilityDiscoveryTests(unittest.TestCase):
    def test_current_interpreter_is_runtime_source_without_python3_lookup(self) -> None:
        lookups: list[str] = []
        calls: list[list[str]] = []

        def which(name: str) -> str | None:
            lookups.append(name)
            return "/tools/git" if name == "git" else None

        def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "git version 2.45.1", "")

        report = capabilities.probe_capabilities(runner=runner, which=which)
        tools = {item["name"]: item for item in report["tools"]}
        runtime = tools["python_runtime"]

        self.assertTrue(runtime["available"])
        self.assertEqual(platform.python_version(), runtime["version"])
        self.assertEqual(sys.executable, runtime["executable"])
        self.assertEqual("current_interpreter", runtime["source"])
        self.assertNotIn("python3", lookups)
        self.assertFalse(any("python3" in part for call in calls for part in call))
        self.assertTrue(report["required_ready"])

    def test_mr_provider_is_capability_contract_not_fixed_tool_name(self) -> None:
        report = capabilities.probe_capabilities(which=lambda _: None)
        tools = {item["name"]: item for item in report["tools"]}

        self.assertNotIn("mr_mcp", tools)
        provider = tools["mr_data_provider"]
        self.assertIsNone(provider["available"])
        self.assertEqual(
            ["mr_metadata", "diff", "branch_and_commit"],
            provider["capabilities"],
        )
        self.assertIn("不要求固定名称", provider["impact"])
        self.assertEqual(["mr_data_provider_capability"], report["manual_checks"])


if __name__ == "__main__":
    unittest.main()
