"""Local, deterministic staging for the sealed CodeTalks v2.4 candidate.

The archive is an evaluator input.  This module never executes it and never
places its contents in the PANGEA data workspace.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


ARCHIVE_PATH = Path("/Users/shepard/Downloads/codetalks-fused-v2.4-zh.zip")
ARCHIVE_SHA256 = "7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325"
ARCHIVE_ROOT = "codetalks-fused-v2.4-zh/"
SKILL_NAME = "codetalks-source-driven-blackbox-v2"
SKILL_VERSION = "2.4.0"
SKILL_RELATIVE_ROOT = Path(".opencode/skills") / SKILL_NAME
ADAPTER_RELATIVE_PATH = Path(".opencode/agents/codetalks-fused-v2.4.md")
OUTPUT_RELATIVE_ROOT = Path("codetalks-data")
MANIFEST_NAME = "codetalks-evaluator-manifest.json"
FINAL_RECEIPT_NAME = "codetalks-final-receipt.json"
EXPECTED_FILE_COUNT = 37
REQUIRED_SKILL_FILE = "SKILL.md"
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 96 * 1024 * 1024


class CodeTalksStagingError(ValueError):
    """The candidate archive or its controlled outputs violate the contract."""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_member(name: str) -> tuple[str, bool]:
    if not isinstance(name, str) or not name.startswith(ARCHIVE_ROOT):
        raise CodeTalksStagingError("archive member is outside the sole allowed root")
    if "\\" in name or "\x00" in name:
        raise CodeTalksStagingError("archive member has an invalid separator")
    relative = name[len(ARCHIVE_ROOT):]
    is_directory = relative.endswith("/")
    if not relative or (is_directory and relative == "/"):
        raise CodeTalksStagingError("archive root must not be an archive member")
    value = relative[:-1] if is_directory else relative
    path = PurePosixPath(value)
    if (path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts)
            or path.as_posix() != value):
        raise CodeTalksStagingError("archive member path is not a clean relative path")
    return path.as_posix(), is_directory


def _member_kind(info: zipfile.ZipInfo) -> str:
    mode = (info.external_attr >> 16) & 0xFFFF
    if info.is_dir() or info.filename.endswith("/"):
        return "directory"
    # Archives made on Windows commonly provide mode 0; regular file is the
    # only compatible interpretation.  A supplied Unix type must be regular.
    kind = stat.S_IFMT(mode)
    if kind in (0, stat.S_IFREG):
        return "file"
    if kind == stat.S_IFLNK:
        raise CodeTalksStagingError("archive symlink members are not accepted")
    raise CodeTalksStagingError("archive special members are not accepted")


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that does not silently replace duplicate mappings."""


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise CodeTalksStagingError("SKILL.md frontmatter has duplicate keys")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping,
)


def _parse_skill_metadata(payload: bytes) -> dict[str, str]:
    """Parse safe YAML frontmatter and bind its scalar identity fields."""
    try:
        lines = payload.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise CodeTalksStagingError("SKILL.md must be UTF-8") from exc
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise CodeTalksStagingError("SKILL.md must begin with frontmatter")
    closing = next((index for index, line in enumerate(lines[1:], 1)
                    if line.rstrip("\r\n") == "---"), None)
    if closing is None:
        raise CodeTalksStagingError("SKILL.md frontmatter is not closed")
    try:
        metadata = yaml.load("".join(lines[1:closing]), Loader=_UniqueKeySafeLoader)
    except (yaml.YAMLError, TypeError, ValueError, CodeTalksStagingError) as exc:
        if isinstance(exc, CodeTalksStagingError):
            raise
        raise CodeTalksStagingError("SKILL.md frontmatter is invalid") from exc
    if not isinstance(metadata, dict):
        raise CodeTalksStagingError("SKILL.md frontmatter must be a mapping")
    for key in ("name", "version"):
        if not isinstance(metadata.get(key), str) or not metadata[key].strip():
            raise CodeTalksStagingError(f"SKILL.md frontmatter {key} must be a scalar string")
    if metadata.get("name") != SKILL_NAME:
        raise CodeTalksStagingError("SKILL.md frontmatter name does not match the frozen skill")
    if metadata.get("version") != SKILL_VERSION:
        raise CodeTalksStagingError("SKILL.md frontmatter version does not match the frozen skill")
    return {"name": metadata["name"], "version": metadata["version"]}


