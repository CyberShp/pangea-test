#!/usr/bin/env python3
"""Deterministic, local-only runtime for the PANGEA data workspace.

This module deliberately owns metadata only.  User supplied inbox files are
never moved and repositories are only ever updated through a fast-forward
``git pull`` after conservative admission checks.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.process_runtime import run_text


class DataRuntimeError(RuntimeError):
    pass


LAYOUT = ("inbox", "repositories", "runs")
OPTIONAL_LAYOUT = ("library", "indexes", "reports", "tmp", "contracts", "session")
REQUIRED_RUN_LAYOUT = ("internal",)
OPTIONAL_RUN_LAYOUT = ("checkpoints", "evidence", "internal/audit", "tmp", "final")
# ``final`` is accepted only for historical Runs created before the reports/
# migration. New Runs never create or write it.
CATALOG_NAME = "catalog.jsonl"

STATUS = {
    "mapping": {"label": "梳理中", "face": "(._.)"},
    "analyzing": {"label": "分析中", "face": "(｀・ω・´)"},
    "mining": {"label": "挖掘中", "face": "(ง •̀_•́)ง"},
    "reviewing": {"label": "审核中", "face": "(¬_¬)"},
    "waiting": {"label": "发呆中", "face": "(－_－)"},
    "degraded": {"label": "难过中", "face": "(；へ：)"},
    "escalated": {"label": "狂躁中", "face": "(╬ಠ益ಠ)"},
    "completed": {"label": "高兴中", "face": "(￣▽￣)b"},
}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
GIT_TIMEOUT_SECONDS = 20


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def atomic_write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_json_exclusive(path: Path, value: Any) -> None:
    content = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise DataRuntimeError(f"拒绝覆盖既有文件: {path}") from exc
    created = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        current = path.lstat()
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino):
            raise DataRuntimeError(f"新建文件在发布期间被替换: {path}")
    except BaseException:
        try:
            current = path.lstat()
            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                path.unlink()
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataRuntimeError(f"JSON 无效: {path}: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _lstat(path: Path, kind: str) -> os.stat_result:
    try:
        return path.lstat()
    except FileNotFoundError as exc:
        raise DataRuntimeError(f"缺少受管 {kind}: {path}") from exc


def _require_regular_file(path: Path, workspace: Path, kind: str) -> os.stat_result:
    info = _lstat(path, kind)
    if not stat.S_ISREG(info.st_mode):
        raise DataRuntimeError(f"拒绝非普通文件 {kind}: {path}")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise DataRuntimeError(f"无法解析 {kind}: {path}") from exc
    if not _is_within(resolved, workspace):
        raise DataRuntimeError(f"拒绝越界 {kind}: {path}")
    return info


def data_root(root: Path) -> Path:
    """Return the direct, physical workspace child owned by ``root``.

    ``Path.resolve`` is deliberately applied to the caller-provided root, not
    to the workspace itself: a ``pangea-data`` link must never turn an
    externally-owned directory into a managed workspace.
    """
    normalized_root = Path(root).expanduser().resolve()
    if normalized_root.exists() and not normalized_root.is_dir():
        raise DataRuntimeError(f"root 不是目录: {normalized_root}")
    workspace = normalized_root / "pangea-data"
    # is_symlink() uses lstat(), including for dangling links.
    if workspace.is_symlink():
        raise DataRuntimeError(f"拒绝符号链接 data workspace: {workspace}")
    if workspace.exists() and not stat.S_ISDIR(workspace.lstat().st_mode):
        raise DataRuntimeError(f"data workspace 不是目录: {workspace}")
    return workspace


def ensure_layout(root: Path) -> Path:
    workspace = data_root(root)
    workspace.mkdir(exist_ok=True)
    workspace_resolved = _require_managed_directory(workspace, workspace, "data workspace")
    for relative in LAYOUT:
        directory = workspace / relative
        _ensure_managed_directory(directory, workspace_resolved, f"受管目录 {relative}")
    for relative in OPTIONAL_LAYOUT:
        directory = workspace / relative
        if directory.exists() or directory.is_symlink():
            _require_managed_directory(directory, workspace_resolved, f"受管目录 {relative}")
    return workspace


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _require_managed_directory(path: Path, workspace: Path, kind: str) -> Path:
    """Require every component from the workspace to be a real, contained directory."""
    try:
        relative = path.relative_to(workspace)
    except ValueError as exc:
        raise DataRuntimeError(f"拒绝越界 {kind}: {path}") from exc
    current = workspace
    components = (Path("."), *relative.parts)
    for component in components:
        if component != Path("."):
            current /= component
        info = _lstat(current, kind)
        if not stat.S_ISDIR(info.st_mode):
            raise DataRuntimeError(f"拒绝非目录 {kind}: {current}")
        try:
            resolved = current.resolve(strict=True)
        except OSError as exc:
            raise DataRuntimeError(f"无法解析 {kind}: {current}") from exc
        if not _is_within(resolved, workspace):
            raise DataRuntimeError(f"拒绝越界 {kind}: {current}")
    return path.resolve(strict=True)


def _ensure_managed_directory(path: Path, workspace: Path, kind: str) -> Path:
    """Create missing components only after every existing parent is verified."""
    try:
        relative = path.relative_to(workspace)
    except ValueError as exc:
        raise DataRuntimeError(f"拒绝越界 {kind}: {path}") from exc
    current = workspace
    _require_managed_directory(current, workspace, kind)
    for component in relative.parts:
        current /= component
        if not current.exists() and not current.is_symlink():
            current.mkdir()
        _require_managed_directory(current, workspace, kind)
    return current.resolve(strict=True)


def _reject_cleanup_path(kind: str, path: Path) -> None:
    raise DataRuntimeError(f"拒绝清理不安全的 {kind}: {path}")


def _require_cleanup_directory(path: Path, parent: Path, kind: str) -> Path:
    """Validate a physical cleanup directory without following a link."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        _reject_cleanup_path(kind, path)
    if not stat.S_ISDIR(info.st_mode):
        _reject_cleanup_path(kind, path)
    resolved = path.resolve(strict=True)
    if not _is_within(resolved, parent):
        _reject_cleanup_path(kind, path)
    return resolved


