from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "runtime/capabilities.py",
    "import importlib.util\nimport re\nimport shutil\nimport subprocess\n",
    "import importlib.util\nimport platform\nimport re\nimport shutil\nimport subprocess\nimport sys\n",
)
replace(
    "runtime/capabilities.py",
    '''    return result


def _has_option(text: str, option: str) -> bool:''',
    '''    return result


def _python_runtime_probe() -> dict[str, Any]:
    ready = sys.version_info >= (3, 9)
    return {
        "name": "python_runtime",
        "tier": "required",
        "available": ready,
        "version": platform.python_version(),
        "executable": sys.executable,
        "capabilities": ["runtime"] if ready else [],
        "impact": "当前 Python 运行时可用。" if ready else "当前 Python 版本低于 3.9；PANGEA runtime 不可执行。",
        "source": "current_interpreter",
    }


def _has_option(text: str, option: str) -> bool:''',
)
replace(
    "runtime/capabilities.py",
    '''        _command_probe("python3", "python3", "required", "PANGEA runtime 不可执行。", ["runtime"], runner=runner, which=which),''',
    '''        _python_runtime_probe(),''',
)
replace(
    "runtime/capabilities.py",
    '''    tools.append({
        "name": "mr_mcp",
        "tier": "required",
        "available": None,
        "version": None,
        "capabilities": ["mr_metadata", "diff", "branch_and_commit"],
        "impact": "MR MCP 属于 Agent 运行载体能力，无法通过本地 CLI 探测；执行 /mr-regression 时确认。",
        "source": "agent_runtime_manual_check",
    })''',
    '''    tools.append({
        "name": "mr_data_provider",
        "tier": "required",
        "available": None,
        "version": None,
        "capabilities": ["mr_metadata", "diff", "branch_and_commit"],
        "impact": "MR 数据能力属于 Agent 运行载体能力；执行 /mr-regression 时自主发现满足契约的 MCP、连接器或工具，不要求固定名称。",
        "source": "agent_runtime_capability_discovery",
    })''',
)
replace(
    "runtime/capabilities.py",
    '''        "required_ready": all(item["available"] is True for item in tools if item["tier"] == "required" and item["source"] != "agent_runtime_manual_check"),
        "manual_checks": ["mr_mcp"],''',
    '''        "required_ready": all(item["available"] is True for item in tools if item["tier"] == "required" and item["available"] is not None),
        "manual_checks": ["mr_data_provider_capability"],''',
)

replace(
    "tests/test_capabilities.py",
    '''        self.assertIsNone(tools["mr_mcp"]["available"])
        self.assertEqual("agent_runtime_manual_check", tools["mr_mcp"]["source"])''',
    '''        self.assertTrue(tools["python_runtime"]["available"])
        self.assertIsNone(tools["mr_data_provider"]["available"])
        self.assertEqual("agent_runtime_capability_discovery", tools["mr_data_provider"]["source"])''',
)

Path("tests/test_runtime_capability_discovery.py").write_text(
    '''from __future__ import annotations

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
''',
    encoding="utf-8",
)

replace(
    ".opencode/commands/initial.md",
    '''依次运行以下真实入口，并以各命令的实际 JSON 输出为准，不得把预期结果描述成已经执行：

```sh
python3 -m tooling.pangea_cli data session-prepare $ARGUMENTS
python3 -m tooling.pangea_cli library refresh-hints
python3 -m tooling.pangea_cli tool probe
python3 -m tooling.pangea_cli index all
```''',
    '''先在当前环境中自主选择实际可执行的 Python 3.9+ 解释器：Windows 通常为 `python`，POSIX 通常为 `python3`。记其命令为 `<python>`，本次会话后续入口必须使用同一个解释器，禁止因硬编码命令名而误报运行时不可用。

依次运行以下真实入口，并以各命令的实际 JSON 输出为准，不得把预期结果描述成已经执行：

```text
<python> -m tooling.pangea_cli data session-prepare $ARGUMENTS
<python> -m tooling.pangea_cli library refresh-hints
<python> -m tooling.pangea_cli tool probe
<python> -m tooling.pangea_cli index all
```''',
)
replace(
    ".opencode/commands/initial.md",
    '''```sh
python3 -m tooling.pangea_cli library classify --source-path "<catalog.source_path>" --json '<classification-json>'
```''',
    '''```text
<python> -m tooling.pangea_cli library classify --source-path "<catalog.source_path>" --json '<classification-json>'
```''',
)
replace(
    ".opencode/commands/initial.md",
    '''3. `tool probe` 安全探测 Git、GitNexus、MR MCP、文档转换和可选静态工具的可用性及版本；只检测，不安装。MR MCP 是运行载体能力，按输出标记为执行时确认。''',
    '''3. `tool probe` 安全探测 Git、GitNexus、当前 Python 运行时、文档转换和可选静态工具的可用性及版本；只检测，不安装。MR 数据提供能力由 Agent 在运行载体中自主发现满足契约的 MCP、连接器或工具，不绑定固定名称。''',
)

replace(
    ".opencode/commands/setup-tools.md",
    '''运行 `python3 -m tooling.pangea_cli tool probe` 获取 GitNexus、文档转换和静态工具的实际能力与版本；再运行 `python3 -m tooling.pangea_cli tool setup-plan $ARGUMENTS` 仅输出用户明确指定工具的受控来源建议。两条命令都不安装、不联网、不使用容器。''',
    '''先自主选择当前环境实际可执行的 Python 3.9+ 解释器（Windows 通常 `python`，POSIX 通常 `python3`），并在本次操作中保持一致。使用该解释器运行 `-m tooling.pangea_cli tool probe` 获取 GitNexus、文档转换和静态工具的实际能力与版本；再运行 `-m tooling.pangea_cli tool setup-plan $ARGUMENTS` 仅输出用户明确指定工具的受控来源建议。两条命令都不安装、不联网、不使用容器。''',
)
