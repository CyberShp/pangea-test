"""Stage public benchmark inputs; sealed evaluator answers never enter this API."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError


ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = ROOT / "manifest.json"
PUBLIC_CASE_SCHEMA_PATH = ROOT / "evaluation" / "public-case.schema.json"
PUBLIC_CASE_FIELDS = {
    "id", "title", "repository_id", "repository_url", "frozen_commit", "mode",
    "phase_membership", "source_scope", "contract", "agent_input", "safety_boundary",
}
REPOSITORIES = {
    "spdk": ("https://github.com/spdk/spdk", "97af299e3c76368219f0cddcc710fafd57edcc1c"),
    "nvme-cli": ("https://github.com/linux-nvme/nvme-cli", "cc00f4fd5d8262c440d033de9504ebf641880e62"),
}
PHASES = {"smoke", "pilot", "full"}
# Keep this literal in lockstep with the negative string patterns in the public
# schema.  "oracle" deliberately subsumes sealed_oracle/oracle_answer.
FORBIDDEN_MARKERS = {
    "fault_mode", "evidence_keywords", "scoring", "oracle",
    "expected findings", "expected_findings", "skill triggers", "skill_triggers", "mutations",
}
EXPECTED_PHASES = {
    "smoke": {"spdk-recv-state-diagnostics", "nvme-cli-command-dispatch"},
    "pilot": {"spdk-nvmf-tcp-receive-closure", "spdk-recv-state-diagnostics",
              "nvme-cli-command-dispatch", "nvme-cli-format-safety"},
}
EXPECTED_CASE_IDS = {
    "spdk-nvmf-tcp-receive-closure", "spdk-nvme-rdma-reset-recovery",
    "spdk-nvmf-tcp-resource-recovery", "spdk-recv-state-diagnostics",
    "nvme-cli-command-dispatch", "nvme-cli-format-safety",
    "nvme-cli-sanitize-status", "nvme-cli-parse-open-boundary",
}
EXPECTED_CASE_METADATA = {
    "spdk-nvmf-tcp-receive-closure": ("spdk", {"pilot", "full"}),
    "spdk-nvme-rdma-reset-recovery": ("spdk", {"full"}),
    "spdk-nvmf-tcp-resource-recovery": ("spdk", {"full"}),
    "spdk-recv-state-diagnostics": ("spdk", {"smoke", "pilot", "full"}),
    "nvme-cli-command-dispatch": ("nvme-cli", {"smoke", "pilot", "full"}),
    "nvme-cli-format-safety": ("nvme-cli", {"pilot", "full"}),
    "nvme-cli-sanitize-status": ("nvme-cli", {"full"}),
    "nvme-cli-parse-open-boundary": ("nvme-cli", {"full"}),
}
# Independent Python-side freeze of all 34 source-scope entries.  The schema
# carries its own literals as a second enforcement layer.
EXPECTED_SCOPE_PATHS = {
    "spdk-nvmf-tcp-receive-closure": (
        "include/spdk_internal/nvme_tcp.h", "lib/nvmf/tcp.c",
        "test/unit/lib/nvmf/tcp.c/tcp_ut.c",
    ),
    "spdk-nvme-rdma-reset-recovery": (
        "lib/nvme/nvme_ctrlr.c", "lib/nvme/nvme_internal.h", "lib/nvme/nvme_qpair.c",
        "lib/nvme/nvme_rdma.c", "test/unit/lib/nvme/nvme_ctrlr.c/nvme_ctrlr_ut.c",
        "test/unit/lib/nvme/nvme_qpair.c/nvme_qpair_ut.c",
        "test/unit/lib/nvme/nvme_rdma.c/nvme_rdma_ut.c",
    ),
    "spdk-nvmf-tcp-resource-recovery": (
        "include/spdk_internal/nvme_tcp.h", "lib/nvmf/tcp.c",
        "test/unit/lib/nvmf/tcp.c/tcp_ut.c",
    ),
    "spdk-recv-state-diagnostics": (
        "include/spdk_internal/nvme_tcp.h", "lib/nvme/nvme_tcp.c",
        "test/unit/lib/nvme/nvme_tcp.c/nvme_tcp_ut.c",
    ),
    "nvme-cli-command-dispatch": (
        "cmd_handler.h", "nvme-builtin.h", "nvme.c", "plugin.c",
    ),
    "nvme-cli-format-safety": (
        "cmd_handler.h", "libnvme/src/nvme/ioctl.c", "libnvme/src/nvme/lib.c",
        "nvme-builtin.h", "nvme.c", "nvme.h", "plugin.c",
    ),
    "nvme-cli-sanitize-status": (
        "libnvme/src/nvme/ioctl.c", "nvme-builtin.h", "nvme.c", "nvme.h",
    ),
    "nvme-cli-parse-open-boundary": (
        "libnvme/src/nvme/lib.c", "nvme.c", "nvme.h",
    ),
}


class BenchmarkError(ValueError):
    """Raised when public benchmark data cannot be safely staged."""


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """Read one regular-file snapshot and reject an in-place concurrent edit."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise BenchmarkError("manifest must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        stable = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                  before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                                          after.st_mtime_ns, after.st_ctime_ns)
        if not stable:
            raise BenchmarkError("manifest changed while being read")
        payload = b"".join(chunks).decode("utf-8")
        return json.loads(payload)
    finally:
        os.close(fd)