def _catalog_path(workspace: Path) -> Path:
    return workspace / "library" / CATALOG_NAME


def _workspace_file(workspace: Path, relative: str, kind: str) -> Path:
    candidate = workspace / relative
    workspace_resolved = workspace.resolve(strict=True)
    _require_managed_directory(candidate.parent, workspace_resolved, f"{kind}目录")
    _require_regular_file(candidate, workspace_resolved, kind)
    return candidate


def _validate_conversion_artifacts(record: dict[str, Any], workspace: Path) -> None:
    markdown_path = record.get("markdown_path")
    if not isinstance(markdown_path, str):
        raise DataRuntimeError("converted catalog 缺少 markdown_path")
    _workspace_file(workspace, markdown_path, "既有 Markdown 输出")
    asset_paths = record.get("asset_paths", [])
    if not isinstance(asset_paths, list) or not all(isinstance(path, str) for path in asset_paths):
        raise DataRuntimeError("converted catalog 的 asset_paths 无效")
    for asset_path in asset_paths:
        _workspace_file(workspace, asset_path, "既有转换资产")


def _validate_output_target(path: Path, workspace: Path, kind: str) -> None:
    workspace_resolved = workspace.resolve(strict=True)
    _ensure_managed_directory(path.parent, workspace_resolved, f"{kind}目录")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(info.st_mode):
        raise DataRuntimeError(f"拒绝非普通{kind}目标: {path}")
    if not _is_within(path.resolve(strict=True), workspace_resolved):
        raise DataRuntimeError(f"拒绝越界{kind}目标: {path}")


def _open_regular_beneath(workspace: Path, path: Path, kind: str) -> int:
    """Open a regular file through no-follow directory descriptors rooted at workspace."""
    workspace_resolved = workspace.resolve(strict=True)
    try:
        relative = path.relative_to(workspace)
    except ValueError as exc:
        raise DataRuntimeError(f"拒绝越界{kind}: {path}") from exc
    if not relative.parts:
        raise DataRuntimeError(f"{kind}缺少文件名: {path}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(workspace_resolved, directory_flags)
        descriptors.append(current)
        for component in relative.parts[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=current)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            raise DataRuntimeError(f"{kind}不是普通文件: {path}")
        return descriptor
    except OSError as exc:
        raise DataRuntimeError(f"无法安全打开{kind}: {path}") from exc
    finally:
        for descriptor_to_close in reversed(descriptors):
            os.close(descriptor_to_close)


def _stage_workspace_file(path: Path, staging: Path, workspace: Path) -> tuple[Path, str]:
    """Copy one no-follow workspace file while computing the copied bytes' digest."""
    _require_managed_directory(staging, workspace.resolve(strict=True), "转换临时目录")
    source_fd = _open_regular_beneath(workspace, path, "内容寻址归档")
    temporary = staging / path.name
    digest = hashlib.sha256()
    try:
        source_info = os.fstat(source_fd)
        if not stat.S_ISREG(source_info.st_mode):
            raise DataRuntimeError(f"内容寻址归档不是普通文件: {path}")
        destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        destination_fd = os.open(temporary, destination_flags, 0o600)
        with os.fdopen(source_fd, "rb", closefd=False) as source, os.fdopen(destination_fd, "wb") as handle:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(source_fd)
    _require_regular_file(temporary, workspace.resolve(strict=True), "转换输入快照")
    return temporary, digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DataRuntimeError(f"catalog 第 {number} 行无效: {exc}") from exc
        if not isinstance(value, dict):
            raise DataRuntimeError(f"catalog 第 {number} 行必须是对象")
        records.append(value)
    return records


def infer_document_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".md": "markdown", ".txt": "text", ".doc": "word", ".docx": "word",
        ".xls": "spreadsheet", ".xlsx": "spreadsheet", ".csv": "spreadsheet",
        ".ppt": "presentation", ".pptx": "presentation", ".pdf": "pdf",
        ".json": "json", ".html": "html",
    }.get(suffix, "other")


def _archive_source(temporary: Path, suffix: str, workspace: Path, checksum: str) -> str:
    """Atomically retain a verified staging copy as a content-addressed source."""
    target = workspace / "library" / "sources" / f"{checksum}{suffix}"
    _ensure_managed_directory(target.parent, workspace.resolve(strict=True), "归档目录")
    if target.exists() or target.is_symlink():
        _require_regular_file(target, workspace, "既有内容寻址归档")
        if sha256_file(target) != checksum:
            raise DataRuntimeError(f"既有内容寻址归档摘要不匹配: {target}")
        temporary.unlink()
    else:
        temporary.replace(target)
        _require_regular_file(target, workspace, "新内容寻址归档")
        if sha256_file(target) != checksum:
            raise DataRuntimeError(f"新内容寻址归档摘要不匹配: {target}")
    return str(target.relative_to(workspace))