def _checked_members(archive: Path) -> tuple[list[tuple[zipfile.ZipInfo, str]], dict[str, str]]:
    if not archive.is_file() or archive.is_symlink():
        raise CodeTalksStagingError("candidate archive must be a regular non-link file")
    if sha256_file(archive) != ARCHIVE_SHA256:
        raise CodeTalksStagingError("candidate archive SHA-256 does not match the frozen value")
    try:
        package = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise CodeTalksStagingError("candidate archive is not a valid zip") from exc
    with package:
        seen: set[str] = set()
        files: list[tuple[zipfile.ZipInfo, str]] = []
        directories: set[str] = set()
        total = 0
        for info in package.infolist():
            relative, directory_name = _relative_member(info.filename)
            kind = _member_kind(info)
            if directory_name != (kind == "directory"):
                raise CodeTalksStagingError("archive directory metadata is inconsistent")
            key = relative + ("/" if kind == "directory" else "")
            if key in seen:
                raise CodeTalksStagingError("archive has duplicate member names")
            seen.add(key)
            if kind == "file":
                if info.file_size > MAX_MEMBER_BYTES:
                    raise CodeTalksStagingError("archive member exceeds the size limit")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise CodeTalksStagingError("archive total exceeds the size limit")
                files.append((info, relative))
            else:
                directories.add(relative)
        file_paths = {relative for _, relative in files}
        for relative in file_paths:
            if relative in directories or any(parent in file_paths for parent in PurePosixPath(relative).parents
                                              if parent != PurePosixPath(".")):
                raise CodeTalksStagingError("archive members have conflicting file and directory paths")
        if len(files) != EXPECTED_FILE_COUNT:
            raise CodeTalksStagingError("archive file count differs from the frozen 37-file candidate")
        if REQUIRED_SKILL_FILE not in {relative for _, relative in files}:
            raise CodeTalksStagingError("archive is missing the required SKILL.md entry")
        skill_info = next(info for info, relative in files if relative == REQUIRED_SKILL_FILE)
        return files, _parse_skill_metadata(package.read(skill_info))


def _adapter_bytes() -> bytes:
    return ("---\n"
            "description: Run the staged CodeTalks v2.4 skill for the assigned task.\n"
            "mode: primary\n"
            "---\n\n"
            "Load and follow `.opencode/skills/codetalks-source-driven-blackbox-v2/SKILL.md` "
            "for the assigned task. Write any file outputs only under `codetalks-data/`.\n").encode("utf-8")


def _write_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o444)
    except FileExistsError as exc:
        raise CodeTalksStagingError("refusing to overwrite a staged candidate file") from exc
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _freeze_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise CodeTalksStagingError("staged candidate unexpectedly contains a symlink")
        if path.is_file():
            path.chmod(0o444)
        elif path.is_dir():
            path.chmod(0o555)
    root.chmod(0o555)


