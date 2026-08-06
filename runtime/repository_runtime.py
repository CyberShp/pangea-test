#!/usr/bin/env python3
"""Read-only Git archive snapshots for PANGEA analysis runs.

Snapshots deliberately use ``git archive`` rather than checkout, worktree, or
any other operation that can mutate a supplied repository.  They are analysis
inputs only and always live below a Run's ``tmp`` directory.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from runtime import data_runtime
from runtime.process_runtime import run_text


class RepositoryRuntimeError(RuntimeError):
    pass


GIT_TIMEOUT_SECONDS = 60
MANIFEST_NAME = "snapshot-manifest.json"
GITLINK_GAP_KIND = "gitlink"
GITLINK_GAP_DETAIL = "Git archive 不包含 gitlink；需提供关联仓的精确 commit 快照"


def _snapshot_content_sha256(snapshot_dir: Path) -> str:
    """Hash archived content without trusting its adjacent manifest.

    The encoding is deliberately unambiguous and independent of permissions or
    timestamps: every relative path, entry type, regular-file content, and
    symbolic-link target participates.  The manifest is excluded because it
    stores this digest itself.
    """
    digest = hashlib.sha256()
    entries = sorted(snapshot_dir.rglob("*"), key=lambda path: path.relative_to(snapshot_dir).as_posix())
    for entry in entries:
        relative = entry.relative_to(snapshot_dir).as_posix()
        if relative == MANIFEST_NAME:
            continue
        encoded_path = relative.encode("utf-8", "surrogateescape")
        if entry.is_symlink():
            target = os.readlink(entry).encode("utf-8", "surrogateescape")
            digest.update(b"L\0" + encoded_path + b"\0" + target + b"\0")
        elif entry.is_dir():
            digest.update(b"D\0" + encoded_path + b"\0")
        elif entry.is_file():
            digest.update(b"F\0" + encoded_path + b"\0")
            with entry.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
        else:
            raise RepositoryRuntimeError(f"快照包含不支持的条目: {relative}")
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _under(path: Path, parent: Path, label: str) -> Path:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise RepositoryRuntimeError(f"拒绝访问 {label} 外路径: {path}") from exc
    return path.resolve()


def _safe_name(value: str, label: str) -> str:
    candidate = Path(value)
    if not value or candidate.name != value or value in {".", ".."}:
        raise RepositoryRuntimeError(f"{label} 非法")
    return value


def _git(repo: Path, *args: str, binary: bool = False) -> subprocess.CompletedProcess[Any]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    command = ["git", "-C", str(repo), *args]
    try:
        if binary:
            return subprocess.run(
                command, capture_output=True, text=False, check=False,
                timeout=GIT_TIMEOUT_SECONDS, env=env,
            )
        return run_text(command, timeout=GIT_TIMEOUT_SECONDS, env=env)
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(exc.cmd, 124, b"" if binary else "", "git 命令超时")


def _failure(result: subprocess.CompletedProcess[Any]) -> str:
    stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
    stdout = result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else result.stdout
    return (stderr or stdout or "git 命令失败").strip()


def _repository(root: Path, repository: str) -> tuple[Path, Path]:
    workspace = data_runtime.ensure_layout(root)
    repositories = workspace / "repositories"
    # Repository names are intentionally a single directory.  This prevents a
    # caller from using snapshots to read arbitrary local Git repositories.
    name = _safe_name(repository, "repository")
    raw_repo = repositories / name
    # ``resolve`` is intentionally not used until after this check.  An alias
    # within repositories is still an alias and must never gain Git access.
    if raw_repo.is_symlink():
        raise RepositoryRuntimeError(f"拒绝符号链接仓库目录: {repository}")
    repo = _under(raw_repo, repositories, "repositories")
    if not repo.is_dir():
        raise RepositoryRuntimeError(f"代码仓不存在: {repository}")
    inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    if inside.returncode or (inside.stdout or "").strip() != "true":
        raise RepositoryRuntimeError(f"不是 Git 工作树: {repository}")
    # ``--is-inside-work-tree`` alone also accepts a plain directory below a
    # parent Git repository.  The registered directory itself must be the
    # worktree root; this also works for Git worktrees whose .git is a file.
    top_level = _git(repo, "rev-parse", "--show-toplevel")
    top_level_output = (top_level.stdout or "").strip()
    if top_level.returncode or not top_level_output:
        raise RepositoryRuntimeError(f"无法确定 Git 工作树根目录: {repository}")
    try:
        top_level_path = Path(top_level_output).resolve()
    except OSError as exc:
        raise RepositoryRuntimeError(f"无法解析 Git 工作树根目录: {repository}") from exc
    if top_level_path != repo.resolve():
        raise RepositoryRuntimeError(f"登记目录不是独立 Git 工作树根目录: {repository}")
    return workspace, repo


def _run_tmp(root: Path, run_id: str) -> Path:
    """Return the physical tmp directory of the manifest-bound Run."""
    try:
        run_dir, _ = data_runtime._load_run(root, run_id)
    except data_runtime.DataRuntimeError as exc:
        raise RepositoryRuntimeError(str(exc)) from exc
    return run_dir / "tmp"


def _archive_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or path.parts[:1] == ("",):
        raise RepositoryRuntimeError(f"归档包含危险路径: {name!r}")
    return path


def _safe_extract(archive: Path, destination: Path) -> int:
    count = 0
    root = destination.resolve()
    with tarfile.open(archive, "r:") as tar:
        for member in tar.getmembers():
            relative = _archive_member_path(member.name)
            target = _under(destination / Path(*relative.parts), root, "快照")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.isreg():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tar.extractfile(member)
                if source is None:
                    raise RepositoryRuntimeError(f"无法读取归档成员: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                count += 1
                continue
            if member.issym():
                link = PurePosixPath(member.linkname)
                # Symlinks are allowed only when their resolved destination is
                # inside this archive, and never when absolute or traversing.
                if link.is_absolute() or ".." in link.parts:
                    raise RepositoryRuntimeError(f"归档包含危险符号链接: {member.name}")
                linked = _under(target.parent / Path(*link.parts), root, "快照")
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(member.linkname, target)
                # ``linked`` is computed only to enforce containment above.
                del linked
                count += 1
                continue
            raise RepositoryRuntimeError(f"不支持的归档成员类型: {member.name}")
    return count


def _make_read_only(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if child.is_symlink():
            continue
        child.chmod(0o555 if child.is_dir() else 0o444)
    path.chmod(0o555)


def _make_removable(path: Path) -> None:
    for child in path.rglob("*"):
        if not child.is_symlink():
            try:
                child.chmod(0o755 if child.is_dir() else 0o644)
            except OSError:
                pass
    try:
        path.chmod(0o755)
    except OSError:
        pass


def _gitlink_path(value: str) -> str:
    """Require a canonical, archive-relative POSIX path for a gitlink."""
    try:
        path = PurePosixPath(value)
    except TypeError as exc:
        raise RepositoryRuntimeError("gitlink path 格式无效") from exc
    if not value or path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise RepositoryRuntimeError("gitlink path 格式无效")
    return value


def _gitlink_commit(value: str) -> str:
    if not isinstance(value, str) or len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise RepositoryRuntimeError("gitlink commit_sha 必须是 40 位小写 SHA")
    return value


def _gitlink_gaps(repo: Path, commit: str, repository: str, snapshot_id: str) -> list[dict[str, str]]:
    """Read every gitlink directly from a commit tree, preserving its binding."""
    result = _git(repo, "ls-tree", "-r", "-z", "--full-tree", commit, binary=True)
    if result.returncode:
        raise RepositoryRuntimeError(f"无法读取 commit gitlink: {_failure(result)}")
    gaps: list[dict[str, str]] = []
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_sha = metadata.split(b" ", 2)
            path = raw_path.decode("utf-8")
            gitlink_commit = object_sha.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise RepositoryRuntimeError("gitlink 记录格式无效") from exc
        if mode != b"160000":
            continue
        if object_type != b"commit":
            raise RepositoryRuntimeError("gitlink 对象类型无效")
        gaps.append({
            "kind": GITLINK_GAP_KIND,
            "repository": repository,
            "snapshot_id": snapshot_id,
            "path": _gitlink_path(path),
            "commit_sha": _gitlink_commit(gitlink_commit),
            "detail": GITLINK_GAP_DETAIL,
        })
    return gaps


def create_snapshot(root: Path, run_id: str, repository: str, ref: str = "HEAD", snapshot_id: str | None = None) -> dict[str, Any]:
    """Archive an immutable commit into ``runs/<id>/tmp/snapshots``.

    ``ref`` is resolved to a full object SHA before archiving so a moving branch
    name cannot make the manifest disagree with the extracted contents.
    """
    workspace, repo = _repository(root, repository)
    tmp = _run_tmp(root, run_id)
    resolved = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    commit = (resolved.stdout or "").strip()
    if resolved.returncode or not commit:
        raise RepositoryRuntimeError(f"无法解析 ref {ref!r}: {_failure(resolved)}")
    snapshot_id = _safe_name(snapshot_id or repository, "snapshot_id")
    snapshots = tmp / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    destination = _under(snapshots / snapshot_id, tmp, "Run tmp")
    if destination.exists():
        raise RepositoryRuntimeError(f"快照已存在: {snapshot_id}")

    gaps = _gitlink_gaps(repo, commit, repository, snapshot_id)

    with tempfile.TemporaryDirectory(prefix="pangea-archive-", dir=tmp) as staging:
        archive = Path(staging) / "source.tar"
        exported = _git(repo, "archive", "--format=tar", "--output", str(archive), commit)
        if exported.returncode:
            raise RepositoryRuntimeError(f"git archive 失败: {_failure(exported)}")
        destination.mkdir(parents=True)
        try:
            file_count = _safe_extract(archive, destination)
            manifest = {
                "schema_version": "1.1", "repository": repository, "source_repository": str(repo),
                "requested_ref": ref, "commit_sha": commit, "created_at": _now(),
                "file_count": file_count, "content_sha256": _snapshot_content_sha256(destination),
                "coverage_gaps": gaps,
            }
            (destination / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            _make_read_only(destination)
        except Exception:
            _make_removable(destination)
            shutil.rmtree(destination, ignore_errors=True)
            raise
    return {"snapshot_id": snapshot_id, "snapshot_dir": str(destination), "manifest": manifest}


def create_snapshots(root: Path, run_id: str, repositories: Iterable[dict[str, str]]) -> dict[str, list[dict[str, Any]]]:
    """Create independent snapshots; an unavailable related repository is a gap."""
    snapshots: list[dict[str, Any]] = []
    gaps: list[dict[str, str]] = []
    for spec in repositories:
        name = spec.get("repository", "")
        try:
            snapshots.append(create_snapshot(root, run_id, name, spec.get("ref", "HEAD"), spec.get("snapshot_id")))
        except RepositoryRuntimeError as exc:
            gaps.append({"repository": name, "detail": str(exc)})
    return {"snapshots": snapshots, "coverage_gaps": gaps}


def _manifest_coverage_gaps(value: Any, repository: str, snapshot_id: str) -> list[dict[str, str]]:
    """Validate coverage gaps embedded in a snapshot manifest.

    These gaps are part of the snapshot's evidence.  Treat malformed evidence
    as unavailable rather than silently dropping it and allowing an MR gate to
    continue with incomplete coverage information.
    """
    if not isinstance(value, list):
        raise RepositoryRuntimeError("快照清单 coverage_gaps 格式无效")
    gaps: list[dict[str, str]] = []
    for gap in value:
        if not isinstance(gap, dict) or not isinstance(gap.get("kind"), str) or not gap["kind"]:
            raise RepositoryRuntimeError("快照清单 coverage_gaps 条目格式无效")
        if gap["kind"] != GITLINK_GAP_KIND:
            if set(gap) != {"kind", "detail"} or not isinstance(gap.get("detail"), str) or not gap["detail"]:
                raise RepositoryRuntimeError("快照清单 coverage_gaps 条目格式无效")
            gaps.append({"kind": gap["kind"], "detail": gap["detail"]})
            continue
        if set(gap) != {"kind", "repository", "snapshot_id", "path", "commit_sha", "detail"}:
            raise RepositoryRuntimeError("快照清单 coverage_gaps 条目格式无效")
        if gap["repository"] != repository or gap["snapshot_id"] != snapshot_id or gap["detail"] != GITLINK_GAP_DETAIL:
            raise RepositoryRuntimeError("快照清单 gitlink 归属或说明无效")
        if not isinstance(gap["repository"], str) or not isinstance(gap["snapshot_id"], str) or not isinstance(gap["detail"], str):
            raise RepositoryRuntimeError("快照清单 coverage_gaps 条目格式无效")
        gaps.append({
            "kind": GITLINK_GAP_KIND,
            "repository": repository,
            "snapshot_id": snapshot_id,
            "path": _gitlink_path(gap["path"]),
            "commit_sha": _gitlink_commit(gap["commit_sha"]),
            "detail": GITLINK_GAP_DETAIL,
        })
    return gaps


def cleanup_snapshot(root: Path, run_id: str, snapshot_id: str) -> dict[str, Any]:
    tmp = _run_tmp(root, run_id)
    destination = _under(tmp / "snapshots" / _safe_name(snapshot_id, "snapshot_id"), tmp, "Run tmp")
    if not destination.exists():
        return {"snapshot_id": snapshot_id, "removed": False}
    if not destination.is_dir():
        raise RepositoryRuntimeError(f"快照路径不是目录: {snapshot_id}")
    _make_removable(destination)
    shutil.rmtree(destination)
    return {"snapshot_id": snapshot_id, "removed": True}


def snapshot_status(root: Path, run_id: str) -> dict[str, Any]:
    """Read the current Run's snapshot manifests without consulting source repos.

    A resumed analysis must continue from the archived commit it originally
    inspected.  This function deliberately performs no Git operation: it only
    reports whether each managed snapshot and its commit binding still exist.
    """
    tmp = _run_tmp(root, run_id)
    snapshots = tmp / "snapshots"
    if not snapshots.exists():
        return {"snapshots": [], "coverage_gaps": []}
    if snapshots.is_symlink() or not snapshots.is_dir():
        raise RepositoryRuntimeError("快照目录不是受管目录")

    present: list[dict[str, str]] = []
    gaps: list[dict[str, str]] = []
    for entry in sorted(snapshots.iterdir(), key=lambda item: item.name):
        if entry.is_symlink() or not entry.is_dir():
            gaps.append({"snapshot_id": entry.name, "detail": "快照条目不是受管目录"})
            continue
        manifest_path = entry / MANIFEST_NAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            gaps.append({"snapshot_id": entry.name, "detail": "缺少快照清单"})
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            gaps.append({"snapshot_id": entry.name, "detail": f"快照清单无效: {exc}"})
            continue
        if not isinstance(manifest, dict):
            gaps.append({"snapshot_id": entry.name, "detail": "快照清单无效: 顶层必须是对象"})
            continue
        repository = manifest.get("repository")
        commit = manifest.get("commit_sha")
        expected_digest = manifest.get("content_sha256")
        if not isinstance(repository, str) or not isinstance(commit, str) or not commit or not isinstance(expected_digest, str):
            gaps.append({"snapshot_id": entry.name, "detail": "快照清单缺少 repository、commit_sha 或 content_sha256"})
            continue
        try:
            manifest_gaps = _manifest_coverage_gaps(manifest.get("coverage_gaps"), repository, entry.name)
        except RepositoryRuntimeError as exc:
            gaps.append({"snapshot_id": entry.name, "repository": repository, "detail": str(exc)})
            continue
        try:
            actual_digest = _snapshot_content_sha256(entry)
        except (OSError, RepositoryRuntimeError) as exc:
            gaps.append({"snapshot_id": entry.name, "detail": f"快照内容无法校验: {exc}"})
            continue
        if actual_digest != expected_digest:
            gaps.append({"snapshot_id": entry.name, "detail": "快照内容 SHA-256 不匹配"})
            continue
        present.append({"snapshot_id": entry.name, "repository": repository, "commit_sha": commit,
                        "content_sha256": actual_digest, "snapshot_dir": str(entry.resolve())})
        gaps.extend({"snapshot_id": entry.name, "repository": repository, **gap} for gap in manifest_gaps)
    return {"snapshots": present, "coverage_gaps": gaps}


def verify_snapshots_against_source(root: Path, run_id: str) -> dict[str, Any]:
    """Re-archive every managed snapshot from its registered source commit.

    Snapshot manifests are evidence, not authority.  This gate obtains the
    commit content afresh from the registered repository using ``git archive``
    and compares the canonical extracted-tree digest.  It never checks out,
    resets, or otherwise writes a source worktree.  The verification archive is
    held below the Run's managed tmp directory and removed before returning.
    """
    tmp = _run_tmp(root, run_id)
    status = snapshot_status(root, run_id)
    verified: list[dict[str, str]] = []
    gaps = list(status["coverage_gaps"])
    for binding in status["snapshots"]:
        repository = binding["repository"]
        commit = binding["commit_sha"]
        snapshot_dir = Path(binding["snapshot_dir"])
        try:
            _, repo = _repository(root, repository)
            resolved = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}")
            if resolved.returncode or (resolved.stdout or "").strip() != commit:
                raise RepositoryRuntimeError("登记源仓无法验证快照 commit")
            with tempfile.TemporaryDirectory(prefix="pangea-verify-", dir=tmp) as staging:
                archive = Path(staging) / "source.tar"
                expected_dir = Path(staging) / "expected"
                expected_dir.mkdir()
                exported = _git(repo, "archive", "--format=tar", "--output", str(archive), commit)
                if exported.returncode:
                    raise RepositoryRuntimeError(f"源仓 git archive 失败: {_failure(exported)}")
                _safe_extract(archive, expected_dir)
                expected_digest = _snapshot_content_sha256(expected_dir)
            if expected_digest != binding["content_sha256"]:
                raise RepositoryRuntimeError("快照内容与登记源仓 commit 的归档 SHA-256 不匹配")
            manifest_path = snapshot_dir / MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest_gaps = _manifest_coverage_gaps(manifest.get("coverage_gaps"), repository, binding["snapshot_id"])
            source_gitlinks = _gitlink_gaps(repo, commit, repository, binding["snapshot_id"])
            if manifest_gaps != source_gitlinks:
                raise RepositoryRuntimeError("快照 gitlink 元数据与登记源仓 commit 不匹配")
            verified.append({**binding, "source_content_sha256": expected_digest})
        except (OSError, RepositoryRuntimeError) as exc:
            gaps.append({"snapshot_id": binding.get("snapshot_id", repository), "detail": str(exc)})
    return {"snapshots": verified, "coverage_gaps": gaps}


def cleanup_run_tmp(root: Path, run_id: str) -> dict[str, Any]:
    """Remove only PANGEA-owned temporary snapshot material for one Run.

    ``tmp`` is a private Run directory, but this function still uses an
    explicit allowlist.  It never follows a symlink and never removes an
    unrecognised sibling, so a malformed Run cannot turn finalization into a
    broader filesystem deletion.
    """
    tmp = _run_tmp(root, run_id)
    removed: list[str] = []
    managed = {"snapshots"}
    for entry in sorted(tmp.iterdir(), key=lambda item: item.name):
        if entry.name not in managed and not entry.name.startswith("pangea-archive-"):
            continue
        if entry.is_symlink():
            raise RepositoryRuntimeError(f"拒绝清理符号链接临时项: {entry.name}")
        _under(entry, tmp, "Run tmp")
        if entry.is_dir():
            _make_removable(entry)
            shutil.rmtree(entry)
        elif entry.is_file():
            entry.unlink()
        else:
            raise RepositoryRuntimeError(f"临时项类型不受支持: {entry.name}")
        removed.append(entry.name)
    return {"run_id": run_id, "removed": removed, "tmp": str(tmp)}
