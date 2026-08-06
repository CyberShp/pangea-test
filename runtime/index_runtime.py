#!/usr/bin/env python3
"""Controlled, source-read-only GitNexus indexing for PANGEA-TEST.

The supplied repository is never a GitNexus target.  A managed local clone
below ``pangea-data/indexes/shadows`` is the only mutable analysis surface.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from runtime import data_runtime


class IndexRuntimeError(RuntimeError):
    pass


GIT_TIMEOUT_SECONDS = 60
NEXUS_TIMEOUT_SECONDS = 15 * 60
MARKER_NAME = ".pangea-index-shadow.json"
RECORD_SCHEMA_VERSION = "1.0"
CommandRunner = Callable[[Sequence[str], Optional[Path], int], subprocess.CompletedProcess[str]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run(command: Sequence[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(list(command), cwd=cwd, text=True, capture_output=True, check=False,
                              timeout=timeout, env=environment)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(list(command), 124, "", f"command timed out after {timeout}s")


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return ((result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")).strip()


def _safe_repository_name(value: str) -> str:
    # A repository is exactly one directory below pangea-data/repositories.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise IndexRuntimeError("repository 必须是 pangea-data/repositories 下的单层安全名称")
    if value in {".", "..", ".git"}:
        raise IndexRuntimeError("repository 名称非法")
    return value


def _under(path: Path, parent: Path, label: str) -> Path:
    resolved, root = path.resolve(), parent.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise IndexRuntimeError(f"拒绝访问 {label} 外路径: {path}") from exc
    return resolved


def _git(runner: CommandRunner, repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return runner(["git", "-C", str(repo), *args], None, GIT_TIMEOUT_SECONDS)


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    return _output(result) or f"git exit {result.returncode}"


def _source_repository(root: Path, repository: str, runner: CommandRunner) -> tuple[Path, Path, str]:
    workspace = data_runtime.ensure_layout(root)
    name = _safe_repository_name(repository)
    repositories = workspace / "repositories"
    source = _under(repositories / name, repositories, "repositories")
    if not source.is_dir():
        raise IndexRuntimeError(f"代码仓不存在: {name}")
    check = _git(runner, source, "rev-parse", "--is-inside-work-tree")
    if check.returncode or check.stdout.strip() != "true":
        raise IndexRuntimeError(f"不是 Git 工作树: {name}")
    # A directory inside the parent workspace repository also returns true
    # above.  Only an independently registered repository/worktree may be
    # cloned into the mutable shadow area.
    top_level = _git(runner, source, "rev-parse", "--show-toplevel")
    if top_level.returncode:
        raise IndexRuntimeError(f"无法确定 Git 工作树根目录: {name}")
    try:
        top_level_path = Path(top_level.stdout.strip()).resolve()
    except OSError as exc:
        raise IndexRuntimeError(f"无法解析 Git 工作树根目录: {name}") from exc
    if top_level_path != source.resolve():
        raise IndexRuntimeError(f"登记目录不是独立 Git 工作树根目录: {name}")
    revision = _git(runner, source, "rev-parse", "--verify", "HEAD^{commit}")
    if revision.returncode:
        raise IndexRuntimeError(f"无法读取源仓 commit: {_git_error(revision)}")
    return workspace, source, revision.stdout.strip()


def _marker(shadow: Path) -> Path:
    return shadow / MARKER_NAME


def _shadow_repository(workspace: Path, name: str, source: Path, source_commit: str,
                       runner: CommandRunner) -> tuple[Path, str]:
    shadows = workspace / "indexes" / "shadows"
    shadows.mkdir(parents=True, exist_ok=True)
    shadow = _under(shadows / name, shadows, "indexes/shadows")
    if shadow.exists():
        if shadow.is_symlink() or not shadow.is_dir() or not _marker(shadow).is_file():
            raise IndexRuntimeError(f"影子仓不是 PANGEA 管理的 clone: {name}")
        metadata = json.loads(_marker(shadow).read_text(encoding="utf-8"))
        if metadata.get("source_repository") != str(source):
            raise IndexRuntimeError(f"影子仓来源不匹配: {name}")
        current = _git(runner, shadow, "rev-parse", "--verify", "HEAD^{commit}")
        if current.returncode:
            raise IndexRuntimeError(f"影子仓不可用: {_git_error(current)}")
        if current.stdout.strip() == source_commit:
            return shadow, "unchanged"
        fetched = _git(runner, shadow, "fetch", "--prune", "origin")
        if fetched.returncode:
            raise IndexRuntimeError(f"更新影子仓失败: {_git_error(fetched)}")
        mode = "updated"
    else:
        cloned = runner(["git", "clone", "--no-hardlinks", "--origin", "origin", str(source), str(shadow)],
                        None, GIT_TIMEOUT_SECONDS)
        if cloned.returncode:
            raise IndexRuntimeError(f"创建影子仓失败: {_git_error(cloned)}")
        data_runtime.atomic_write_json(_marker(shadow), {
            "schema_version": RECORD_SCHEMA_VERSION, "managed_by": "pangea-test",
            "source_repository": str(source), "created_at": _now(),
        })
        mode = "cold"
    checkout = _git(runner, shadow, "checkout", "--detach", source_commit)
    reset = _git(runner, shadow, "reset", "--hard", source_commit)
    if checkout.returncode or reset.returncode:
        detail = _git_error(checkout if checkout.returncode else reset)
        raise IndexRuntimeError(f"定位影子仓 commit 失败: {detail}")
    return shadow, mode


def _gitnexus_capability(runner: CommandRunner, executable: str | None) -> dict[str, Any]:
    if not executable:
        return {"available": False, "version": None, "analyze_available": False,
                "safe_arguments": [], "incremental_detection": {"available": False, "basis": "command unavailable"}}
    version = runner([executable, "--version"], None, GIT_TIMEOUT_SECONDS)
    help_result = runner([executable, "analyze", "--help"], None, GIT_TIMEOUT_SECONDS)
    help_text = _output(help_result)
    safe = [option for option in ("--skip-agents-md", "--no-stats") if re.search(r"(?<![\w-])" + re.escape(option) + r"(?![\w-])", help_text)]
    root_help = runner([executable, "--help"], None, GIT_TIMEOUT_SECONDS)
    root_text = _output(root_help)
    # This reports observable CLI support only.  It deliberately makes no
    # promise that a particular GitNexus version performs incremental analysis.
    return {
        "available": help_result.returncode == 0,
        "version": _version(_output(version)),
        "analyze_available": help_result.returncode == 0,
        "safe_arguments": safe,
        "incremental_detection": {
            "available": "detect-changes" in root_text or "detect_changes" in root_text,
            "basis": "root help exposes detect-changes" if ("detect-changes" in root_text or "detect_changes" in root_text) else "root help does not expose detect-changes",
        },
    }


def _version(text: str) -> str | None:
    match = re.search(r"\b\d+(?:\.\d+){1,3}(?:[-+._a-zA-Z0-9]+)?\b", text)
    return match.group(0) if match else None


def _directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def _reported_stats(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for key, patterns in {
        "files": (r"\bfiles?\s*[:=]\s*([\d,]+)",),
        "symbols": (r"\bsymbols?\s*[:=]\s*([\d,]+)", r"\bnodes?\s*[:=]\s*([\d,]+)"),
    }.items():
        for pattern in patterns:
            found = re.search(pattern, text, re.IGNORECASE)
            if found:
                values[key] = int(found.group(1).replace(",", ""))
                break
    return values


def _record_path(workspace: Path, name: str) -> Path:
    return workspace / "indexes" / "records" / f"{name}.json"


def _read_previous_record(workspace: Path, name: str) -> tuple[dict[str, Any] | None, str | None]:
    path = _record_path(workspace, name)
    if not path.is_file():
        return None, "record_missing"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, "record_invalid"
    if not isinstance(value, dict):
        return None, "record_invalid"
    return value, None


def _capability_signature(capability: dict[str, Any]) -> dict[str, Any]:
    incremental = capability.get("incremental_detection") or {}
    return {
        "version": capability.get("version"),
        "analyze_available": capability.get("analyze_available"),
        "safe_arguments": capability.get("safe_arguments", []),
        "incremental_detection": {
            "available": incremental.get("available"),
            "basis": incremental.get("basis"),
        },
    }


def _baseline_status(previous: dict[str, Any] | None, read_error: str | None, shadow: Path,
                     source_commit: str, capability: dict[str, Any]) -> dict[str, Any]:
    if read_error:
        return {"usable": False, "reason": read_error}
    assert previous is not None
    if previous.get("status") not in {"indexed", "unchanged"}:
        return {"usable": False, "reason": f"previous_status_{previous.get('status', 'unknown')}"}
    if previous.get("source_commit") != source_commit or previous.get("shadow_commit") != source_commit:
        return {"usable": False, "reason": "commit_mismatch"}
    if not (shadow / ".gitnexus").is_dir():
        return {"usable": False, "reason": "index_missing"}
    previous_capability = previous.get("gitnexus")
    if not isinstance(previous_capability, dict) or _capability_signature(previous_capability) != _capability_signature(capability):
        return {"usable": False, "reason": "capability_changed"}
    return {"usable": True, "reason": "successful_compatible_index"}


def _write_record(workspace: Path, name: str, record: dict[str, Any]) -> dict[str, Any]:
    path = _record_path(workspace, name)
    record["record_path"] = str(path)
    data_runtime.atomic_write_json(path, record)
    return record


def index_repository(root: Path, repository: str, *, runner: CommandRunner = _run,
                     which: Callable[[str], str | None] = shutil.which) -> dict[str, Any]:
    """Index one managed repository, returning a durable structured outcome."""
    started, monotonic = _now(), time.monotonic()
    name = _safe_repository_name(repository)
    workspace = data_runtime.ensure_layout(root)
    base: dict[str, Any] = {
        "schema_version": RECORD_SCHEMA_VERSION, "record_type": "gitnexus_index",
        "repository": name, "started_at": started, "ended_at": None, "duration_seconds": None,
        "status": "failed", "index_mode": None, "source_commit": None, "shadow_commit": None,
        "gitnexus": {}, "statistics": {}, "failure": None, "degradation": None,
        "baseline": {"usable": False, "reason": "not_evaluated"},
    }
    try:
        workspace, source, source_commit = _source_repository(root, name, runner)
        base["source_repository"] = str(source)
        base["source_commit"] = source_commit
        executable = which("gitnexus")
        capability = _gitnexus_capability(runner, executable)
        base["gitnexus"] = capability
        if not capability["available"]:
            base.update({"status": "skipped", "degradation": "GitNexus 不可用；保留源码搜索与人工调用链分析。"})
            return _finish(workspace, name, base, monotonic)
        shadow, mode = _shadow_repository(workspace, name, source, source_commit, runner)
        base["shadow_repository"] = str(shadow)
        base["shadow_commit"] = source_commit
        base["index_mode"] = mode
        if mode == "unchanged":
            previous, read_error = _read_previous_record(workspace, name)
            baseline = _baseline_status(previous, read_error, shadow, source_commit, capability)
            base["baseline"] = baseline
            if baseline["usable"]:
                base.update({"status": "unchanged", "statistics": {"shadow_disk_bytes": _directory_size(shadow)}})
                return _finish(workspace, name, base, monotonic)
            base["index_mode"] = "retry"
        else:
            base["baseline"] = {"usable": False, "reason": "shadow_created" if mode == "cold" else "source_commit_changed"}
        command = [executable, "analyze", str(shadow), *capability["safe_arguments"]]
        result = runner(command, None, NEXUS_TIMEOUT_SECONDS)
        output = _output(result)
        base["gitnexus"]["command_capability"] = {"analyze": True, "safe_arguments": capability["safe_arguments"]}
        if result.returncode:
            base.update({"status": "failed", "failure": output or f"gitnexus exit {result.returncode}",
                         "degradation": "GitNexus 索引失败；本次降级为源码搜索与人工调用链分析。"})
            return _finish(workspace, name, base, monotonic)
        stats = _reported_stats(output)
        stats["shadow_disk_bytes"] = _directory_size(shadow)
        nexus_dir = shadow / ".gitnexus"
        if nexus_dir.is_dir():
            stats["index_disk_bytes"] = _directory_size(nexus_dir)
        base.update({"status": "indexed", "statistics": stats})
    except (IndexRuntimeError, OSError, json.JSONDecodeError) as exc:
        base["failure"] = str(exc)
    return _finish(workspace, name, base, monotonic)


def _finish(workspace: Path, name: str, record: dict[str, Any], monotonic: float) -> dict[str, Any]:
    record["ended_at"] = _now()
    record["duration_seconds"] = round(time.monotonic() - monotonic, 3)
    return _write_record(workspace, name, record)


def index_all(root: Path, *, runner: CommandRunner = _run,
              which: Callable[[str], str | None] = shutil.which) -> dict[str, Any]:
    workspace = data_runtime.ensure_layout(root)
    names = sorted(path.name for path in (workspace / "repositories").iterdir() if path.is_dir() and not path.is_symlink())
    records: list[dict[str, Any]] = []
    for name in names:
        try:
            records.append(index_repository(root, name, runner=runner, which=which))
        except IndexRuntimeError as exc:
            # Keep walking even when a user-created directory cannot be a valid
            # repository identifier.  It is intentionally not persisted: an
            # unsafe name must never influence a records-path calculation.
            records.append({
                "schema_version": RECORD_SCHEMA_VERSION, "record_type": "gitnexus_index",
                "repository": name, "started_at": _now(), "ended_at": _now(), "duration_seconds": 0,
                "status": "failed", "index_mode": None, "source_commit": None, "shadow_commit": None,
                "gitnexus": {}, "statistics": {}, "failure": str(exc), "degradation": None,
                "baseline": {"usable": False, "reason": "repository_rejected"},
            })
    return {"repositories": records, "indexed": sum(item["status"] == "indexed" for item in records),
            "failed": sum(item["status"] == "failed" for item in records)}
