#!/usr/bin/env python3
"""Zero-dependency environment diagnosis for PANGEA-TEST."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import shutil
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

    primary_agents = [path for path in agents.glob("*.md") if frontmatter_value(path, "mode") == "primary"]
    primary = agents / "pangea-test.md"
    primary_mode = frontmatter_value(primary, "mode")
    identity_ok = primary.exists() and primary_mode == "primary" and primary_agents == [primary]
    checks.append(check(
        "primary_agent_identity",
        "PASS" if identity_ok else "FAIL",
        f"primary={[path.stem for path in primary_agents]}; pangea-test:exists={primary.exists()},mode={primary_mode}",
        "direct",
    ))

    try:
        title = (ROOT / "core" / "shared" / "溯源铁律.md").read_text(encoding="utf-8").splitlines()[0]
        checks.append(check("core_chinese_path", "PASS", title, "direct"))
    except (OSError, IndexError) as exc:
        checks.append(check("core_chinese_path", "FAIL", str(exc), "direct"))

    retired = ("dev-expert", "troubleshooter", "test-designer")
    legacy_ok = not any((agents / f"{name}.md").exists() for name in retired)
    checks.append(check("retired_family_agents", "PASS" if legacy_ok else "FAIL", ", ".join(retired), "direct"))

    # Capability packs are documents, not OpenCode agents.  Keep this check
    # exact: an accidentally restored persona is an active topology change.
    expected_roles = {"pangea-test", "analysis-worker", "auditor", "mr-reader"}
    actual_roles = {path.stem for path in agents.glob("*.md")}
    roles_ok = actual_roles == expected_roles
    checks.append(check("runtime_role_contract", "PASS" if roles_ok else "FAIL",
                        f"expected={sorted(expected_roles)}; actual={sorted(actual_roles)}", "direct"))

    internal_ok = True
    internal_details: list[str] = []
    for name in ("analysis-worker", "mr-reader", "auditor"):
        mode = frontmatter_value(agents / f"{name}.md", "mode")
        hidden = frontmatter_value(agents / f"{name}.md", "hidden")
        valid = mode == "subagent" and hidden == "true"
        internal_ok = internal_ok and valid
        internal_details.append(f"{name}:mode={mode},hidden={hidden}")
    checks.append(check("internal_agent_visibility", "PASS" if internal_ok else "FAIL", "; ".join(internal_details), "direct"))

    # Verify the parser's resolved view as well as the files on disk.  This
    # catches malformed front matter or an OpenCode merge changing permissions.
    opencode = shutil.which("opencode")
    if not opencode:
        checks.append(check("opencode_resolved_permissions", "WARN", "opencode 未安装", "optional"))
    else:
        process = subprocess.run([opencode, "debug", "config", "--pure"], cwd=ROOT,
                                 text=True, capture_output=True, check=False)
        try:
            resolved = json.loads(process.stdout)
            configured = resolved.get("agent", {})
            worker = configured.get("analysis-worker", {})
            auditor = configured.get("auditor", {})
            primary_config = configured.get("pangea-test", {})
            denied = {"edit", "bash", "task", "webfetch", "skill", "todowrite", "external_directory"}
            worker_ok = (set(configured) == expected_roles and worker.get("mode") == "subagent"
                         and worker.get("hidden") is True
                         and all(worker.get("permission", {}).get(item) == "deny" for item in denied))
            auditor_ok = (auditor.get("mode") == "subagent" and auditor.get("hidden") is True
                          and all(auditor.get("permission", {}).get(item) == "deny" for item in denied))
            task = primary_config.get("permission", {}).get("task", {})
            primary_ok = (primary_config.get("mode") == "primary" and task.get("*") == "deny"
                          and {name for name, value in task.items() if name != "*" and value == "allow"}
                              == {"analysis-worker", "mr-reader", "auditor"})
            resolved_ok = process.returncode == 0 and worker_ok and auditor_ok and primary_ok
            detail = f"roles={sorted(configured)}; worker={worker_ok}; auditor={auditor_ok}; primary={primary_ok}"
        except (json.JSONDecodeError, AttributeError):
            resolved_ok, detail = False, process.stderr.strip() or "opencode debug config 输出无效"
        checks.append(check("opencode_resolved_permissions", "PASS" if resolved_ok else "FAIL", detail, "managed"))

    try:
        workflows = json.loads((ROOT / "registry" / "workflows.json").read_text(encoding="utf-8"))
        scenarios = json.loads((ROOT / "registry" / "scenarios.json").read_text(encoding="utf-8"))
        expected = {"mr-regression", "module-analysis"}
        v2_only = (set(workflows.get("workflows", {})) == expected
                   and set(scenarios.get("scenarios", {})) == expected
                   and "legacy_aliases" not in workflows and "legacy_aliases" not in scenarios)
        checks.append(check("v2_workflow_entrypoints", "PASS" if v2_only else "FAIL", ", ".join(sorted(expected)), "direct"))
    except (OSError, json.JSONDecodeError) as exc:
        checks.append(check("v2_workflow_entrypoints", "FAIL", str(exc), "direct"))

    # The deterministic runtime deliberately stays compatible with Python 3.9.
    checks.append(check("python", "PASS" if sys.version_info >= (3, 9) else "FAIL", sys.version.split()[0], "managed"))
    for label, command in (
        ("runctl", [sys.executable, str(ROOT / "runtime" / "runctl.py"), "--help"]),
        ("pangea_cli", [sys.executable, "-m", "tooling.pangea_cli", "data", "--help"]),
    ):
        process = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        checks.append(check(label, "PASS" if process.returncode == 0 else "FAIL", process.stderr.strip() or "CLI available", "managed"))

    runtime_file = ROOT / "runtime" / "data_runtime.py"
    checks.append(check("data_runtime", "PASS" if runtime_file.exists() else "FAIL", str(runtime_file), "managed"))
    data_root = ROOT / "pangea-data"
    checks.append(check("data_workspace", "PASS" if data_root.is_dir() else "WARN", str(data_root), "optional"))

    for name, command in (("gitnexus", "gitnexus"), ("pandoc", "pandoc"), ("libreoffice", "libreoffice"),
                          ("clang_tidy", "clang-tidy"), ("cppcheck", "cppcheck"), ("semgrep", "semgrep")):
        executable = shutil.which(command)
        checks.append(check(name, "PASS" if executable else "WARN", executable or "未安装，按降级路径继续", "optional"))

    strict = importlib.util.find_spec("jsonschema") is not None
    checks.append(check("strict_jsonschema", "PASS" if strict else "WARN", "Draft 2020-12 strict validation available" if strict else "未安装；使用标准库校验", "optional"))
    checks.append(check("opencode_runtime_discovery", "MANUAL", "启动 opencode 后确认仅可见 pangea-test", "direct"))

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