def _stage_inbox_file(path: Path, workspace: Path) -> tuple[Path, str, os.stat_result]:
    """Copy one inbox file once, so hashing and archiving see identical bytes."""
    staging = workspace / "tmp"
    _ensure_managed_directory(staging, workspace.resolve(strict=True), "导入临时目录")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        source_fd = os.open(path, flags)
    except OSError as exc:
        raise DataRuntimeError(f"无法安全读取 inbox 文件: {path}") from exc
    try:
        source_info = os.fstat(source_fd)
        if not stat.S_ISREG(source_info.st_mode):
            raise DataRuntimeError(f"拒绝非普通 inbox 文件: {path}")
        with os.fdopen(source_fd, "rb", closefd=False) as source, tempfile.NamedTemporaryFile(
            "wb", dir=staging, prefix="inbox-", delete=False
        ) as handle:
            shutil.copyfileobj(source, handle)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
    finally:
        os.close(source_fd)
    temporary_info = _require_regular_file(temporary, workspace, "导入临时文件")
    return temporary, sha256_file(temporary), temporary_info


def _inbox_files(inbox: Path, workspace: Path) -> list[Path]:
    """Walk inbox without following links; every discovered object must be regular."""
    files: list[Path] = []

    def visit(directory: Path) -> None:
        _require_managed_directory(directory, workspace, "inbox 目录")
        for entry in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                _require_regular_file(path, workspace, "inbox 文件")
                files.append(path)
            else:
                raise DataRuntimeError(f"拒绝非普通 inbox 项: {path}")

    visit(inbox)
    return files


def _conversion_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key.startswith("conversion_") or key in {
        "markdown_path", "asset_paths", "source_archive_path", "converted_at",
    }}


