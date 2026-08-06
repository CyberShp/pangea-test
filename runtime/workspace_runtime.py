"""Portable, no-guess PANGEA workspace preflight."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from runtime.process_runtime import run_text

ROOT_MARKERS = (
    ".opencode",
    "runtime/runctl.py",
    "tooling/pangea_cli/__main__.py",
    "registry/scenarios.json",
)
SHELL_OPERATORS = {"&&", "||", ";"}


class WorkspaceResolutionError(RuntimeError):
    def __init__(self, message: str, *, checked_paths: list[str] | None = None) -> None:
        super().__init__(message)
        self.checked_paths = checked_paths or []


def _marker_missing(root: Path) -> list[str]:
    return [marker for marker in ROOT_MARKERS if not (root / marker).exists()]


def validate_project_root(value: str | Path, *, platform_name: str | None = None) -> Path:
    raw = str(value)
    effective_platform = (platform_name or os.name).lower()
    if effective_platform in {"nt", "windows"} and re.match(r"^/[A-Za-z]/", raw):
        raise WorkspaceResolutionError(
            f"Windows 下拒绝 MSYS 风格盘符路径: {raw}；请使用原生绝对路径，例如 D:\\\\path\\\\pangea-test"
        )
    candidate = Path(value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceResolutionError(f"项目根目录不存在或不可解析: {candidate}") from exc
    if not resolved.is_dir():
        raise WorkspaceResolutionError(f"项目根路径不是目录: {resolved}")
    missing = _marker_missing(resolved)
    if missing:
        raise WorkspaceResolutionError(
            "指定目录不是 PANGEA-TEST 项目根目录；缺少标记: " + ", ".join(missing),
            checked_paths=[str(resolved)],
        )
    return resolved


def locate_project_root(
    *,
    explicit: str | Path | None = None,
    start: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    package_root: str | Path | None = None,
    platform_name: str | None = None,
) -> dict[str, Any]:
    environment = os.environ if env is None else env
    checked: list[str] = []
    if explicit is not None:
        root = validate_project_root(explicit, platform_name=platform_name)
        return {"project_root": root, "root_source": "explicit", "checked_paths": [str(root)]}
    configured = environment.get("PANGEA_ROOT")
    if configured:
        root = validate_project_root(configured, platform_name=platform_name)
        return {"project_root": root, "root_source": "environment", "checked_paths": [str(root)]}

    origin = Path(start or Path.cwd()).expanduser()
    try:
        origin = origin.resolve(strict=True)
    except OSError as exc:
        raise WorkspaceResolutionError(f"当前工作目录不可解析: {origin}") from exc
    candidates = [origin, *origin.parents]
    for candidate in candidates:
        checked.append(str(candidate))
        if not _marker_missing(candidate):
            return {"project_root": candidate, "root_source": "cwd_or_parent", "checked_paths": checked}

    if package_root is not None:
        package = Path(package_root).expanduser().resolve()
        if str(package) not in checked:
            checked.append(str(package))
            if not _marker_missing(package):
                return {"project_root": package, "root_source": "package_root", "checked_paths": checked}
    raise WorkspaceResolutionError(
        "当前目录及其父目录中未找到 PANGEA-TEST 根标记；禁止扫描盘符或猜测相似目录",
        checked_paths=checked,
    )


def shell_family(env: Mapping[str, str] | None = None, *, platform_name: str | None = None) -> str:
    environment = os.environ if env is None else env
    effective_platform = (platform_name or os.name).lower()
    if effective_platform in {"nt", "windows"}:
        if environment.get("PSModulePath") or environment.get("POWERSHELL_DISTRIBUTION_CHANNEL"):
            return "powershell"
        return "windows"
    shell = Path(environment.get("SHELL", "")).name.lower()
    return shell or "posix"


def build_preflight_steps(project_root: Path, python_executable: str) -> list[tuple[str, list[str]]]:
    return [
        ("session_prepare", [python_executable, "-m", "tooling.pangea_cli", "data", "--root", str(project_root), "session-prepare"]),
        ("library_refresh_hints", [python_executable, "-m", "tooling.pangea_cli", "library", "refresh-hints"]),
        ("tool_probe", [python_executable, "-m", "tooling.pangea_cli", "tool", "probe"]),
        ("index_all", [python_executable, "-m", "tooling.pangea_cli", "index", "all"]),
    ]


def _parse_step_output(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw_output": text}


def _known_repositories(repository_root: Path) -> list[str]:
    if not repository_root.is_dir():
        return []
    return sorted(path.name for path in repository_root.iterdir() if path.is_dir() and not path.is_symlink())


def run_preflight(
    *,
    explicit_root: str | Path | None = None,
    start: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    python_executable: str | None = None,
    runner: Callable[..., Any] = run_text,
    platform_name: str | None = None,
) -> dict[str, Any]:
    process_cwd = Path(start or Path.cwd()).expanduser()
    executable = str(Path(python_executable or sys.executable).resolve())
    base: dict[str, Any] = {
        "status": "workspace_unresolved",
        "platform": platform.system().lower(),
        "shell_family": shell_family(env, platform_name=platform_name),
        "python_executable": executable,
        "python_version": platform.python_version(),
        "process_cwd": str(process_cwd),
        "project_root": None,
        "root_source": None,
        "checked_paths": [],
        "data_root": None,
        "repository_root": None,
        "known_repositories": [],
        "step_results": {},
        "step_errors": {},
        "allowed_next_actions": ["ask_user_for_project_root"],
    }
    try:
        located = locate_project_root(
            explicit=explicit_root,
            start=process_cwd,
            env=env,
            package_root=Path(__file__).resolve().parents[1],
            platform_name=platform_name,
        )
    except WorkspaceResolutionError as exc:
        base["checked_paths"] = exc.checked_paths
        base["step_errors"]["workspace"] = {"type": type(exc).__name__, "message": str(exc)}
        return base

    project_root = Path(located["project_root"])
    data_root = project_root / "pangea-data"
    repository_root = data_root / "repositories"
    base.update({
        "status": "ready",
        "project_root": str(project_root),
        "root_source": located["root_source"],
        "checked_paths": located["checked_paths"],
        "data_root": str(data_root),
        "repository_root": str(repository_root),
        "known_repositories": _known_repositories(repository_root),
        "allowed_next_actions": ["draft_contract"],
    })

    for name, command in build_preflight_steps(project_root, executable):
        if any(part in SHELL_OPERATORS for part in command):
            base["step_errors"][name] = {"type": "UnsafeCommand", "message": "preflight 子命令不得包含 shell 连接符"}
            continue
        try:
            result = runner(command, cwd=str(project_root))
        except BaseException as exc:
            base["step_errors"][name] = {"type": type(exc).__name__, "message": str(exc) or "子命令执行失败"}
            continue
        if result.returncode:
            base["step_errors"][name] = {
                "type": "CommandFailed",
                "returncode": result.returncode,
                "message": (result.stderr or result.stdout or "子命令执行失败").strip(),
                "command": command,
            }
        else:
            base["step_results"][name] = _parse_step_output(result.stdout or "")

    session = base["step_results"].get("session_prepare")
    if isinstance(session, dict):
        base["data_root"] = session.get("data_root", base["data_root"])
        known = session.get("known_repositories")
        if isinstance(known, list):
            base["known_repositories"] = known
    else:
        base["known_repositories"] = _known_repositories(repository_root)
    if base["step_errors"]:
        base["status"] = "degraded"
        base["allowed_next_actions"] = ["inspect_step_errors"]
        if "session_prepare" not in base["step_errors"]:
            base["allowed_next_actions"].append("draft_contract")
    return base
