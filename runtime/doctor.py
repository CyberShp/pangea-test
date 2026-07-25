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

    primary = agents / "pangea-test.md"
    primary_mode = frontmatter_value(primary, "mode")
    identity_ok = primary.exists() and primary_mode == "primary" and not (agents / "dispatcher.md").exists()
    checks.append(check(
        "primary_agent_identity",
        "PASS" if identity_ok else "FAIL",
        f"pangea-test:exists={primary.exists()},mode={primary_mode}; legacy_dispatcher_exists={(agents / 'dispatcher.md').exists()}",
        "direct",
    ))

    for name in ("source", "inputs", "workspace", "outputs", "projects", "assets"):
        path = ROOT / name
        checks.append(check(f"space_{name}", "PASS" if path.is_dir() else "FAIL", str(path), "managed"))

    try:
        title = (ROOT / "core" / "shared" / "溯源铁律.md").read_text(encoding="utf-8").splitlines()[0]
        checks.append(check("core_chinese_path", "PASS", title, "direct"))
    except (OSError, IndexError) as exc:
        checks.append(check("core_chinese_path", "FAIL", str(exc), "direct"))

    family_modes = [f"{name}={frontmatter_value(agents / f'{name}.md', 'mode')}" for name in ("dev-expert", "troubleshooter", "test-designer")]
    checks.append(check("family_agent_tab_modes", "PASS" if all(item.endswith("=all") for item in family_modes) else "FAIL", ", ".join(family_modes), "direct"))

    internal_ok = True
    internal_details: list[str] = []
    for name in ("code-excavator", "mr-reader", "auditor", "log-miner", "pcap-analyzer"):
        mode = frontmatter_value(agents / f"{name}.md", "mode")
        hidden = frontmatter_value(agents / f"{name}.md", "hidden")
        valid = mode == "subagent" and hidden == "true"
        internal_ok = internal_ok and valid
        internal_details.append(f"{name}:mode={mode},hidden={hidden}")
    checks.append(check("internal_agent_visibility", "PASS" if internal_ok else "FAIL", "; ".join(internal_details), "direct"))

    checks.append(check("python", "PASS" if sys.version_info >= (3, 10) else "FAIL", sys.version.split()[0], "managed"))
    for label, command in (
        ("runctl", [sys.executable, str(ROOT / "runtime" / "runctl.py"), "--help"]),
        ("pangea_cli", [sys.executable, "-m", "tooling.pangea_cli", "project", "--help"]),
    ):
        process = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        checks.append(check(label, "PASS" if process.returncode == 0 else "FAIL", process.stderr.strip() or "CLI available", "managed"))

    for name in ("workspace", "outputs"):
        path = ROOT / name
        try:
            path.mkdir(exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path, delete=True):
                pass
            checks.append(check(f"{name}_writable", "PASS", str(path), "managed"))
        except OSError as exc:
            checks.append(check(f"{name}_writable", "FAIL", str(exc), "managed"))

    index = ROOT / "projects" / "index.json"
    if index.exists():
        try:
            current = json.loads(index.read_text(encoding="utf-8")).get("current_project")
            checks.append(check("current_project", "PASS" if current else "WARN", str(current), "managed"))
        except json.JSONDecodeError as exc:
            checks.append(check("current_project", "FAIL", str(exc), "managed"))
    else:
        checks.append(check("current_project", "WARN", "尚未创建项目；直接专家模式仍可用", "optional"))

    strict = importlib.util.find_spec("jsonschema") is not None
    checks.append(check("strict_jsonschema", "PASS" if strict else "WARN", "Draft 2020-12 strict validation available" if strict else "未安装；使用标准库校验", "optional"))
    checks.append(check("opencode_runtime_discovery", "MANUAL", "启动 opencode 后确认 Tab 可见 pangea-test 与三个族 Agent", "direct"))

    direct_failures = [item for item in checks if item["scope"] == "direct" and item["status"] == "FAIL"]
    managed_failures = [item for item in checks if item["scope"] == "managed" and item["status"] == "FAIL"]
    payload: dict[str, Any] = {
        "artifact_type": "doctor_report",
        "schema_version": "1.2",
        "direct_expert_mode": "AVAILABLE" if not direct_failures else "UNAVAILABLE",
        "managed_task_mode": "AVAILABLE" if not direct_failures and not managed_failures else "UNAVAILABLE",
        "checks": checks,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not direct_failures and not managed_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