def _remove_empty_managed_directory(path: Path, workspace: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    resolved = _require_managed_directory(path, workspace.resolve(strict=True), "空目录清理")
    if not any(path.iterdir()):
        path.rmdir()
        del resolved


def scan_inbox(root: Path) -> dict[str, Any]:
    workspace = ensure_layout(root)
    inbox = workspace / "inbox"
    old_records = _read_jsonl(_catalog_path(workspace))
    previous = {row.get("source_path"): row for row in old_records}
    by_hash = {row.get("sha256"): row for row in old_records if isinstance(row.get("sha256"), str)}
    current: list[dict[str, Any]] = []
    added = changed = unchanged = 0
    _require_managed_directory(inbox, workspace.resolve(), "inbox 目录")
    for path in _inbox_files(inbox, workspace.resolve()):
        relative = path.relative_to(inbox).as_posix()
        temporary, checksum, source_stat = _stage_inbox_file(path, workspace)
        old = previous.get(relative)
        if old is None:
            added += 1
        elif old.get("sha256") == checksum:
            unchanged += 1
        else:
            changed += 1
        record = {
            "record_type": "input_source", "source_path": relative,
            "absolute_path": str(path.resolve(strict=True)), "sha256": checksum,
            "size_bytes": source_stat.st_size,
            "modified_at": datetime.fromtimestamp(source_stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "format": infer_document_kind(path),
            "discovered_at": old.get("discovered_at", utc_now()) if old else utc_now(),
        }
        # Same path keeps its conversion record; a renamed duplicate may reuse
        # an already converted content-addressed artifact as well.
        metadata_source = old if old and old.get("sha256") == checksum else by_hash.get(checksum)
        if metadata_source:
            record.update(_conversion_metadata(metadata_source))
        else:
            record["conversion_status"] = "pending"
        try:
            record["source_archive_path"] = _archive_source(temporary, path.suffix.lower(), workspace, checksum)
        finally:
            if temporary.exists():
                temporary.unlink()
        current.append(record)
    catalog = _catalog_path(workspace)
    if current or old_records or catalog.exists():
        atomic_write_jsonl(catalog, current)
    _remove_empty_managed_directory(workspace / "tmp", workspace)
    return {"catalog": str(catalog), "added": added, "changed": changed,
            "unchanged": unchanged, "removed": len(set(previous) - {r["source_path"] for r in current}),
            "count": len(current)}


def convert_catalog(root: Path) -> dict[str, Any]:
    """Convert supported catalog entries once per SHA-256 and preserve metadata."""
    from runtime import converters

    workspace = ensure_layout(root)
    catalog = _catalog_path(workspace)
    records = _read_jsonl(catalog)
    converted = reused = pending = skipped = 0
    if not records:
        return {"catalog": str(catalog), "converted": 0, "reused": 0,
                "pending": 0, "skipped": 0, "count": 0}
    by_hash: dict[str, dict[str, Any]] = {}
    for record in records:
        checksum = record.get("sha256")
        if not isinstance(checksum, str):
            continue
        prior = by_hash.get(checksum)
        if prior and prior.get("conversion_status") in {"converted", "pending"}:
            _validate_conversion_artifacts(prior, workspace)
            record.update(_conversion_metadata(prior)); reused += 1; continue
        if record.get("conversion_status") == "converted" and record.get("markdown_path"):
            _validate_conversion_artifacts(record, workspace)
            by_hash[checksum] = record; reused += 1; continue
        archive_path = record.get("source_archive_path")
        if not isinstance(archive_path, str):
            raise DataRuntimeError("catalog 缺少 source_archive_path")
        archive = _workspace_file(workspace, archive_path, "内容寻址归档")
        if archive.suffix.lower() not in {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf", ".csv", ".md", ".txt"}:
            record["conversion_status"] = "skipped"; skipped += 1; by_hash[checksum] = record; continue
        try:
            conversion_tmp = workspace / "tmp"
            _ensure_managed_directory(conversion_tmp, workspace.resolve(strict=True), "转换临时目录")
            with tempfile.TemporaryDirectory(prefix="conversion-", dir=conversion_tmp) as temporary_name:
                conversion_root = Path(temporary_name)
                _require_managed_directory(conversion_root, workspace.resolve(strict=True), "转换临时目录")
                staged_archive, staged_checksum = _stage_workspace_file(archive, conversion_root, workspace)
                if staged_checksum != checksum:
                    raise DataRuntimeError(f"内容寻址归档摘要不匹配: {archive}")
                result = converters.convert_document(
                    staged_archive, conversion_root / "converted", managed_root=conversion_root
                )
                _require_regular_file(staged_archive, workspace.resolve(strict=True), "转换输入快照")
                if sha256_file(staged_archive) != checksum:
                    raise DataRuntimeError("转换输入快照在转换期间发生变化")

                markdown_path = workspace / "library" / "markdown" / f"{checksum}.md"
                final_assets = [
                    workspace / "library" / "assets" / checksum / "assets" / asset.name
                    for asset in result.assets
                ]
                _validate_output_target(markdown_path, workspace, "Markdown 输出")
                for source_asset, final_asset in zip(result.assets, final_assets):
                    _require_regular_file(source_asset, conversion_root, "转换资产")
                    _validate_output_target(final_asset, workspace, "转换资产")
                for source_asset, final_asset in zip(result.assets, final_assets):
                    converters.publish_file(source_asset, final_asset, managed_root=workspace)
                converters.write_markdown(result, markdown_path, managed_root=workspace)
            record.update({
                "conversion_status": result.status,
                "conversion_sha256": checksum,
                "markdown_path": str(markdown_path.relative_to(workspace)),
                "asset_paths": [str(path.relative_to(workspace)) for path in final_assets],
                "converted_at": utc_now(),
            })
            if result.status == "converted": converted += 1
            else: pending += 1
        except converters.OutputSecurityError as exc:
            raise DataRuntimeError(str(exc)) from exc
        except (OSError, converters.ConversionError) as exc:
            record.update({"conversion_status": "failed", "conversion_error": str(exc), "converted_at": utc_now()})
        by_hash[checksum] = record
    atomic_write_jsonl(catalog, records)
    _remove_empty_managed_directory(workspace / "tmp", workspace)
    return {"catalog": str(catalog), "converted": converted, "reused": reused,
            "pending": pending, "skipped": skipped, "count": len(records)}


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # A new session must never block on an interactive credential request.
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return run_text(
            ["git", "-C", str(repo), *args],
            timeout=GIT_TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["git", "-C", str(repo), *args], 124, "", f"git 命令超时（{GIT_TIMEOUT_SECONDS}s）"
        )


def _git_failure(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stderr or result.stdout or "git 命令失败").strip()


def _worktree_change_summary(output: str) -> dict[str, int]:
    summary = {"total": 0, "deleted": 0, "modified": 0, "added": 0,
               "untracked": 0, "renamed": 0, "conflicted": 0}
    for line in output.splitlines():
        if not line:
            continue
        code = line[:2]
        summary["total"] += 1
        if code == "??":
            summary["untracked"] += 1
            continue
        if "D" in code:
            summary["deleted"] += 1
        if "M" in code:
            summary["modified"] += 1
        if "A" in code:
            summary["added"] += 1
        if "R" in code:
            summary["renamed"] += 1
        if "U" in code or code in {"AA", "DD", "AU", "UA", "DU", "UD"}:
            summary["conflicted"] += 1
    return summary


def _blocked_repository(name: str, reason: str) -> dict[str, Any]:
    return {
        "repository": name,
        "status": "blocked",
        "access_status": "blocked",
        "head_commit": None,
        "worktree_status": "unknown",
        "worktree_changes": {"total": 0, "deleted": 0, "modified": 0, "added": 0,
                                 "untracked": 0, "renamed": 0, "conflicted": 0},
        "update_status": "not_attempted",
        "update_reason": reason,
        "reason": reason,
        "index_eligible": False,
        "snapshot_eligible": False,
    }


def _ready_repository(name: str, head_commit: str, worktree_status: str,
                      changes: dict[str, int], update_status: str,
                      reason: str) -> dict[str, Any]:
    return {
        "repository": name,
        "status": "ready",
        "access_status": "ready",
        "head_commit": head_commit,
        "worktree_status": worktree_status,
        "worktree_changes": changes,
        "update_status": update_status,
        "update_reason": reason,
        "reason": reason,
        "index_eligible": True,
        "snapshot_eligible": True,
    }


def safe_pull_repositories(root: Path) -> list[dict[str, Any]]:
    workspace = ensure_layout(root)
    outcomes: list[dict[str, Any]] = []
    repositories = workspace / "repositories"
    repositories_resolved = _require_managed_directory(repositories, workspace.resolve(), "repositories 目录")
    candidates = sorted(path for path in repositories.iterdir() if path.is_dir() or path.is_symlink())
    for repo in candidates:
        name = repo.name
        if repo.is_symlink():
            outcomes.append(_blocked_repository(name, "拒绝符号链接仓库目录"))
            continue
        try:
            _require_managed_directory(repo, repositories_resolved, "仓库目录")
        except DataRuntimeError as exc:
            outcomes.append(_blocked_repository(name, str(exc)))
            continue
        inside = _git(repo, "rev-parse", "--is-inside-work-tree")
        if inside.returncode or (inside.stdout or "").strip() != "true":
            outcomes.append(_blocked_repository(name, "不是 Git 工作树"))
            continue
        top_level = _git(repo, "rev-parse", "--show-toplevel")
        top_level_output = (top_level.stdout or "").strip()
        if top_level.returncode or not top_level_output:
            outcomes.append(_blocked_repository(name, "无法确认 Git 工作树根目录"))
            continue
        try:
            is_registered_worktree = Path(top_level_output).resolve() == repo.resolve()
        except OSError:
            is_registered_worktree = False
        if not is_registered_worktree:
            outcomes.append(_blocked_repository(name, "不是独立登记的 Git 工作树"))
            continue
        revision = _git(repo, "rev-parse", "--verify", "HEAD^{commit}")
        head_commit = (revision.stdout or "").strip()
        if revision.returncode or not head_commit:
            outcomes.append(_blocked_repository(name, "无法读取仓库 HEAD commit"))
            continue

        dirty = _git(repo, "status", "--porcelain=v1")
        if dirty.returncode:
            reason = f"无法检查工作区状态：{_git_failure(dirty)}；仅跳过自动 git pull，仓库读取、索引和 commit 快照仍可用"
            outcomes.append(_ready_repository(
                name, head_commit, "unknown", _worktree_change_summary(""), "skipped", reason
            ))
            continue
        changes = _worktree_change_summary(dirty.stdout or "")
        if changes["total"]:
            reason = (
                f"工作区存在未提交修改（共 {changes['total']} 项，删除 {changes['deleted']} 项）；"
                "仅跳过自动 git pull，仓库读取、索引和 commit 快照仍可用"
            )
            outcomes.append(_ready_repository(name, head_commit, "dirty", changes, "skipped", reason))
            continue

        branch = _git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
        if branch.returncode or not (branch.stdout or "").strip():
            reason = "HEAD 未附着分支；仅跳过自动 git pull，仓库读取、索引和 commit 快照仍可用"
            outcomes.append(_ready_repository(name, head_commit, "clean", changes, "skipped", reason))
            continue
        upstream = _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
        if upstream.returncode:
            reason = "当前分支未配置上游；仅跳过自动 git pull，仓库读取、索引和 commit 快照仍可用"
            outcomes.append(_ready_repository(name, head_commit, "clean", changes, "skipped", reason))
            continue
        pull = _git(repo, "pull", "--ff-only")
        if pull.returncode:
            reason = f"自动 git pull --ff-only 失败：{_git_failure(pull)}；继续使用当前 HEAD 进行读取、索引和快照"
            outcomes.append(_ready_repository(name, head_commit, "clean", changes, "skipped", reason))
            continue
        updated_revision = _git(repo, "rev-parse", "--verify", "HEAD^{commit}")
        updated_commit = (updated_revision.stdout or "").strip()
        if not updated_revision.returncode and updated_commit:
            head_commit = updated_commit
        message = (pull.stdout or pull.stderr or "").strip() or "已检查更新"
        outcomes.append(_ready_repository(name, head_commit, "clean", changes, "updated", message))
    return outcomes


def _safe_run_path(workspace: Path, run_id: str) -> Path:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise DataRuntimeError("run_id 非法")
    runs = workspace / "runs"
    _require_managed_directory(runs, workspace.resolve(), "runs 目录")
    return runs / run_id


def _require_run_directory(workspace: Path, run_dir: Path, run_id: str) -> Path:
    runs = workspace / "runs"
    runs_resolved = _require_managed_directory(runs, workspace.resolve(), "runs 目录")
    resolved = _require_managed_directory(run_dir, runs_resolved, "Run 目录")
    for directory in REQUIRED_RUN_LAYOUT:
        _require_managed_directory(run_dir / directory, resolved, f"Run 固定目录 {directory}")
    for directory in OPTIONAL_RUN_LAYOUT:
        candidate = run_dir / directory
        if candidate.exists() or candidate.is_symlink():
            _require_managed_directory(candidate, resolved, f"Run 可选目录 {directory}")
    return resolved


def validate_runtime_record(instance: dict[str, Any], schema_name: str) -> None:
    """Use the existing deterministic schema validator without a dependency at import time."""
    from runtime import runctl
    try:
        runctl.validate(instance, schema_name)
    except runctl.RunCtlError as exc:
        raise DataRuntimeError(str(exc)) from exc


def create_run(root: Path, run_id: str, contract: dict[str, Any], max_audit_rounds: int = 2) -> dict[str, Any]:
    workspace = ensure_layout(root)
    run_dir = _safe_run_path(workspace, run_id)
    if run_dir.exists() or run_dir.is_symlink():
        raise DataRuntimeError(f"Run 已存在: {run_id}")
    if not 1 <= max_audit_rounds <= 5:
        raise DataRuntimeError("max_audit_rounds 必须在 1 到 5 之间")
    now = utc_now()
    manifest = {
        "schema_version": "2.0", "run_id": run_id, "status": "active", "created_at": now,
        "updated_at": now, "machine_state": "mapping", "contract_file": "internal/task-contract.json",
        "checkpoint_count": 0, "risk_ledger_file": "internal/risk-ledger.json",
        "deliverables": None,
        "audit": {"rounds": 0, "max_rounds": max_audit_rounds, "status": "pending",
                  "opinion_file": None, "required_actions": []},
    }
    validate_runtime_record(contract, "task-contract.schema.json")
    validate_runtime_record(manifest, "session-manifest.schema.json")
    try:
        run_dir.mkdir()
    except FileExistsError as exc:
        raise DataRuntimeError(f"Run 已存在: {run_id}") from exc
    run_resolved = _require_managed_directory(run_dir, (workspace / "runs").resolve(), "Run 目录")
    for directory in REQUIRED_RUN_LAYOUT:
        _ensure_managed_directory(run_dir / directory, run_resolved, f"Run 固定目录 {directory}")
    _require_run_directory(workspace, run_dir, run_id)
    atomic_write_json(run_dir / "internal" / "task-contract.json", contract)
    ledger = {
        "schema_version": "1.0", "run_id": run_id, "updated_at": now, "risks": []
    }
    validate_runtime_record(ledger, "risk-ledger.schema.json")
    atomic_write_json(run_dir / "internal" / "risk-ledger.json", ledger)
    atomic_write_json(run_dir / "manifest.json", manifest)
    return {"run_dir": str(run_dir), "manifest": manifest}


def incomplete_runs(root: Path) -> list[dict[str, Any]]:
    workspace = ensure_layout(root)
    result: list[dict[str, Any]] = []
    runs = workspace / "runs"
    runs_resolved = _require_managed_directory(runs, workspace.resolve(), "runs 目录")
    for child in sorted(runs.iterdir()):
        if child.is_symlink() or not stat.S_ISDIR(child.lstat().st_mode):
            raise DataRuntimeError(f"拒绝非目录 Run 项: {child}")
        _require_run_directory(workspace, child, child.name)
        manifest_path = child / "manifest.json"
        _require_regular_file(manifest_path, runs_resolved, "Run manifest")
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("status") in TERMINAL_RUN_STATUSES:
            continue
        result.append({"run_id": manifest.get("run_id", child.name), "status": manifest.get("status", "unknown"),
                       "machine_state": manifest.get("machine_state", "unknown"),
                       "updated_at": manifest.get("updated_at"), "run_dir": str(child)})
    return result


def cleanup_stale_tmp(root: Path, stale_hours: int = 24) -> dict[str, Any]:
    if stale_hours < 1:
        raise DataRuntimeError("stale_hours 必须至少为 1")
    workspace = ensure_layout(root)
    cutoff = datetime.now(timezone.utc).timestamp() - stale_hours * 3600
    removed: list[str] = []
    runs = workspace / "runs"
    workspace_resolved = workspace.resolve()
    runs_resolved = _require_cleanup_directory(runs, workspace_resolved, "runs 目录")
    stale_candidates: list[Path] = []
    for run_dir in runs.iterdir():
        if run_dir.is_symlink():
            _reject_cleanup_path("run 目录", run_dir)
        if not run_dir.is_dir():
            continue
        _require_cleanup_directory(run_dir, runs_resolved, "run 目录")
        run_resolved = _require_run_directory(workspace, run_dir, run_dir.name)
        manifest_path = run_dir / "manifest.json"
        if manifest_path.is_symlink():
            _reject_cleanup_path("run manifest", manifest_path)
        manifest = read_json(manifest_path)
        # Active and paused Runs may depend on immutable MR snapshots for an
        # exact-commit resume. Age alone must never invalidate that binding.
        if not isinstance(manifest, dict) or manifest.get("status") not in TERMINAL_RUN_STATUSES:
            continue
        tmp = run_dir / "tmp"
        if not tmp.exists() and not tmp.is_symlink():
            continue
        tmp_resolved = _require_cleanup_directory(tmp, run_resolved, "tmp 目录")
        for candidate in tmp.iterdir():
            # Never use stat() before rejecting a link: stat follows both live
            # and dangling-link targets and would make cleanup target-owned.
            if candidate.is_symlink():
                _reject_cleanup_path("tmp 候选项", candidate)
            resolved = candidate.resolve()
            if not _is_within(resolved, tmp_resolved):
                _reject_cleanup_path("tmp 候选项", candidate)
            if candidate.stat().st_mtime >= cutoff:
                continue
            stale_candidates.append(candidate)
    # Validation above completes before any deletion, so a later malformed
    # entry cannot leave a terminal run only partially cleaned.
    for candidate in stale_candidates:
        if candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink()
        removed.append(str(candidate))
    return {"removed": removed, "stale_hours": stale_hours}


def _load_run(root: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    workspace = ensure_layout(root)
    run_dir = _safe_run_path(workspace, run_id)
    run_resolved = _require_run_directory(workspace, run_dir, run_id)
    manifest_path = run_dir / "manifest.json"
    _require_regular_file(manifest_path, run_resolved, "Run manifest")
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
        raise DataRuntimeError(f"Run 不存在或 manifest 无效: {run_id}")
    return run_resolved, manifest


def set_run_state(root: Path, run_id: str, state: str, message: str) -> dict[str, Any]:
    if state not in STATUS:
        raise DataRuntimeError(f"未知状态码: {state}")
    run_dir, manifest = _load_run(root, run_id)
    if manifest.get("status") in TERMINAL_RUN_STATUSES:
        raise DataRuntimeError("已结束 Run 不可更新状态")
    event = {"at": utc_now(), "machine_state": state, "message": message, "display": STATUS[state]}
    events_path = run_dir / "internal" / "state-events.jsonl"
    existing = _read_jsonl(events_path)
    if existing and existing[-1].get("machine_state") == state:
        # States are phase events, not a token-stream decoration. Repeating a
        # phase does not manufacture a new emotional transition.
        return {**existing[-1], "deduplicated": True}
    atomic_write_jsonl(events_path, [*existing, event])
    manifest["machine_state"] = state
    manifest["updated_at"] = event["at"]
    atomic_write_json(run_dir / "manifest.json", manifest)
    return event



def _verify_checkpoint_artifacts(run_dir: Path, manifest: dict[str, Any], checkpoint: dict[str, Any]) -> None:
    """Lifecycle completed stages must bind their fixed stage artifact by current SHA-256."""
    if manifest.get("contract_record_file") != "internal/contract-record.json":
        return
    if checkpoint.get("status", "completed") != "completed" or checkpoint.get("stage") in {"report", "rework"}:
        return
    bindings = checkpoint.get("artifact_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise DataRuntimeError("生命周期 Run 的 completed checkpoint 必须提供 artifact_bindings")
    expected_path = f"internal/stages/{checkpoint.get('stage')}.json"
    found = False
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise DataRuntimeError("checkpoint artifact binding 必须只包含 path 和 sha256")
        raw = binding.get("path")
        if not isinstance(raw, str) or Path(raw).is_absolute() or ".." in Path(raw).parts or Path(raw).as_posix() != raw:
            raise DataRuntimeError(f"checkpoint artifact 路径不安全: {raw}")
        artifact = run_dir / raw
        _require_regular_file(artifact, run_dir, "checkpoint 绑定工件")
        if sha256_file(artifact) != binding.get("sha256"):
            raise DataRuntimeError(f"checkpoint artifact SHA-256 已过期: {raw}")
        if raw == expected_path:
            found = True
    if not found:
        raise DataRuntimeError(f"checkpoint 必须绑定当前阶段固定工件: {expected_path}")



def append_checkpoint(root: Path, run_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:
    run_dir, manifest = _load_run(root, run_id)
    if manifest.get("status") in TERMINAL_RUN_STATUSES:
        raise DataRuntimeError("已结束 Run 不可追加检查点")
    validate_runtime_record(manifest, "session-manifest.schema.json")
    checkpoint_dir = run_dir / "checkpoints"
    _ensure_managed_directory(checkpoint_dir, run_dir, "checkpoints 目录")
    checkpoint_count = manifest.get("checkpoint_count")
    if isinstance(checkpoint_count, bool) or not isinstance(checkpoint_count, int) or checkpoint_count < 0:
        raise DataRuntimeError("manifest checkpoint_count 无效")
    files = sorted(checkpoint_dir.iterdir(), key=lambda item: item.name)
    if len(files) != checkpoint_count:
        raise DataRuntimeError("checkpoint 文件数量与 manifest checkpoint_count 不一致")
    for expected, path in enumerate(files, 1):
        _require_regular_file(path, run_dir, "历史 checkpoint")
        match = re.fullmatch(r"(\d{3})-([a-z_]+)\.json", path.name)
        if match is None or int(match.group(1)) != expected:
            raise DataRuntimeError(f"历史 checkpoint 文件名或序列无效: {path.name}")
        existing = read_json(path)
        if not isinstance(existing, dict):
            raise DataRuntimeError(f"历史 checkpoint 不是对象: {path.name}")
        validate_runtime_record(existing, "stage-checkpoint.schema.json")
        _verify_checkpoint_artifacts(run_dir, manifest, existing)
        if existing.get("run_id") != run_id:
            raise DataRuntimeError(f"历史 checkpoint run_id 与当前 Run 不一致: {path.name}")
        if existing.get("sequence") != expected:
            raise DataRuntimeError(f"历史 checkpoint sequence 与文件名不一致: {path.name}")
        if existing.get("stage") != match.group(2):
            raise DataRuntimeError(f"历史 checkpoint stage 与文件名不一致: {path.name}")
    number = checkpoint_count + 1
    if number > 999:
        raise DataRuntimeError("checkpoint 序列超过 999")
    checkpoint = {**checkpoint, "status": checkpoint.get("status", "completed"),
                  "schema_version": "1.0", "run_id": run_id, "sequence": number, "created_at": utc_now()}
    if checkpoint["status"] == "skipped" and not str(checkpoint.get("skip_reason", "")).strip():
        raise DataRuntimeError("skipped 检查点必须提供 skip_reason")
    if checkpoint["status"] == "completed" and checkpoint.get("skip_reason"):
        raise DataRuntimeError("completed 检查点不得提供 skip_reason")
    validate_runtime_record(checkpoint, "stage-checkpoint.schema.json")
    _verify_checkpoint_artifacts(run_dir, manifest, checkpoint)
    _write_json_exclusive(checkpoint_dir / f"{number:03d}-{checkpoint['stage']}.json", checkpoint)
    manifest["checkpoint_count"] = number
    manifest["updated_at"] = checkpoint["created_at"]
    validate_runtime_record(manifest, "session-manifest.schema.json")
    atomic_write_json(run_dir / "manifest.json", manifest)
    return checkpoint


def upsert_risk(root: Path, run_id: str, risk: dict[str, Any]) -> dict[str, Any]:
    run_dir, manifest = _load_run(root, run_id)
    if manifest.get("status") in TERMINAL_RUN_STATUSES:
        raise DataRuntimeError("已结束 Run 不可更新风险账本")
    path = run_dir / "internal" / "risk-ledger.json"
    _require_regular_file(path, run_dir, "风险账本")
    ledger = read_json(path)
    if not isinstance(ledger, dict):
        raise DataRuntimeError("风险账本无效")
    validate_runtime_record(ledger, "risk-ledger.schema.json")
    if ledger.get("run_id") != run_id:
        raise DataRuntimeError("风险账本 run_id 与当前 Run 不一致")
    risks = ledger["risks"]
    existing_ids: set[str] = set()
    for existing in risks:
        validate_runtime_record(existing, "risk-card.schema.json")
        existing_id = existing["risk_id"]
        if existing_id in existing_ids:
            raise DataRuntimeError(f"风险账本存在重复 risk_id: {existing_id}")
        existing_ids.add(existing_id)
    risk_id = risk.get("risk_id")
    if not isinstance(risk_id, str) or not risk_id:
        raise DataRuntimeError("风险卡缺少 risk_id")
    validate_runtime_record(risk, "risk-card.schema.json")
    risks[:] = [item for item in risks if item.get("risk_id") != risk_id]
    risks.append(risk)
    risks.sort(key=lambda item: item["risk_id"])
    ledger["updated_at"] = utc_now()
    validate_runtime_record(ledger, "risk-ledger.schema.json")
    atomic_write_json(path, ledger)
    return risk




def workspace_inventory(root: Path) -> dict[str, Any]:
    workspace = ensure_layout(root)
    workspace_resolved = workspace.resolve(strict=True)
    runs_root = workspace / "runs"
    run_history: list[dict[str, Any]] = []
    legacy_reports: list[dict[str, str]] = []
    for run_dir in sorted(runs_root.iterdir(), key=lambda item: item.name):
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise DataRuntimeError(f"拒绝非目录 Run 项: {run_dir}")
        run_resolved = _require_run_directory(workspace, run_dir, run_dir.name)
        manifest_path = run_dir / "manifest.json"
        _require_regular_file(manifest_path, run_resolved, "Run manifest")
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise DataRuntimeError(f"Run manifest 无效: {run_dir.name}")
        existing = []
        for name in ("checkpoints", "evidence", "internal", "tmp"):
            candidate = run_dir / name
            if candidate.exists() or candidate.is_symlink():
                _require_managed_directory(candidate, run_resolved, f"Run 工件目录 {name}")
                existing.append(str(candidate))
        final = run_dir / "final"
        legacy_md, legacy_html = final / "report.md", final / "report.html"
        if legacy_md.is_file() and legacy_html.is_file():
            legacy_reports.append({"run_id": run_dir.name, "report_md": str(legacy_md),
                                   "report_html": str(legacy_html), "kind": "legacy_run_final"})
        run_history.append({
            "run_id": manifest.get("run_id", run_dir.name),
            "status": manifest.get("status", "unknown"),
            "machine_state": manifest.get("machine_state", "unknown"),
            "updated_at": manifest.get("updated_at"),
            "record_dir": str(run_dir),
            "intermediate_dirs": existing,
            "deliverables": manifest.get("deliverables"),
        })

    reports_root = workspace / "reports"
    formal_reports: list[dict[str, Any]] = []
    if reports_root.exists() or reports_root.is_symlink():
        reports_resolved = _require_managed_directory(reports_root, workspace_resolved, "reports 目录")
        for report_dir in sorted(reports_root.iterdir(), key=lambda item: item.name):
            if report_dir.is_symlink() or not report_dir.is_dir():
                raise DataRuntimeError(f"拒绝非目录报告项: {report_dir}")
            report_resolved = _require_managed_directory(report_dir, reports_resolved, "正式报告目录")
            md, page = report_dir / "report.md", report_dir / "report.html"
            complete = md.is_file() and page.is_file() and md.stat().st_size > 0 and page.stat().st_size > 0
            if md.exists() and md.is_file():
                _require_regular_file(md, report_resolved, "Markdown 正式报告")
            if page.exists() and page.is_file():
                _require_regular_file(page, report_resolved, "HTML 正式报告")
            formal_reports.append({"run_id": report_dir.name, "complete": complete,
                                   "report_md": str(md) if md.is_file() else None,
                                   "report_html": str(page) if page.is_file() else None})

    contracts_root = workspace / "contracts"
    contracts: list[dict[str, Any]] = []
    if contracts_root.exists() or contracts_root.is_symlink():
        contracts_resolved = _require_managed_directory(contracts_root, workspace_resolved, "contracts 目录")
        for contract_dir in sorted(contracts_root.iterdir(), key=lambda item: item.name):
            if contract_dir.is_symlink() or not contract_dir.is_dir():
                raise DataRuntimeError(f"拒绝非目录任务契约项: {contract_dir}")
            resolved = _require_managed_directory(contract_dir, contracts_resolved, "任务契约目录")
            record_path = contract_dir / "contract.json"
            _require_regular_file(record_path, resolved, "任务契约记录")
            record = read_json(record_path)
            if not isinstance(record, dict):
                raise DataRuntimeError(f"任务契约记录无效: {contract_dir.name}")
            contracts.append({"contract_id": record.get("contract_id", contract_dir.name),
                              "status": record.get("status", "unknown"),
                              "target": record.get("task_contract", {}).get("target"),
                              "record": str(record_path), "activation": record.get("activation")})

    return {
        "locations": {
            "documents_inbox": str(workspace / "inbox"),
            "document_library": str(workspace / "library"),
            "repositories": str(workspace / "repositories"),
            "indexes": str(workspace / "indexes"),
            "run_history": str(workspace / "runs"),
            "formal_reports": str(workspace / "reports"),
            "task_contracts": str(workspace / "contracts"),
        },
        "formal_reports": formal_reports,
        "run_history": run_history,
        "legacy_reports": legacy_reports,
        "task_contracts": contracts,
    }


def session_prepare(root: Path, stale_hours: int = 24) -> dict[str, Any]:
    project_root = Path(root).expanduser().resolve()
    workspace = ensure_layout(project_root)
    repository_root = workspace / "repositories"
    step_errors: dict[str, dict[str, str]] = {}

    def capture(name: str, action: Any, fallback: Any) -> Any:
        try:
            return action()
        except (DataRuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
            step_errors[name] = {"type": type(exc).__name__, "message": str(exc) or f"{name} 失败"}
            return fallback

    inbox = capture("inbox", lambda: scan_inbox(project_root), {"added": 0, "changed": 0, "count": 0})
    document_import = capture(
        "document_import", lambda: convert_catalog(project_root),
        {"catalog": None, "converted": 0, "reused": 0, "pending": 0, "skipped": 0, "count": 0},
    )
    repositories = capture("repositories", lambda: safe_pull_repositories(project_root), [])
    unfinished = capture("incomplete_runs", lambda: incomplete_runs(project_root), [])
    cleanup = capture("tmp_cleanup", lambda: cleanup_stale_tmp(project_root, stale_hours), {"removed": [], "stale_hours": stale_hours})
    inventory = capture(
        "workspace_inventory", lambda: workspace_inventory(project_root),
        {"locations": {"repositories": str(repository_root), "run_history": str(workspace / "runs"),
                       "formal_reports": str(workspace / "reports")},
         "formal_reports": [], "run_history": [], "legacy_reports": []},
    )
    known_repositories = sorted(path.name for path in repository_root.iterdir()
                                if path.is_dir() and not path.is_symlink())
    return {
        "current_workspace": str(Path.cwd().resolve()),
        "project_root": str(project_root),
        "data_root": str(workspace),
        "repository_root": str(repository_root),
        "known_repositories": known_repositories,
        "inbox": inbox,
        "document_import": document_import,
        "repositories": repositories,
        "incomplete_runs": unfinished,
        "tmp_cleanup": cleanup,
        "workspace_inventory": inventory,
        "step_errors": step_errors,
    }