def materialize_candidate(destination: Path, archive: Path = ARCHIVE_PATH) -> dict[str, Any]:
    """Verify and materialize the frozen archive under an isolated root.

    ``destination`` must not exist.  The returned manifest is evaluator-owned,
    while only ``codetalks-data`` remains writable for candidate output.
    """
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise CodeTalksStagingError("candidate destination must not already exist")
    members, skill_metadata = _checked_members(Path(archive))
    temporary_parent = destination.parent
    temporary_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".codetalks-stage-", dir=temporary_parent))
    try:
        skill_root = staging / SKILL_RELATIVE_ROOT
        hashes: dict[str, str] = {}
        with zipfile.ZipFile(archive) as package:
            for info, relative in members:
                payload = package.read(info)
                if len(payload) != info.file_size:
                    raise CodeTalksStagingError("archive member changed during extraction")
                target = skill_root / relative
                _write_exclusive(target, payload)
                hashes[relative] = _sha256_bytes(payload)
        adapter = staging / ADAPTER_RELATIVE_PATH
        adapter_payload = _adapter_bytes()
        _write_exclusive(adapter, adapter_payload)
        output_root = staging / OUTPUT_RELATIVE_ROOT
        output_root.mkdir(mode=0o700)
        manifest = {
            "schema_version": "1.0",
            "archive": {"filename": Path(archive).name, "sha256": ARCHIVE_SHA256},
            "skill": {"name": skill_metadata["name"], "version": skill_metadata["version"],
                      "relative_root": SKILL_RELATIVE_ROOT.as_posix(), "files": hashes},
            "adapter": {"relative_path": ADAPTER_RELATIVE_PATH.as_posix(),
                        "sha256": _sha256_bytes(adapter_payload)},
            "output_root": OUTPUT_RELATIVE_ROOT.as_posix(),
        }
        manifest_payload = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
        _write_exclusive(staging / MANIFEST_NAME, manifest_payload)
        _freeze_tree(skill_root)
        adapter.chmod(0o444)
        (staging / ".opencode" / "agents").chmod(0o555)
        (staging / ".opencode").chmod(0o555)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging.replace(destination)
        return {**manifest, "manifest_sha256": _sha256_bytes(manifest_payload)}
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _controlled_output_members(output_root: Path) -> dict[str, tuple[int, int, int, int, int, int]]:
    if output_root.is_symlink() or not output_root.is_dir():
        raise CodeTalksStagingError("controlled output root must be a directory, not a link")
    members: dict[str, tuple[int, int, int, int, int, int]] = {}
    for path in sorted(output_root.rglob("*")):
        relative = path.relative_to(output_root).as_posix()
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise CodeTalksStagingError("controlled output must not contain symlinks")
        if stat.S_ISDIR(info.st_mode):
            continue
        if not stat.S_ISREG(info.st_mode):
            raise CodeTalksStagingError("controlled output must contain only regular files")
        if path.suffix.lower() not in {".md", ".json"}:
            raise CodeTalksStagingError("controlled output accepts only Markdown or JSON")
        members[relative] = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return members


