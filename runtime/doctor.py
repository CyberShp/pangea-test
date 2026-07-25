#!/usr/bin/env python3
"""Zero-dependency environment diagnosis for PANGEA-TEST."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def check(name: str, status: str, detail: str, scope: str) -> dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "scope": scope}


def frontmatter_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith(f"{key}:"):
            return line.split(":", 1)[1].strip()
    return None


def main() -> int:
    checks: list[dict[str, str]] = []
    checks.append(check("repository_root", "PASS" if (ROOT / "core").is_dir() else "FAIL", str(ROOT), "direct"))

    agents = ROOT / ".opencode" / "agents"
    commands = ROOT / ".opencode" / "commands"
    skills = ROOT / ".opencode" / "skills"
    checks.append(check("opencode_agents", "PASS" if agents.is_dir() else "FAIL", str(agents), "direct"))
    checks.append(check("opencode_commands", "PASS" if commands.is_dir() else "FAIL", str(commands), "managed"))
    checks.append(check("opencode_skills", "PASS" if skills.is_dir() else "FAIL", str(skills), "direct"))

    core_probe = ROOT / "core" / "shared" / "溯源铁律.md"
    try:
        title = core_probe.read_text(encoding="utf-8").splitlines()[0]
        checks.append(check("core_chinese_path", "PASS", title, "direct"))
    except (OSError, IndexError) as exc:
        checks.append(check("core_chinese_path", "FAIL", str(exc), "direct"))

    family_modes: list[str] = []
    for name in ("dev-expert", "troubleshooter", "test-designer"):
        mode = frontmatter_value(agents / f"{name}.md", "mode")
        family_modes.append(f"{name}={mode}")
    modes_ok = all(item.endswith("=all") for item in family_modes)
    checks.append(check("family_agent_tab_modes", "PASS" if modes_ok else "FAIL", ", ".join(family_modes), "direct"))

    internal_ok = True
    internal_details: list[str] = []
    for name in ("code-excavator", "mr-reader", "auditor", "log-miner", "pcap-analyzer"):
        path = agents / f"{name}.md"
        mode = frontmatter_value(path, "mode")
        hidden = frontmatter_value(path, "hidden")
        valid = mode == "subagent" and hidden == "true"
        internal_ok = internal_ok and valid
        internal_details.append(f"{name}:mode={mode},hidden={hidden}")
    checks.append(check("internal_agent_visibility", "PASS" if internal_ok else "FAIL", "; ".join(internal_details), "direct"))

    python_ok = sys.version_info >= (3, 10)
    checks.append(check("python", "PASS" if python_ok else "FAIL", sys.version.split()[0], "managed"))
    runctl = ROOT / "runtime" / "runctl.py"
    process = subprocess.run([sys.executable, str(runctl), "--help"], cwd=ROOT, text=True, capture_output=True, check=False)
    checks.append(check("runctl", "PASS" if process.returncode == 0 else "FAIL", process.stderr.strip() or "CLI available", "managed"))

    runs = ROOT / "runs"
    try:
        runs.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=runs, delete=True):
            pass
        checks.append(check("runs_writable", "PASS", str(runs), "managed"))
    except OSError as exc:
        checks.append(check("runs_writable", "FAIL", str(exc), "managed"))

    strict_available = importlib.util.find_spec("jsonschema") is not None
    checks.append(check(
        "strict_jsonschema",
        "PASS" if strict_available else "WARN",
        "Draft 2020-12 strict validation available" if strict_available else "未安装；将使用内置标准库校验，不影响基本使用",
        "optional",
    ))
    checks.append(check("opencode_runtime_discovery", "MANUAL", "启动 opencode 后确认 Tab 可见 dispatcher 与三个族 Agent", "direct"))

    direct_failures = [item for item in checks if item["scope"] == "direct" and item["status"] == "FAIL"]
    managed_failures = [item for item in checks if item["scope"] == "managed" and item["status"] == "FAIL"]
    payload: dict[str, Any] = {
        "artifact_type": "doctor_report",
        "schema_version": "1.0",
        "direct_expert_mode": "AVAILABLE" if not direct_failures else "UNAVAILABLE",
        "managed_task_mode": "AVAILABLE" if not direct_failures and not managed_failures else "UNAVAILABLE",
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not direct_failures and not managed_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