def canonical_case_payload(case: dict[str, Any]) -> bytes:
    """Return the one public byte representation used by every case stager."""
    return (json.dumps(case, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in FORBIDDEN_MARKERS or _contains_forbidden(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return isinstance(value, str) and any(marker in value.lower() for marker in FORBIDDEN_MARKERS)


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_relative_path(value: Any) -> bool:
    if (not _nonblank(value) or value != value.strip() or "\\" in value
            or value.endswith("/") or any(character in value for character in "\r\n\t\x00")):
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and "." not in path.parts and ".." not in path.parts
            and path.as_posix() == value)


def _schema_validator() -> Draft202012Validator:
    schema = json.loads(PUBLIC_CASE_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _validate_manifest_snapshot(manifest: Any) -> list[str]:
    """Validate one already-read manifest snapshot with both contract layers."""
    try:
        validator = _schema_validator()
    except (OSError, json.JSONDecodeError, TypeError, ValueError, SchemaError) as exc:
        return [f"cannot read public benchmark contract: {exc}"]
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]

    errors: list[str] = []
    if set(manifest) != {"schema_version", "description", "cases"} or manifest.get("schema_version") != "2.0":
        errors.append("manifest must contain only schema_version, description, cases at schema 2.0")
    if not _nonblank(manifest.get("description")):
        errors.append("manifest description is required")
    # Top-level description is metadata for maintainers and is deliberately not
    # staged.  Only the selected case object and agent_input cross the boundary;
    # tests independently prove description text cannot appear in either file.
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != 8:
        return errors + ["manifest must contain exactly eight cases"]

    ids: set[str] = set()
    observed_phases: dict[str, set[str]] = {phase: set() for phase in PHASES}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"case[{index}]: must be an object")
            continue
        raw_id = case.get("id")
        case_id = raw_id if isinstance(raw_id, str) else f"case[{index}]"
        schema_errors = sorted(validator.iter_errors(case), key=lambda item: list(item.absolute_path))
        errors.extend(f"{case_id}: schema: {item.message}" for item in schema_errors)
        if set(case) != PUBLIC_CASE_FIELDS:
            errors.append(f"{case_id}: public case fields must exactly match contract")
            continue
        if not isinstance(raw_id, str) or not re.fullmatch(r"[a-z0-9-]+", raw_id):
            errors.append(f"{case_id}: invalid case id")
        elif raw_id in ids:
            errors.append(f"duplicate case id: {raw_id}")
        else:
            ids.add(raw_id)
        if not _nonblank(case["title"]) or case["mode"] != "module-analysis":
            errors.append(f"{case_id}: invalid title or mode")
        if case.get("contract") != {
            "scenario": "module-analysis", "mode": "module_analysis", "analysis_depth": "complete",
            "confirmation_source": "user_explicit_bypass", "materials_status": "confirmed_none",
        }:
            errors.append(f"{case_id}: contract projection does not match the frozen evaluator policy")

        repo = case["repository_id"]
        if (not isinstance(repo, str) or repo not in REPOSITORIES
                or not isinstance(case["repository_url"], str)
                or not isinstance(case["frozen_commit"], str)
                or (case["repository_url"], case["frozen_commit"]) != REPOSITORIES.get(repo)):
            errors.append(f"{case_id}: repository or frozen commit is not allowlisted")

        phases = case["phase_membership"]
        phases_valid = (isinstance(phases, list) and bool(phases)
                        and all(isinstance(phase, str) for phase in phases)
                        and len(phases) == len(set(phases)) and set(phases) <= PHASES)
        if not phases_valid:
            errors.append(f"{case_id}: invalid phase_membership")
        elif isinstance(raw_id, str):
            for phase in phases:
                observed_phases[phase].add(raw_id)
            expected_metadata = EXPECTED_CASE_METADATA.get(raw_id)
            if expected_metadata is not None and (repo, set(phases)) != expected_metadata:
                errors.append(f"{case_id}: repository or phase membership does not match case identity")

        scope = case["source_scope"]
        paths = scope.get("paths") if isinstance(scope, dict) else None
        paths_valid = (isinstance(scope, dict) and set(scope) <= {"paths", "symbol_hints"}
                       and isinstance(paths, list) and bool(paths)
                       and all(isinstance(path, str) for path in paths)
                       and len(paths) == len(set(paths))
                       and all(_valid_relative_path(path) for path in paths))
        if not paths_valid:
            errors.append(f"{case_id}: invalid source_scope paths")
        elif not isinstance(raw_id, str) or tuple(paths) != EXPECTED_SCOPE_PATHS.get(raw_id):
            errors.append(f"{case_id}: source_scope paths do not match frozen case scope")
        hints = scope.get("symbol_hints") if isinstance(scope, dict) else None
        if hints is not None and (not isinstance(hints, list)
                                  or not all(_nonblank(hint) for hint in hints)):
            errors.append(f"{case_id}: invalid source_scope symbol_hints")
        if not _nonblank(case["agent_input"]):
            errors.append(f"{case_id}: agent_input is required")
        if not _nonblank(case["safety_boundary"]):
            errors.append(f"{case_id}: safety_boundary is required")
        if _contains_forbidden(case):
            errors.append(f"{case_id}: contains a private evaluation marker")

    if ids != EXPECTED_CASE_IDS:
        errors.append("case ids do not match the frozen eight-case set")
    expected = {**EXPECTED_PHASES, "full": EXPECTED_CASE_IDS}
    for phase, expected_ids in expected.items():
        if observed_phases[phase] != expected_ids:
            errors.append(f"{phase}: phase membership does not match frozen matrix")
    return errors


def validate_manifest(root: Path = ROOT) -> list[str]:
    """Return public-contract errors without consulting workspace Oracle data."""
    try:
        manifest = load_manifest(root / "manifest.json")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return [f"cannot read public benchmark contract: {exc}"]
    return _validate_manifest_snapshot(manifest)


def _lexical_absolute(path: Path, label: str) -> Path:
    if ".." in path.parts:
        raise BenchmarkError(f"{label} traversal is not allowed")
    return Path(os.path.abspath(os.fspath(path)))


def _inode(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def _entry_inode(dir_fd: int, name: str) -> tuple[int, int] | None:
    try:
        return _inode(os.stat(name, dir_fd=dir_fd, follow_symlinks=False))
    except FileNotFoundError:
        return None


DirectoryChain = tuple[tuple[str, tuple[int, int]], ...]
StatFingerprint = tuple[int, int, int, int, int, int]


def _stat_fingerprint(info: os.stat_result) -> StatFingerprint:
    """Metadata epoch used by the begin/end consistency protocol (never atime)."""
    try:
        mtime_ns = info.st_mtime_ns
        ctime_ns = info.st_ctime_ns
    except AttributeError as exc:
        raise BenchmarkError("filesystem does not expose nanosecond metadata epochs") from exc
    if not isinstance(mtime_ns, int) or not isinstance(ctime_ns, int) or ctime_ns <= 0:
        raise BenchmarkError("filesystem metadata epochs are not reliable enough")
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, mtime_ns, ctime_ns)


def _require_epoch_advance(before: os.stat_result, after: os.stat_result, label: str) -> None:
    """Fail closed when this filesystem cannot observe a known mutation."""
    before_epoch = _stat_fingerprint(before)
    after_epoch = _stat_fingerprint(after)
    if before_epoch[:2] != after_epoch[:2]:
        raise BenchmarkError(f"{label} identity changed during epoch capability check")
    if before_epoch[4:] == after_epoch[4:]:
        raise BenchmarkError(f"filesystem did not expose an epoch change for {label}")


def _open_absolute_directory(path: Path) -> tuple[int, tuple[int, int], int, DirectoryChain]:
    """Open *path* without symlinks and freeze every name/inode edge from `/`."""
    if not path.is_absolute():
        raise BenchmarkError("directory chain must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    anchor_fd = os.open(path.anchor, flags)
    anchor_identity = _inode(os.fstat(anchor_fd))
    current_fd = anchor_fd
    chain: list[tuple[str, tuple[int, int]]] = []
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            identity = _inode(os.fstat(next_fd))
            chain.append((component, identity))
            if current_fd != anchor_fd:
                os.close(current_fd)
            current_fd = next_fd
        if current_fd == anchor_fd:
            current_fd = os.dup(anchor_fd)
        return anchor_fd, anchor_identity, current_fd, tuple(chain)
    except Exception:
        if current_fd != anchor_fd:
            os.close(current_fd)
        os.close(anchor_fd)
        raise


def _rewalk_directory_chain(
    anchor_fd: int,
    anchor_identity: tuple[int, int],
    chain: DirectoryChain,
) -> int:
    """Re-resolve a frozen absolute chain and fail on rename/replacement."""
    if _inode(os.fstat(anchor_fd)) != anchor_identity:
        raise BenchmarkError("filesystem anchor identity changed")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.dup(anchor_fd)
    try:
        for component, expected_identity in chain:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            observed = os.fstat(next_fd)
            os.close(current_fd)
            current_fd = next_fd
            if not stat.S_ISDIR(observed.st_mode) or _inode(observed) != expected_identity:
                raise BenchmarkError(f"staging ancestor changed: {component}")
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def _capture_directory_chain(
    anchor_fd: int,
    anchor_identity: tuple[int, int],
    chain: DirectoryChain,
) -> tuple[int, tuple[tuple[str, StatFingerprint], ...]]:
    """Rewalk and capture every directory epoch from anchor through parent."""
    anchor_info = os.fstat(anchor_fd)
    if _inode(anchor_info) != anchor_identity:
        raise BenchmarkError("filesystem anchor identity changed")
    records: list[tuple[str, StatFingerprint]] = [("/", _stat_fingerprint(anchor_info))]
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    current_fd = os.dup(anchor_fd)
    try:
        for component, expected_identity in chain:
            next_fd = os.open(component, flags, dir_fd=current_fd)
            observed = os.fstat(next_fd)
            os.close(current_fd)
            current_fd = next_fd
            if not stat.S_ISDIR(observed.st_mode) or _inode(observed) != expected_identity:
                raise BenchmarkError(f"staging ancestor changed: {component}")
            records.append((component, _stat_fingerprint(observed)))
        return current_fd, tuple(records)
    except Exception:
        os.close(current_fd)
        raise


def _verify_parent_chain(
    anchor_fd: int,
    anchor_identity: tuple[int, int],
    chain: DirectoryChain,
    held_parent_fd: int,
) -> None:
    fresh_parent_fd = _rewalk_directory_chain(anchor_fd, anchor_identity, chain)
    try:
        if _inode(os.fstat(fresh_parent_fd)) != _inode(os.fstat(held_parent_fd)):
            raise BenchmarkError("staging parent path changed")
    finally:
        os.close(fresh_parent_fd)


def _write_bundle_file(dir_fd: int, name: str, payload: bytes) -> tuple[int, tuple[int, int]]:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
    try:
        # Ownership comes from the exclusively-created descriptor, never from a
        # mutable name.  The name is only a second observation to bind to it.
        created = os.fstat(fd)
        identity = _inode(created)
        visible_at_creation = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (not stat.S_ISREG(created.st_mode) or not stat.S_ISREG(visible_at_creation.st_mode)
                or _inode(visible_at_creation) != identity):
            raise BenchmarkError(f"private bundle member is not regular: {name}")
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while staging public case")
            view = view[written:]
        os.fsync(fd)
        _require_epoch_advance(created, os.fstat(fd), f"content write for {name}")
        return fd, identity
    except Exception:
        os.close(fd)
        # POSIX has no unlink-if-inode-equals operation.  Never turn a
        # stat(name)->unlink(name) observation into a claim of ownership.
        raise


def _read_fd(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _verify_bundle(
    dir_fd: int,
    dir_identity: tuple[int, int],
    members: dict[str, tuple[int, tuple[int, int], bytes]],
    *,
    expected_dir_mode: int | None = None,
    expected_file_mode: int | None = None,
) -> None:
    opened_dir = os.fstat(dir_fd)
    if not stat.S_ISDIR(opened_dir.st_mode) or _inode(opened_dir) != dir_identity:
        raise BenchmarkError("private bundle directory identity changed")
    if (expected_dir_mode is not None
            and stat.S_IMODE(opened_dir.st_mode) != expected_dir_mode):
        raise BenchmarkError("private bundle directory permissions changed")
    if set(os.listdir(dir_fd)) != set(members):
        raise BenchmarkError("private bundle entries changed")
    for name, (fd, identity, payload) in members.items():
        opened = os.fstat(fd)
        try:
            visible = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as exc:
            raise BenchmarkError(f"private bundle member unavailable: {name}: {exc}") from exc
        if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(visible.st_mode)
                or _inode(opened) != identity or _inode(visible) != identity):
            raise BenchmarkError(f"private bundle member identity changed: {name}")
        if (expected_file_mode is not None
                and (stat.S_IMODE(opened.st_mode) != expected_file_mode
                     or stat.S_IMODE(visible.st_mode) != expected_file_mode)):
            raise BenchmarkError(f"private bundle member permissions changed: {name}")
        if opened.st_size != len(payload) or _read_fd(fd) != payload:
            raise BenchmarkError(f"private bundle member content changed: {name}")


def _capture_published_epoch(
    anchor_fd: int,
    anchor_identity: tuple[int, int],
    chain: DirectoryChain,
    held_parent_fd: int,
    held_destination_fd: int,
    destination_name: str,
    directory_identity: tuple[int, int],
    members: dict[str, tuple[int, tuple[int, int], bytes]],
) -> tuple[Any, ...]:
    """Capture and validate one complete epoch through the returned path."""
    parent_fd, ancestor_epochs = _capture_directory_chain(anchor_fd, anchor_identity, chain)
    destination_fd: int | None = None
    visible_fds: list[int] = []
    try:
        parent_info = os.fstat(parent_fd)
        held_parent_info = os.fstat(held_parent_fd)
        if _inode(parent_info) != _inode(held_parent_info):
            raise BenchmarkError("held parent and returned path parent differ")
        parent_entries = tuple(sorted(os.listdir(parent_fd)))
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        destination_fd = os.open(destination_name, flags, dir_fd=parent_fd)
        destination_info = os.fstat(destination_fd)
        held_destination_info = os.fstat(held_destination_fd)
        if (not stat.S_ISDIR(destination_info.st_mode)
                or _inode(destination_info) != directory_identity
                or _inode(held_destination_info) != directory_identity):
            raise BenchmarkError("published destination identity changed")
        if (stat.S_IMODE(destination_info.st_mode) != 0o555
                or stat.S_IMODE(held_destination_info.st_mode) != 0o555):
            raise BenchmarkError("published destination permissions changed")
        destination_entries = tuple(sorted(os.listdir(destination_fd)))
        if destination_entries != tuple(sorted(members)):
            raise BenchmarkError("published destination entries changed")

        member_epochs: list[tuple[Any, ...]] = []
        file_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        for name in sorted(members):
            original_fd, expected_identity, payload = members[name]
            visible_name_info = os.stat(name, dir_fd=destination_fd, follow_symlinks=False)
            visible_fd = os.open(name, file_flags, dir_fd=destination_fd)
            visible_fds.append(visible_fd)
            visible_open_info = os.fstat(visible_fd)
            original_info = os.fstat(original_fd)
            if (not stat.S_ISREG(visible_name_info.st_mode)
                    or not stat.S_ISREG(visible_open_info.st_mode)
                    or not stat.S_ISREG(original_info.st_mode)
                    or _inode(visible_name_info) != expected_identity
                    or _inode(visible_open_info) != expected_identity
                    or _inode(original_info) != expected_identity):
                raise BenchmarkError(f"published member identity changed: {name}")
            if any(stat.S_IMODE(info.st_mode) != 0o444
                   for info in (visible_name_info, visible_open_info, original_info)):
                raise BenchmarkError(f"published member permissions changed: {name}")
            content = _read_fd(visible_fd)
            visible_after_read = os.fstat(visible_fd)
            if _stat_fingerprint(visible_open_info) != _stat_fingerprint(visible_after_read):
                raise BenchmarkError(f"published member changed while being read: {name}")
            if content != payload:
                raise BenchmarkError(f"published member content changed: {name}")
            member_epochs.append((
                name,
                _stat_fingerprint(visible_name_info),
                _stat_fingerprint(visible_open_info),
                _stat_fingerprint(visible_after_read),
                _stat_fingerprint(original_info),
                hashlib.sha256(content).hexdigest(),
            ))
        return (
            ancestor_epochs,
            _stat_fingerprint(parent_info),
            _stat_fingerprint(held_parent_info),
            parent_entries,
            _stat_fingerprint(destination_info),
            _stat_fingerprint(held_destination_info),
            destination_entries,
            tuple(member_epochs),
        )
    finally:
        for visible_fd in visible_fds:
            os.close(visible_fd)
        if destination_fd is not None:
            os.close(destination_fd)
        os.close(parent_fd)


def _verify_published_path(
    anchor_fd: int,
    anchor_identity: tuple[int, int],
    chain: DirectoryChain,
    held_parent_fd: int,
    held_destination_fd: int,
    destination_name: str,
    directory_identity: tuple[int, int],
    members: dict[str, tuple[int, tuple[int, int], bytes]],
) -> None:
    """Require two equal, independently rewalked, fully valid epochs."""
    begin = _capture_published_epoch(
        anchor_fd, anchor_identity, chain, held_parent_fd, held_destination_fd,
        destination_name, directory_identity, members,
    )
    end = _capture_published_epoch(
        anchor_fd, anchor_identity, chain, held_parent_fd, held_destination_fd,
        destination_name, directory_identity, members,
    )
    if begin != end:
        raise BenchmarkError("published path changed during the validation epoch")


def _publish_directory_noreplace(parent_fd: int, source: str, destination: str) -> None:
    """Atomically rename a complete directory without replacing destination."""
    libc = ctypes.CDLL(None, use_errno=True)
    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    if sys.platform == "darwin":
        function = getattr(libc, "renameatx_np", None)
        if function is None:
            raise OSError(errno.ENOTSUP, "renameatx_np is unavailable")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                             ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(parent_fd, source_bytes, parent_fd, destination_bytes, 0x00000004)
    elif sys.platform.startswith("linux"):
        function = getattr(libc, "renameat2", None)
        if function is None:
            raise OSError(errno.ENOTSUP, "renameat2 is unavailable")
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                             ctypes.c_uint]
        function.restype = ctypes.c_int
        result = function(parent_fd, source_bytes, parent_fd, destination_bytes, 0x00000001)
    else:
        raise OSError(errno.ENOTSUP, f"atomic no-replace directory publish unsupported on {sys.platform}")
    if result != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), destination)