def _read_stable_regular(path: Path, expected: tuple[int, int, int, int, int, int]) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise CodeTalksStagingError("controlled output cannot be safely opened") from exc
    try:
        before = os.fstat(descriptor)
        fingerprint = (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if not stat.S_ISREG(before.st_mode) or fingerprint != expected:
            raise CodeTalksStagingError("controlled output changed before read")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            payload = handle.read()
        after = os.fstat(descriptor)
        after_fingerprint = (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        if after_fingerprint != expected or len(payload) != before.st_size:
            raise CodeTalksStagingError("controlled output changed during read")
    finally:
        os.close(descriptor)
    try:
        if (lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns))(path.lstat()) != expected:
            raise CodeTalksStagingError("controlled output changed after read")
    except OSError as exc:
        raise CodeTalksStagingError("controlled output changed after read") from exc
    return payload


def _validated_manifest(root: Path, expected_sha256: str, expected_materialization: dict[str, Any] | None = None) -> str:
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise CodeTalksStagingError("evaluator expected manifest SHA-256 is required")
    path = root / MANIFEST_NAME
    info = path.lstat() if path.exists() else None
    if info is None or stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise CodeTalksStagingError("candidate evaluator manifest is required")
    payload = _read_stable_regular(path, (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    actual = _sha256_bytes(payload)
    if actual != expected_sha256:
        raise CodeTalksStagingError("candidate evaluator manifest differs from evaluator receipt")
    try:
        manifest = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodeTalksStagingError("candidate evaluator manifest is invalid") from exc
    expected = expected_materialization or {
        "archive": {"filename": ARCHIVE_PATH.name, "sha256": ARCHIVE_SHA256},
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION, "relative_root": SKILL_RELATIVE_ROOT.as_posix()},
        "adapter": {"relative_path": ADAPTER_RELATIVE_PATH.as_posix(), "sha256": _sha256_bytes(_adapter_bytes())},
    }
    if (not isinstance(manifest, dict) or set(manifest) != {"schema_version", "archive", "skill", "adapter", "output_root"}
            or manifest.get("schema_version") != "1.0" or manifest.get("output_root") != OUTPUT_RELATIVE_ROOT.as_posix()
            or manifest.get("archive") != expected["archive"]
            or not isinstance(manifest.get("skill"), dict) or any(manifest["skill"].get(key) != expected["skill"].get(key) for key in ("name", "version", "relative_root"))
            or not isinstance(manifest["skill"].get("files"), dict) or len(manifest["skill"]["files"]) != EXPECTED_FILE_COUNT
            or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in manifest["skill"]["files"].values())
            or manifest.get("adapter") != expected["adapter"]):
        raise CodeTalksStagingError("candidate evaluator manifest violates frozen materialization")
    return actual


def collect_final_output(
    candidate_root: Path, native_final_text: str | None = None, *, evaluator_root: Path | None = None,
    expected_manifest_sha256: str | None = None,
    expected_materialization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind the ordered CodeTalks file set, or (only when empty) native text.

    Receipts are evaluator-owned.  A native chat summary is deliberately only
    hashed as auxiliary information when formal files exist; it is never
    mixed into the content presented to the scorer.
    """
    root = Path(candidate_root)
    output_root = root / OUTPUT_RELATIVE_ROOT
    if evaluator_root is None:
        raise CodeTalksStagingError("evaluator-owned receipt root is required")
    receipt_root = Path(evaluator_root)
    if receipt_root.is_symlink() or not receipt_root.is_dir():
        raise CodeTalksStagingError("evaluator receipt root must be an existing regular directory")
    try:
        receipt_root.resolve().relative_to(root.resolve())
    except ValueError:
        pass
    else:
        raise CodeTalksStagingError("evaluator receipt root must be outside the candidate root")
    manifest_sha256 = _validated_manifest(root, expected_manifest_sha256 or "", expected_materialization)
    before_members = _controlled_output_members(output_root)
    formal_content = ""
    if before_members:
        chunks: list[str] = []
        files: list[dict[str, str]] = []
        for relative, fingerprint in sorted(before_members.items()):
            payload = _read_stable_regular(output_root / relative, fingerprint)
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CodeTalksStagingError("controlled output must be UTF-8") from exc
            digest = _sha256_bytes(payload)
            files.append({"path": relative, "sha256": digest})
            chunks.append(f"<!-- codetalks-file: {relative} -->\n{text}")
        if _controlled_output_members(output_root) != before_members:
            raise CodeTalksStagingError("controlled output directory changed during collection")
        formal_content = "\n\n".join(chunks)
        receipt = {"schema_version": "1.0", "output_root": OUTPUT_RELATIVE_ROOT.as_posix(),
                   "files": files,
                   "formal_content_sha256": _sha256_bytes(formal_content.encode("utf-8")),
                   "native_final_text_sha256": (_sha256_bytes(native_final_text.encode("utf-8"))
                                                if isinstance(native_final_text, str) else None)}
    else:
        if not isinstance(native_final_text, str) or not native_final_text.strip():
            raise CodeTalksStagingError("a non-empty bound native final text is required when output is empty")
        receipt: dict[str, Any] = {"schema_version": "1.0", "output_root": OUTPUT_RELATIVE_ROOT.as_posix(),
                                   "native_final_text_sha256": _sha256_bytes(native_final_text.encode("utf-8"))}
    receipt["candidate_manifest_sha256"] = manifest_sha256
    _write_exclusive(receipt_root / FINAL_RECEIPT_NAME, (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode("utf-8"))
    return {**receipt, "formal_content": formal_content or native_final_text}
