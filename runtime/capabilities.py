#!/usr/bin/env python3
"""Read-only local capability discovery for PANGEA-TEST.

This module deliberately limits subprocesses to version, help, and list
commands.  It neither accepts a repository path nor runs an analyser, so it
cannot mutate a source checkout or bootstrap third-party software.
"""
from __future__ import annotations

import importlib.util
import platform
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Optional, Sequence

from runtime.process_runtime import run_text


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
Which = Callable[[str], Optional[str]]
MAX_REGISTERED_INDEX_ENTRIES = 20
MAX_PROBE_ERROR_CHARS = 4096


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return run_text(list(command))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or result.stderr or "").strip()


def _bounded(value: str, limit: int = MAX_PROBE_ERROR_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n... 已截断 {len(value) - limit} 个字符"


def _version(output: str) -> Optional[str]:
    match = re.search(r"\b\d+(?:\.\d+){1,3}(?:[-+._a-zA-Z0-9]+)?\b", output)
    return match.group(0) if match else None


def _command_probe(
    name: str,
    executable: str,
    tier: str,
    impact: str,
    capabilities: list[str],
    *,
    runner: CommandRunner,
    which: Which,
) -> dict[str, Any]:
    location = which(executable)
    result: dict[str, Any] = {
        "name": name,
        "tier": tier,
        "available": bool(location),
        "version": None,
        "capabilities": capabilities if location else [],
        "impact": "可用。" if location else f"缺少 {name}；{impact}",
        "source": "local_command",
    }
    if not location:
        return result
    completed = runner([location, "--version"])
    text = _output(completed)
    result["version"] = _version(text)
    if completed.returncode:
        result["capabilities"] = []
        result["impact"] = f"{name} 可定位但版本探测失败；{impact}"
        result["probe_error"] = text or f"exit {completed.returncode}"
    return result


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


def _has_option(text: str, option: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(option) + r"(?![\w-])", text) is not None


def _gitnexus_probe(*, runner: CommandRunner, which: Which) -> dict[str, Any]:
    location = which("gitnexus")
    result: dict[str, Any] = {
        "name": "gitnexus",
        "tier": "optional",
        "available": bool(location),
        "version": None,
        "capabilities": [],
        "impact": "缺少 GitNexus；调用链和影响面退化为源码搜索与人工追踪。",
        "source": "local_command",
        "incremental_detection": {"available": False, "basis": "command unavailable"},
        "analyze": {"available": False, "safe_arguments": []},
        "registered_indexes": {"available": False, "entries": []},
    }
    if not location:
        return result

    version = runner([location, "--version"])
    result["version"] = _version(_output(version))
    root_help = runner([location, "--help"])
    help_text = _output(root_help)
    if root_help.returncode:
        result["impact"] = "GitNexus 可定位但 help 探测失败；不作为索引能力使用。"
        result["probe_error"] = _bounded(help_text) or f"exit {root_help.returncode}"
        return result

    result["capabilities"].append("repository_index_query")
    detect_present = "detect-changes" in help_text or "detect_changes" in help_text
    detect_help = ""
    if detect_present:
        change = runner([location, "detect-changes", "--help"])
        detect_help = _output(change)
        available = change.returncode == 0 and ("--scope" in detect_help or "--base-ref" in detect_help)
        result["incremental_detection"] = {
            "available": available,
            "basis": "detect-changes help exposes scope/base-ref" if available else "detect-changes help unavailable or lacks observable options",
            "capabilities": [item for item in ("scope", "base_ref", "repository") if f"--{item.replace('_', '-')}" in detect_help],
        }
        if available:
            result["capabilities"].append("changed_symbol_and_flow_mapping")
    else:
        result["incremental_detection"] = {"available": False, "basis": "root help does not expose detect-changes"}

    analyze = runner([location, "analyze", "--help"])
    analyze_help = _output(analyze)
    options = [option for option in ("--skip-agents-md", "--no-stats", "--skip-git", "--force") if _has_option(analyze_help, option)]
    result["analyze"] = {
        "available": analyze.returncode == 0,
        "safe_arguments": options,
        "writes_index": analyze.returncode == 0,
        "note": "analyze 会写入索引；本探测绝不执行 analyze。",
    }

    listed = runner([location, "list"])
    lines = [line.strip() for line in (listed.stdout or "").splitlines() if line.strip()]
    result["registered_indexes"] = {
        "available": listed.returncode == 0,
        "entries": lines[:MAX_REGISTERED_INDEX_ENTRIES],
        "total_count": len(lines),
        "truncated": len(lines) > MAX_REGISTERED_INDEX_ENTRIES,
        "error": _bounded(_output(listed)) if listed.returncode else None,
    }
    result["impact"] = "GitNexus 可用于已登记索引的代码地图和影响面分析。"
    return result


_PYTHON_PACKAGES = {
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "pptx": "python-pptx",
    "pypdf": "pypdf",
    "markdown": "markdown",
    "jsonschema": "jsonschema",
}


def _python_packages() -> list[dict[str, Any]]:
    return [
        {
            "name": display,
            "tier": "optional",
            "available": importlib.util.find_spec(module) is not None,
            "version": None,
            "capabilities": ["python_document_processing"] if importlib.util.find_spec(module) is not None else [],
            "impact": "可用。" if importlib.util.find_spec(module) is not None else f"缺少 {display}；相关文件转换或严格校验将降级。",
            "source": "python_import",
        }
        for module, display in _PYTHON_PACKAGES.items()
    ]


def probe_capabilities(*, runner: CommandRunner = _run, which: Which = shutil.which) -> dict[str, Any]:
    """Return a JSON-serializable, side-effect-free local capability report."""
    tools = [
        _command_probe("git", "git", "required", "仓库版本和安全更新能力不可用。", ["repository_metadata"], runner=runner, which=which),
        _python_runtime_probe(),
        _gitnexus_probe(runner=runner, which=which),
        _command_probe("clang-tidy", "clang-tidy", "optional", "C/C++ 静态审查增强不可用。", ["cpp_static_analysis"], runner=runner, which=which),
        _command_probe("cppcheck", "cppcheck", "optional", "C/C++ 缺陷模式扫描不可用。", ["cpp_static_analysis"], runner=runner, which=which),
        _command_probe("semgrep", "semgrep", "optional", "规则化源码扫描不可用。", ["rule_based_scanning"], runner=runner, which=which),
        _command_probe("codeql", "codeql", "optional", "CodeQL 查询增强不可用。", ["codeql_analysis"], runner=runner, which=which),
        _command_probe("pandoc", "pandoc", "optional", "当前运行时未调用 pandoc；它不是已启用的转换能力。", [], runner=runner, which=which),
        _command_probe("pdftotext", "pdftotext", "optional", "PDF 文本提取会降级。", ["pdf_text_extraction"], runner=runner, which=which),
        _command_probe("libreoffice", "libreoffice", "optional", "旧二进制 Office 文件会生成待转换材料；当前运行时未自动调用 LibreOffice。", [], runner=runner, which=which),
    ]
    tools.extend(_python_packages())
    tools.append({
        "name": "mr_data_provider",
        "tier": "required",
        "available": None,
        "version": None,
        "capabilities": ["mr_metadata", "diff", "branch_and_commit"],
        "impact": "MR 数据能力属于 Agent 运行载体能力；执行 /mr-regression 时自主发现满足契约的 MCP、连接器或工具，不要求固定名称。",
        "source": "agent_runtime_capability_discovery",
    })
    return {
        "artifact_type": "capability_report",
        "schema_version": "1.0",
        "generated_at": _now(),
        "read_only": True,
        "tools": tools,
        "required_ready": all(item["available"] is True for item in tools if item["tier"] == "required" and item["available"] is not None),
        "manual_checks": ["mr_data_provider_capability"],
    }


_SETUP_SOURCES = {
    "gitnexus": "npm 包或已批准的本地发行包；不要自动安装。",
    "clang-tidy": "LLVM / 系统软件源。",
    "cppcheck": "系统软件源。",
    "semgrep": "内网 pip wheel 或 Python 软件源。",
    "codeql": "独立 CodeQL CLI 发行包。",
    "pandoc": "系统软件源。",
    "pdftotext": "Poppler 系统软件源。",
    "libreoffice": "系统软件源或已批准的本地发行包。",
    "python-docx": "内网 pip wheel 或 Python 软件源。",
    "openpyxl": "内网 pip wheel 或 Python 软件源。",
    "python-pptx": "内网 pip wheel 或 Python 软件源。",
    "pypdf": "内网 pip wheel 或 Python 软件源。",
    "markdown": "内网 pip wheel 或 Python 软件源。",
    "jsonschema": "内网 pip wheel 或 Python 软件源。",
}


def setup_plan(requested_tools: Optional[Iterable[str]]) -> dict[str, Any]:
    """Describe explicit setup options without downloading or installing anything."""
    requested = list(requested_tools or [])
    entries = []
    for name in requested:
        canonical = name.strip().lower()
        entries.append({
            "name": name,
            "recognized": canonical in _SETUP_SOURCES,
            "possible_source": _SETUP_SOURCES.get(canonical),
            "action": "用户确认后在受控环境安装；本函数不执行任何安装、联网或容器操作。",
        })
    return {"artifact_type": "setup_plan", "schema_version": "1.0", "read_only": True, "requested": entries}