def stage_case(
    case_id: str,
    destination: Path,
    root: Path = ROOT,
    *,
    staging_root: Path,
) -> Path:
    """Atomically publish exactly TASK.md and CASE.json below a trusted root."""
    try:
        manifest = load_manifest(root / "manifest.json")
        errors = _validate_manifest_snapshot(manifest)
        if errors:
            raise BenchmarkError("invalid public benchmark manifest: " + "; ".join(errors))
        case = next((item for item in manifest["cases"] if item["id"] == case_id), None)
        if case is None:
            raise BenchmarkError(f"unknown benchmark case: {case_id}")

        raw_staging_root = Path(staging_root)
        if not raw_staging_root.is_absolute():
            raise BenchmarkError("trusted staging root must be absolute")
        destination = _lexical_absolute(Path(destination), "destination")
        trusted_root = _lexical_absolute(raw_staging_root, "trusted staging root")
        try:
            relative = destination.relative_to(trusted_root)
        except ValueError as exc:
            raise BenchmarkError("destination escapes trusted staging root") from exc
        if not relative.parts:
            raise BenchmarkError("destination must be a child of trusted staging root")

        parent_path = destination.parent
        anchor_fd, anchor_identity, parent_fd, parent_chain = _open_absolute_directory(parent_path)
        bundle_fd: int | None = None
        private_name = f".pangea-benchmark-{secrets.token_hex(24)}.tmp"
        destination_name = relative.parts[-1]
        private_identity: tuple[int, int] | None = None
        members: dict[str, tuple[int, tuple[int, int], bytes]] = {}
        private_created = False
        private_creation_attempted = False
        publish_attempted = False
        try:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            private_creation_attempted = True
            os.mkdir(private_name, 0o700, dir_fd=parent_fd)
            private_created = True
            bundle_fd = os.open(private_name, flags, dir_fd=parent_fd)
            # Descriptor identity first; bind the mutable private name only
            # after the descriptor is known to be a directory.
            bundle_info = os.fstat(bundle_fd)
            private_identity = _inode(bundle_info)
            created_bundle = os.stat(private_name, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(bundle_info.st_mode) or not stat.S_ISDIR(created_bundle.st_mode)
                    or _inode(created_bundle) != private_identity):
                raise BenchmarkError("private bundle is not a directory")

            case_payload = canonical_case_payload(case)
            payloads = {
                "CASE.json": case_payload,
                "TASK.md": (case["agent_input"] + "\n").encode("utf-8"),
            }
            for name, payload in payloads.items():
                directory_before_create = os.fstat(bundle_fd)
                fd, identity = _write_bundle_file(bundle_fd, name, payload)
                members[name] = (fd, identity, payload)
                _require_epoch_advance(directory_before_create, os.fstat(bundle_fd),
                                       f"directory entry creation for {name}")

            _verify_bundle(bundle_fd, private_identity, members)
            for fd, _identity, _payload in members.values():
                before_chmod = os.fstat(fd)
                os.fchmod(fd, 0o400)
                intermediate_chmod = os.fstat(fd)
                _require_epoch_advance(before_chmod, intermediate_chmod,
                                       "member permission epoch probe")
                os.fchmod(fd, 0o444)
                _require_epoch_advance(intermediate_chmod, os.fstat(fd),
                                       "member permission freeze")
                os.fsync(fd)
            os.fsync(bundle_fd)
            _verify_bundle(bundle_fd, private_identity, members,
                           expected_dir_mode=0o700, expected_file_mode=0o444)
            _verify_parent_chain(anchor_fd, anchor_identity, parent_chain, parent_fd)
            if _entry_inode(parent_fd, private_name) != private_identity:
                raise BenchmarkError("private bundle name changed before publication")

            publish_error: BaseException | None = None
            try:
                publish_attempted = True
                _publish_directory_noreplace(parent_fd, private_name, destination_name)
            except (OSError, ctypes.ArgumentError) as exc:
                publish_error = exc
            # Resolve the syscall's actual effect by identity.  This handles an
            # interrupted wrapper reporting an error after rename took effect.
            destination_identity = _entry_inode(parent_fd, destination_name)
            source_identity = _entry_inode(parent_fd, private_name)
            if not (destination_identity == private_identity and source_identity is None):
                if publish_error is not None:
                    raise publish_error
                raise BenchmarkError("atomic directory publication had an indeterminate effect")

            # macOS renameatx_np requires the source directory itself to remain
            # owner-writable.  Remove that permission immediately after the
            # atomic name transition, before accepting the public bundle.
            os.fchmod(bundle_fd, 0o555)
            os.fsync(bundle_fd)
            _verify_published_path(anchor_fd, anchor_identity, parent_chain,
                                   parent_fd, bundle_fd, destination_name,
                                   private_identity, members)
            os.fsync(parent_fd)
            # Success attests this complete post-durability snapshot.  A process
            # with the same UID can mutate after this linearization point; that
            # is explicitly outside this API's protection boundary.
            _verify_published_path(anchor_fd, anchor_identity, parent_chain,
                                   parent_fd, bundle_fd, destination_name,
                                   private_identity, members)
        except Exception as exc:
            if publish_attempted:
                suffix = ("native publication was attempted; no private or public name was cleaned; "
                          f"inspect {private_name!r} and {destination_name!r}")
            elif private_created or private_creation_attempted:
                suffix = ("private construction failed; POSIX has no inode-CAS unlink, so cleanup was "
                          f"skipped; inspect {private_name!r}")
            else:
                suffix = "private construction was not created"
            if isinstance(exc, BenchmarkError):
                raise BenchmarkError(f"{exc}; {suffix}") from exc
            raise BenchmarkError(f"cannot safely stage public benchmark case: {exc}; {suffix}") from exc
        finally:
            for fd, _identity, _payload in members.values():
                try:
                    os.close(fd)
                except OSError:
                    pass
            if bundle_fd is not None:
                os.close(bundle_fd)
            os.close(parent_fd)
            os.close(anchor_fd)
        return destination
    except BenchmarkError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError,
            KeyError, SchemaError, ctypes.ArgumentError) as exc:
        raise BenchmarkError(f"cannot safely stage public benchmark case: {exc}") from exc
