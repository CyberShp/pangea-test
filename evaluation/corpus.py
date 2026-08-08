"""Evaluator-owned construction of an isolated candidate public corpus.

Repository payloads are materialized directly from the pinned commit's object
tree.  This deliberately does not use ``git archive``: archive output can be
changed by clone-local ``.git/info/attributes`` even when the commit and tree
are identical.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from pathlib import PurePosixPath
from typing import Mapping, Any
import json
import os
import shutil
import stat
import subprocess
import posixpath
import re

from .benchmark import (
    BenchmarkContractError,
    contains_secret_assignment,
    load_corpus_manifest,
    load_frozen_config,
    _opencode_project_plugin_entries,
    _validate_stage_receipt,
    validate_public_bundle,
    write_public_bundle_manifest,
)
from benchmarks import stage as public_stage


ALLOWED_DIRECTORIES = {".opencode", "core", "runtime", "schemas", "registry", "tooling"}
ALLOWED_TOP_FILES = {"README.md", "LICENSE", "pyproject.toml"}
ALLOWED_OPENCODE_CHILDREN = {"agents", "commands", "skills"}
EXCLUDED_PARTS = {
    ".git", ".codebuddy", "pangea-data", "codetalks-data", "benchmarks", "evaluation", "tests",
    "__pycache__", ".pytest_cache", "node_modules", "cache", ".cache", "build", "dist",
}
PUBLIC_CASE_RELATIVE = "CASE.json"
SECRET_FILE_NAMES = {
    "credentials", "credentials.json", "secrets.json", ".npmrc", ".pypirc",
    "id_rsa", "id_ed25519",
}


def _run_git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(root), *args], check=False, capture_output=True)
    except OSError as exc:
        raise BenchmarkContractError(f"git invocation failed: {exc}") from exc
    if result.returncode:
        raise BenchmarkContractError(result.stderr.decode("utf-8", "replace").strip() or "git command failed")
    return result.stdout


def _require_regular_directory(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise BenchmarkContractError(f"{label} must be a regular directory")


def _commit_entries(root: Path, commit: str) -> list[tuple[str, str, str, str]]:
    """Return canonical ``(path, mode, type, object)`` leaf entries."""
    raw = _run_git(root, "ls-tree", "-rz", "-r", "--full-tree", commit)
    entries: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode_bytes, type_bytes, object_bytes = metadata.split(b" ", 2)
            path = raw_path.decode("utf-8", "strict")
            mode = mode_bytes.decode("ascii", "strict")
            kind = type_bytes.decode("ascii", "strict")
            object_id = object_bytes.decode("ascii", "strict")
        except (ValueError, UnicodeDecodeError) as exc:
            raise BenchmarkContractError("git tree contains an undecodable entry") from exc
        pure = PurePosixPath(path)
        if not path or pure.is_absolute() or ".." in pure.parts or path in seen:
            raise BenchmarkContractError(f"unsafe or duplicate git tree path: {path!r}")
        if (mode, kind) not in {
            ("100644", "blob"), ("100755", "blob"),
            ("120000", "blob"), ("160000", "commit"),
        }:
            raise BenchmarkContractError(f"unsupported git tree entry {mode} {kind}: {path}")
        if len(object_id) not in {40, 64} or any(character not in "0123456789abcdef" for character in object_id.lower()):
            raise BenchmarkContractError(f"invalid object id for git tree entry: {path}")
        seen.add(path)
        entries.append((path, mode, kind, object_id.lower()))
    return entries


class _BlobReader:
    """Small streaming wrapper around one read-only ``git cat-file`` process."""

    def __init__(self, root: Path) -> None:
        try:
            self._process = subprocess.Popen(
                ["git", "-C", str(root), "cat-file", "--batch"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise BenchmarkContractError(f"git invocation failed: {exc}") from exc

    def read(self, object_id: str) -> bytes:
        process = self._process
        if process.stdin is None or process.stdout is None:
            raise BenchmarkContractError("git cat-file pipes are unavailable")
        try:
            process.stdin.write(object_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline()
            returned, kind, size_text = header.rstrip(b"\n").split(b" ", 2)
            size = int(size_text)
            data = process.stdout.read(size)
            terminator = process.stdout.read(1)
        except (BrokenPipeError, OSError, ValueError) as exc:
            raise BenchmarkContractError(f"cannot read git blob {object_id}") from exc
        if returned.decode("ascii", "replace").lower() != object_id.lower() or kind != b"blob" or len(data) != size or terminator != b"\n":
            raise BenchmarkContractError(f"invalid git blob response for {object_id}")
        return data

    def close(self) -> None:
        process = self._process
        if process.stdin is not None and not process.stdin.closed:
            process.stdin.close()
        return_code = process.wait()
        stderr = process.stderr.read().decode("utf-8", "replace").strip() if process.stderr is not None else ""
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        if return_code:
            raise BenchmarkContractError(stderr or "git cat-file failed")

    def abort(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
        self._process.wait()
        for pipe in (self._process.stdin, self._process.stdout, self._process.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


def _blob(reader: _BlobReader, cache: dict[str, bytes], object_id: str) -> bytes:
    if object_id not in cache:
        cache[object_id] = reader.read(object_id)
    return cache[object_id]


def _safe_link_target(link_path: str, link: str) -> str:
    if not link or link.startswith("/") or "\\" in link or re.match(r"^[A-Za-z]:", link):
        raise BenchmarkContractError(f"unsafe git symlink target: {link_path}")
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(link_path), link))
    if normalized == ".." or normalized.startswith("../") or normalized.startswith("/"):
        raise BenchmarkContractError(f"unsafe git symlink target: {link_path}")
    return normalized


def _resolve_symlink(
    link_path: str,
    link_data: bytes,
    entries: Mapping[str, tuple[str, str, str]],
    reader: _BlobReader,
    cache: dict[str, bytes],
) -> tuple[bytes, dict[str, Any], bool]:
    """Resolve an in-tree file link without touching the checkout filesystem.

    Directory, dangling, and gitlink targets cannot yield an ordinary blob from
    the superproject.  Those cases receive an explicit canonical descriptor;
    exact regular-file targets receive their actual content.
    """
    chain: list[dict[str, str]] = []
    seen = {link_path}
    current_path = link_path
    current_data = link_data
    for _ in range(64):
        try:
            link = current_data.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise BenchmarkContractError(f"git symlink target is not UTF-8: {current_path}") from exc
        target_path = _safe_link_target(current_path, link)
        chain.append({"path": current_path, "target": link, "resolved_path": target_path})
        entry = entries.get(target_path)
        if entry is not None:
            mode, kind, object_id = entry
            if mode == "120000":
                if target_path in seen:
                    raise BenchmarkContractError(f"cyclic git symlink: {link_path}")
                seen.add(target_path)
                current_path = target_path
                current_data = _blob(reader, cache, object_id)
                continue
            if mode == "160000":
                data = f"gitlink {object_id}\n".encode("ascii")
                return data, {"resolution": "gitlink", "final_path": target_path, "chain": chain}, False
            data = _blob(reader, cache, object_id)
            return data, {
                "resolution": "blob", "final_path": target_path,
                "final_object": object_id, "final_mode": mode, "chain": chain,
            }, mode == "100755"
        gitlink_prefix = next((
            (path, value) for path, value in entries.items()
            if value[0] == "160000" and target_path.startswith(path + "/")
        ), None)
        if gitlink_prefix is not None:
            prefix, (_, _, object_id) = gitlink_prefix
            suffix = target_path[len(prefix) + 1 :]
            data = f"gitlink {object_id} path {suffix}\n".encode("utf-8")
            return data, {"resolution": "gitlink-path", "final_path": target_path, "chain": chain}, False
        descendants = sorted(
            (path, mode, kind, object_id) for path, (mode, kind, object_id) in entries.items()
            if path.startswith(target_path + "/")
        )
        if descendants:
            tree_identity = "".join(f"{path}\0{mode}\0{kind}\0{object_id}\n" for path, mode, kind, object_id in descendants)
            tree_digest = sha256(tree_identity.encode("utf-8")).hexdigest()
            data = f"directory {target_path} tree {tree_digest}\n".encode("utf-8")
            return data, {"resolution": "directory", "final_path": target_path, "tree_sha256": tree_digest, "chain": chain}, False
        data = f"dangling-symlink {target_path}\n".encode("utf-8")
        return data, {"resolution": "dangling", "final_path": target_path, "chain": chain}, False
    raise BenchmarkContractError(f"git symlink chain is too deep: {link_path}")


def _materialize_commit(root: Path, commit: str, destination: Path) -> dict[str, Any]:
    """Materialize a commit without consulting any attribute configuration."""
    entries = _commit_entries(root, commit)
    identity_lines: list[str] = []
    symlinks: list[dict[str, Any]] = []
    gitlinks: list[dict[str, str]] = []
    executables: list[dict[str, str]] = []
    counts = {"regular": 0, "executable": 0, "symlink": 0, "gitlink": 0}
    entries_by_path = {path: (mode, kind, object_id) for path, mode, kind, object_id in entries}
    blob_cache: dict[str, bytes] = {}
    reader = _BlobReader(root)
    try:
        for path, mode, kind, object_id in entries:
            target = destination.joinpath(*PurePosixPath(path).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if mode == "160000":
                # A gitlink has no blob in the superproject.  Preserve it as a
                # deterministic ordinary descriptor instead of silently
                # dropping it or reading an unrelated submodule worktree.
                data = f"gitlink {object_id}\n".encode("ascii")
                target.write_bytes(data)
                digest = sha256(data).hexdigest()
                counts["gitlink"] += 1
                gitlinks.append({"path": path, "commit": object_id, "materialized_sha256": digest})
            else:
                if mode == "120000":
                    source_data = _blob(reader, blob_cache, object_id)
                    data, resolution, target_executable = _resolve_symlink(
                        path, source_data, entries_by_path, reader, blob_cache,
                    )
                    digest = sha256(data).hexdigest()
                    target.write_bytes(data)
                    if target_executable:
                        os.chmod(target, 0o755)
                    counts["symlink"] += 1
                    symlinks.append({
                        "path": path,
                        "link_blob_sha256": sha256(source_data).hexdigest(),
                        "materialized_sha256": digest,
                        **resolution,
                    })
                else:
                    data = reader.read(object_id)
                    digest = sha256(data).hexdigest()
                    target.write_bytes(data)
                    if mode == "100755":
                        os.chmod(target, 0o755)
                        counts["executable"] += 1
                        executables.append({"path": path, "blob_sha256": digest})
                    else:
                        counts["regular"] += 1
            identity_lines.append(f"{path}\0{mode}\0{kind}\0{object_id}\0{digest}\n")
        reader.close()
    except Exception:
        reader.abort()
        raise
    return {
        "materialization_version": "git-object-v1",
        "materialization_sha256": sha256("".join(identity_lines).encode("utf-8")).hexdigest(),
        "entry_count": len(entries),
        "entry_counts": counts,
        "materialized_symlinks": symlinks,
        "materialized_gitlinks": gitlinks,
        "executable_files": executables,
    }


def _canonical_origin(value: str) -> str:
    value = value.strip()
    if value.startswith("git@") and ":" in value:
        value = "https://" + value[4:].replace(":", "/", 1)
    if value.startswith("ssh://git@"):
        value = "https://" + value[len("ssh://git@") :].replace(":", "/", 1)
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value.lower()


def _is_secret_name(path: Path) -> bool:
    name = path.name.lower()
    return name in SECRET_FILE_NAMES or name.startswith(".env") or path.suffix.lower() in {".pem", ".key"}


def _copy_candidate_tree(
    source: Path, destination: Path, *, candidate: str = "pangea",
) -> tuple[dict[str, str], list[str], str]:
    copied: dict[str, str] = {}
    directories: set[str] = set()
    identity: list[str] = []
    _require_regular_directory(source, "candidate root")
    plugin_entries = _opencode_project_plugin_entries(source)
    if plugin_entries:
        raise BenchmarkContractError(
            "candidate contains OpenCode project plugin entries: " + ", ".join(plugin_entries)
        )
    for child in source.iterdir():
        if child.name in EXCLUDED_PARTS or child.name.startswith(".env"):
            continue
        child_mode = child.lstat().st_mode
        if not (stat.S_ISREG(child_mode) or stat.S_ISDIR(child_mode)):
            kind = "symlink" if stat.S_ISLNK(child_mode) else "special file"
            raise BenchmarkContractError(f"unsafe candidate entry ({kind}): {child.name}")
        # The evaluator-authored CodeTalks materialization manifest is public
        # only for the Fuse candidate.  Keeping it out of the general top-file
        # allowlist prevents a PANGEA candidate from acquiring that binding.
        permitted = (
            child.name in ALLOWED_DIRECTORIES
            or child.name in ALLOWED_TOP_FILES
            or (candidate == "fuse" and child.name == "codetalks-evaluator-manifest.json")
        )
        if not permitted:
            continue
        if stat.S_ISREG(child_mode) and child.stat().st_nlink > 1:
            raise BenchmarkContractError(f"candidate hardlink is forbidden: {child.name}")
        if _is_secret_name(child):
            raise BenchmarkContractError(f"candidate secret-like file is forbidden: {child.name}")
        entries = [child] if child.is_file() else list(child.rglob("*"))
        if child.is_dir():
            (destination / child.name).mkdir(parents=True, exist_ok=True)
            directories.add(child.name)
        for item in entries:
            relative = item.relative_to(source)
            if relative.parts[0] == ".opencode" and (len(relative.parts) < 2 or relative.parts[1] not in ALLOWED_OPENCODE_CHILDREN):
                continue
            if any(part in EXCLUDED_PARTS or part.startswith(".env") for part in relative.parts):
                continue
            if item.is_symlink():
                raise BenchmarkContractError(f"candidate allowlist contains symlink: {relative}")
            mode = item.lstat().st_mode
            if item.is_dir():
                if not stat.S_ISDIR(mode):
                    raise BenchmarkContractError(f"unsafe candidate entry: {relative}")
                (destination / relative).mkdir(parents=True, exist_ok=True)
                continue
            if not stat.S_ISREG(mode):
                raise BenchmarkContractError(f"unsafe candidate entry: {relative}")
            if item.stat().st_nlink > 1:
                raise BenchmarkContractError(f"candidate hardlink is forbidden: {relative}")
            if _is_secret_name(item):
                raise BenchmarkContractError(f"candidate secret-like file is forbidden: {relative}")
            try:
                item.resolve().relative_to(source.resolve())
            except ValueError as exc:
                raise BenchmarkContractError(f"candidate entry escapes root: {relative}") from exc
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item, target)
            if mode & 0o111:
                os.chmod(target, 0o755)
            copied[relative.as_posix()] = sha256(target.read_bytes()).hexdigest()
        for item in entries:
            relative = item.relative_to(source)
            if relative.parts[0] == ".opencode" and (len(relative.parts) < 2 or relative.parts[1] not in ALLOWED_OPENCODE_CHILDREN):
                continue
            if any(part in EXCLUDED_PARTS or part.startswith(".env") for part in relative.parts):
                continue
            if item.is_dir() and not item.is_symlink():
                directories.add(relative.as_posix())
    copied = dict(sorted(copied.items()))
    for directory in sorted(directories):
        identity.append(f"D\0{directory}\n")
    for path, digest in copied.items():
        executable = bool((destination / path).stat().st_mode & 0o111)
        identity.append(f"{'X' if executable else 'F'}\0{path}\0{digest}\n")
    return copied, sorted(directories), sha256("".join(sorted(identity)).encode("utf-8")).hexdigest()


def _validate_candidate_payload(destination: Path, files: Mapping[str, str], forbidden_roots: list[Path]) -> None:
    forbidden = [str(root.resolve()).encode("utf-8") for root in forbidden_roots]
    for relative in files:
        data = (destination / relative).read_bytes()
        if contains_secret_assignment(data):
            raise BenchmarkContractError(f"candidate text contains a secret marker: {relative}")
        if any(root_bytes and root_bytes in data for root_bytes in forbidden):
            raise BenchmarkContractError(f"candidate text exposes an absolute source root: {relative}")


def _make_read_only(root: Path, writable_root: Path) -> None:
    writable_root.mkdir(parents=True, exist_ok=True)
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if path == writable_root or writable_root in path.parents:
            continue
        executable = bool(path.lstat().st_mode & 0o111)
        os.chmod(path, 0o555 if path.is_dir() or executable else 0o444)
    os.chmod(root, 0o555)
    # The managed root itself must remain traversable *and writable*: a 0555
    # parent made it impossible for the product to create a fresh artifact.
    os.chmod(writable_root, 0o755)


TASK_BINDING_VERSION = "canonical-agent-input-line-v1"


def _task_binds_agent_input(task_text: str, agent_input: str) -> bool:
    """Allow evaluator framing while retaining one exact canonical input line."""
    return (
        isinstance(task_text, str)
        and bool(task_text.strip())
        and "\x00" not in task_text
        and "\r" not in task_text
        and task_text.splitlines().count(agent_input) == 1
    )


def _canonical_public_case(case: Mapping[str, Any], task_text: str) -> tuple[dict[str, Any], bytes]:
    """Accept only an exact frozen case and an explicitly bound task wrapper."""
    if not isinstance(case, dict) or not isinstance(case.get("id"), str):
        raise BenchmarkContractError("canonical public case is required")
    try:
        manifest = public_stage.load_manifest()
        errors = public_stage._validate_manifest_snapshot(manifest)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise BenchmarkContractError("canonical public case manifest is unavailable") from exc
    if errors:
        raise BenchmarkContractError("canonical public case manifest is invalid")
    canonical = next((item for item in manifest["cases"] if item["id"] == case["id"]), None)
    if canonical is None or case != canonical:
        raise BenchmarkContractError("public case is not the exact frozen canonical case")
    if not _task_binds_agent_input(task_text, canonical["agent_input"]):
        raise BenchmarkContractError("task text does not bind exactly one canonical agent_input line")
    payload = public_stage.canonical_case_payload(canonical)
    return canonical, payload


def stage_public_corpus(
    destination: Path, candidate_root: Path, task_text: str, repositories: Mapping[str, Path], case: Mapping[str, Any], *,
    candidate: str = "pangea", candidate_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Build a read-only candidate bundle from fixed commits and public allowlists."""
    if destination.exists():
        _require_regular_directory(destination, "destination")
        if any(destination.iterdir()):
            raise BenchmarkContractError("destination must not exist or be an empty regular directory")
    if set(repositories) != {"spdk", "nvme-cli"}:
        raise BenchmarkContractError("repositories must be exactly spdk and nvme-cli")
    canonical_case, case_payload = _canonical_public_case(case, task_text)
    if candidate not in load_frozen_config()["candidates"]:
        raise BenchmarkContractError("unknown frozen candidate")
    if candidate == "fuse":
        if not isinstance(candidate_manifest_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_manifest_sha256):
            raise BenchmarkContractError("Fuse requires an evaluator-owned candidate manifest SHA-256")
    elif candidate_manifest_sha256 is not None:
        raise BenchmarkContractError("PANGEA must not bind a CodeTalks candidate manifest")
    managed_root_name = "pangea-data" if candidate == "pangea" else "codetalks-data"
    manifest = load_corpus_manifest()
    frozen = {row["id"]: row["repository"] for row in load_frozen_config()["targets"]}
    manifest_urls = {row["id"]: _canonical_origin(str(row.get("url", ""))) for row in manifest["repositories"]}
    frozen_urls = {repo_id: _canonical_origin(url) for repo_id, url in frozen.items()}
    if manifest_urls != frozen_urls:
        raise BenchmarkContractError("corpus manifest repository URLs differ from frozen targets")
    expected = {row["id"]: row["commit"] for row in manifest["repositories"]}
    expected_trees = {row["id"]: row["tree"] for row in manifest["repositories"]}
    if set(expected) != {"spdk", "nvme-cli"}:
        raise BenchmarkContractError("corpus manifest must pin exactly spdk and nvme-cli")
    case_repo = canonical_case["repository_id"]
    if (canonical_case["repository_url"], canonical_case["frozen_commit"]) != (frozen[case_repo], expected[case_repo]):
        raise BenchmarkContractError("canonical public case repository binding differs from frozen target")
    created = not destination.exists()
    try:
        destination.mkdir(parents=True, exist_ok=False) if created else None
        _require_regular_directory(candidate_root, "candidate root")
        repo_receipts: list[dict[str, Any]] = []
        for repo_id in ("spdk", "nvme-cli"):
            root = repositories[repo_id]
            _require_regular_directory(root, f"{repo_id} repository")
            try:
                origin = _run_git(root, "remote", "get-url", "origin").decode().strip()
            except BenchmarkContractError as exc:
                raise BenchmarkContractError(f"{repo_id} origin is unavailable") from exc
            if _canonical_origin(origin) != _canonical_origin(frozen[repo_id]):
                raise BenchmarkContractError(f"{repo_id} origin does not match frozen repository")
            commit = expected[repo_id]
            try:
                resolved = _run_git(root, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
            except BenchmarkContractError as exc:
                raise BenchmarkContractError(f"{repo_id} frozen commit is unavailable") from exc
            if resolved != commit:
                raise BenchmarkContractError(f"{repo_id} resolved commit differs from frozen commit")
            git_tree = _run_git(root, "rev-parse", "--verify", f"{commit}^{{tree}}").decode().strip()
            if len(git_tree) != 40:
                raise BenchmarkContractError(f"{repo_id} returned invalid git tree")
            if git_tree != expected_trees[repo_id]:
                raise BenchmarkContractError(f"{repo_id} resolved tree differs from frozen tree")
            target = destination / "repositories" / repo_id
            target.mkdir(parents=True, exist_ok=True)
            materialized = _materialize_commit(root, commit, target)
            repo_receipts.append({"id": repo_id, "commit": commit, "git_tree": git_tree, **materialized})
        task = destination / "TASK.md"
        task.write_text(task_text, encoding="utf-8")
        (destination / PUBLIC_CASE_RELATIVE).write_bytes(case_payload)
        candidate_hashes, candidate_directories, candidate_tree_sha256 = _copy_candidate_tree(
            candidate_root, destination, candidate=candidate,
        )
        if candidate == "fuse":
            copied_manifest_sha256 = candidate_hashes.get("codetalks-evaluator-manifest.json")
            if copied_manifest_sha256 != candidate_manifest_sha256:
                raise BenchmarkContractError("Fuse candidate evaluator manifest differs from evaluator materialization receipt")
        visible_text_files = dict(candidate_hashes)
        visible_text_files["TASK.md"] = sha256(task_text.encode()).hexdigest()
        _validate_candidate_payload(destination, visible_text_files, [candidate_root, *repositories.values()])
        receipt = {
            "schema_version": "1.0", "repositories": repo_receipts,
            "candidate": candidate,
            "candidate_files": candidate_hashes, "candidate_directories": candidate_directories,
            "candidate_tree_sha256": candidate_tree_sha256,
            "candidate_manifest_sha256": candidate_manifest_sha256 if candidate == "fuse" else None,
            "task_hash": sha256(task_text.encode()).hexdigest(),
            "task_binding_version": TASK_BINDING_VERSION,
            "agent_input_sha256": sha256(canonical_case["agent_input"].encode()).hexdigest(),
            "case_path": PUBLIC_CASE_RELATIVE,
            "case_id": canonical_case["id"],
            "case_sha256": sha256(case_payload).hexdigest(),
            "contract_projection_sha256": sha256(
                json.dumps(canonical_case["contract"], ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        expected_receipt = {
            "candidate": candidate, "task_hash": sha256(task_text.encode()).hexdigest(),
            "task_binding_version": TASK_BINDING_VERSION,
            "agent_input_sha256": sha256(canonical_case["agent_input"].encode()).hexdigest(),
            "case_path": PUBLIC_CASE_RELATIVE, "case_id": canonical_case["id"],
            "case_sha256": sha256(case_payload).hexdigest(),
            "contract_projection_sha256": receipt["contract_projection_sha256"],
        }
        _validate_stage_receipt(
            receipt, root=destination, expected=expected_receipt,
            candidate_manifest_sha256=candidate_manifest_sha256,
        )
        receipt_path = destination / "stage-receipt.json"
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
        _validate_stage_receipt(
            persisted, root=destination, expected=expected_receipt,
            candidate_manifest_sha256=candidate_manifest_sha256,
        )
        writable_root = destination / managed_root_name
        writable_root.mkdir(parents=True, exist_ok=True)
        if candidate == "pangea":
            (writable_root / ".evaluator-scratch").mkdir(parents=True, exist_ok=True)
        write_public_bundle_manifest(destination)
        errors = validate_public_bundle(destination)
        if errors:
            raise BenchmarkContractError("staged bundle failed validation: " + "; ".join(errors))
        _make_read_only(destination, writable_root)
        errors = validate_public_bundle(destination)
        if errors:
            raise BenchmarkContractError("read-only staged bundle failed validation: " + "; ".join(errors))
        return receipt
    except Exception:
        if destination.exists():
            targets = [destination] if created else list(destination.iterdir())
            for target in targets:
                for path in sorted(target.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                    try:
                        os.chmod(path, 0o755 if path.is_dir() else 0o644)
                    except OSError:
                        pass
                try:
                    os.chmod(target, 0o755 if target.is_dir() else 0o644)
                except OSError:
                    pass
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        raise
