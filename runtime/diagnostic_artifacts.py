#!/usr/bin/env python3
"""Secure, recoverable diagnostic artifact transaction store.

The three diagnostic workflows publish to fixed Run-relative paths.  A
run-level advisory lock serializes cooperating writers; dirfd/O_NOFOLLOW
checks keep every operation bound to the originally opened managed tree.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import secrets
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class DiagnosticArtifactError(RuntimeError):
    """Unified public failure for validation, safety, and transaction errors."""


ARTIFACT_PATHS = {
    "log_summary": "log-summary.json",
    "pcap_summary": "pcap-summary.json",
    "failure_classification": "failure-classification.json",
}
INDEX_NAME = "diagnostic-artifacts.json"
JOURNAL_NAME = ".diagnostic-artifacts.journal.json"
LOCK_NAME = ".diagnostic-artifacts.lock"
SCHEMA_VERSION = "1.0"
HASH_KEYS = {"input_sha256", "body_sha256", "content_sha256"}
_HEX = set("0123456789abcdef")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class _ManagedRun:
    root_path: Path
    fds: list[int]
    bindings: list[tuple[int, str, tuple[int, int]]]
    run_fd: int
    internal_fd: int
    diagnostics_fd: int
    lock_fd: int
    lock_identity: tuple[int, int]

    def close(self) -> None:
        for fd in reversed(self.fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in _HEX for char in value)


def _draft_validate(value: Any, schema_name: str) -> None:
    """Use Draft 2020-12 when installed; manual validators remain fail-closed fallback."""
    try:
        import jsonschema
        schema = json.loads((_ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(schema).validate(value)
    except ImportError:
        return
    except (OSError, json.JSONDecodeError, jsonschema.ValidationError, jsonschema.SchemaError) as exc:
        raise DiagnosticArtifactError(f"{schema_name} 严格校验失败: {exc}") from exc


def _exact(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise DiagnosticArtifactError(f"{label} 字段必须精确为 {sorted(keys)}")
    return value


def _text(value: Any, label: str, minimum: int = 2) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise DiagnosticArtifactError(f"{label} 必须是具体非空文本")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise DiagnosticArtifactError(f"{label} 必须是数组")
    return value


def _validate_tool(value: Any) -> None:
    item = _exact(value, {"name", "version"}, "tool")
    _text(item["name"], "tool.name")
    _text(item["version"], "tool.version", 1)


def _validate_evidence_rows(rows: Any, prefix: str, label: str) -> int:
    values = _list(rows, label)
    for index, row in enumerate(values):
        item = _exact(row, {"raw_ref", "summary"}, f"{label}[{index}]")
        ref = _text(item["raw_ref"], f"{label}[{index}].raw_ref")
        suffix = ref[len(prefix):] if ref.startswith(prefix) else ""
        if not suffix.isdigit() or int(suffix) < 1:
            raise DiagnosticArtifactError(f"{label}[{index}].raw_ref 必须是 {prefix}<正整数>")
        _text(item["summary"], f"{label}[{index}].summary", 4)
    return len(values)


def validate_artifact(artifact: Any) -> dict[str, Any]:
    """Validate the closed runtime representation of one of three schemas."""
    common = {"artifact_type", "schema_version", "run_id", "input_sha256", "status", "tool"}
    if not isinstance(artifact, dict) or artifact.get("artifact_type") not in ARTIFACT_PATHS:
        raise DiagnosticArtifactError("artifact_type 必须是固定诊断类型")
    kind = artifact["artifact_type"]
    if kind in {"log_summary", "pcap_summary"}:
        expected = common | {"timeline", "key_signals", "correlations", "raw_excerpts"}
    else:
        expected = common | {"test_case_id", "conclusion", "confidence", "basis", "evidence", "next_action"}
    _exact(artifact, expected, kind)
    if artifact["schema_version"] != SCHEMA_VERSION:
        raise DiagnosticArtifactError("schema_version 必须为 1.0")
    _text(artifact["run_id"], "run_id", 1)
    if not _is_hash(artifact["input_sha256"]):
        raise DiagnosticArtifactError("input_sha256 必须是 64 位小写 SHA-256")
    if artifact["status"] not in {"complete", "partial"}:
        raise DiagnosticArtifactError("status 非法")
    _validate_tool(artifact["tool"])
    if kind in {"log_summary", "pcap_summary"}:
        prefix = "log:" if kind == "log_summary" else "pkt:"
        count = _validate_evidence_rows(artifact["timeline"], prefix, "timeline")
        count += _validate_evidence_rows(artifact["key_signals"], prefix, "key_signals")
        count += _validate_evidence_rows(artifact["raw_excerpts"], prefix, "raw_excerpts")
        correlations = _list(artifact["correlations"], "correlations")
        for index, row in enumerate(correlations):
            item = _exact(row, {"left_ref", "right_ref", "basis"}, f"correlations[{index}]")
            _text(item["left_ref"], f"correlations[{index}].left_ref")
            _text(item["right_ref"], f"correlations[{index}].right_ref")
            _text(item["basis"], f"correlations[{index}].basis", 4)
        if count == 0:
            raise DiagnosticArtifactError(f"{kind} 至少包含一条可锚定证据")
    else:
        _text(artifact["test_case_id"], "test_case_id", 3)
        if artifact["conclusion"] not in {"product_defect", "test_case_defect", "environment_issue", "undetermined"}:
            raise DiagnosticArtifactError("conclusion 非法")
        if artifact["confidence"] not in {"high", "medium", "low"}:
            raise DiagnosticArtifactError("confidence 非法")
        _text(artifact["basis"], "basis", 8)
        _text(artifact["next_action"], "next_action", 8)
        evidence = _list(artifact["evidence"], "evidence")
        if not evidence:
            raise DiagnosticArtifactError("failure_classification 至少包含一条证据")
        for index, row in enumerate(evidence):
            item = _exact(row, {"raw_ref", "summary"}, f"evidence[{index}]")
            ref = _text(item["raw_ref"], f"evidence[{index}].raw_ref")
            if not any(ref.startswith(prefix) and len(ref) > len(prefix) for prefix in ("log:", "pkt:", "spec:")):
                raise DiagnosticArtifactError("classification evidence 必须绑定 log/pkt/spec 锚点")
            _text(item["summary"], f"evidence[{index}].summary", 4)
    _draft_validate(artifact, "diagnostic-artifact.schema.json")
    return artifact


def _open_dir(parent_fd: int, name: str, label: str) -> tuple[int, tuple[int, int]]:
    try:
        fd = os.open(name, os.O_RDONLY | _DIRECTORY | _NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        raise DiagnosticArtifactError(f"无法安全打开 {label}: {exc}") from exc
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise DiagnosticArtifactError(f"{label} 不是目录")
    return fd, (info.st_dev, info.st_ino)


def _binding(parent_fd: int, name: str) -> tuple[int, int]:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise DiagnosticArtifactError(f"受管路径绑定失效: {name}: {exc}") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise DiagnosticArtifactError(f"受管祖先不是物理目录: {name}")
    return info.st_dev, info.st_ino


def _verify_tree(run: _ManagedRun) -> None:
    try:
        root = run.root_path.stat()
    except OSError as exc:
        raise DiagnosticArtifactError(f"项目根绑定失效: {exc}") from exc
    first = os.fstat(run.fds[0])
    if (root.st_dev, root.st_ino) != (first.st_dev, first.st_ino):
        raise DiagnosticArtifactError("项目根在事务期间被替换")
    for parent, name, identity in run.bindings:
        if _binding(parent, name) != identity:
            raise DiagnosticArtifactError(f"受管目录在事务期间被替换: {name}")
    lock = os.stat(LOCK_NAME, dir_fd=run.internal_fd, follow_symlinks=False)
    if not stat.S_ISREG(lock.st_mode) or lock.st_nlink != 1 \
            or (lock.st_dev, lock.st_ino) != run.lock_identity:
        raise DiagnosticArtifactError("诊断事务锁在事务期间被替换")


def _open_managed(root: Path, run_id: str) -> _ManagedRun:
    if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
        raise DiagnosticArtifactError("run_id 非法")
    supplied_root = Path(root).expanduser()
    if supplied_root.is_symlink():
        raise DiagnosticArtifactError("项目根不得是符号链接")
    root_path = supplied_root.resolve(strict=True)
    fds: list[int] = []
    bindings: list[tuple[int, str, tuple[int, int]]] = []
    try:
        root_fd = os.open(root_path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        fds.append(root_fd)
        parent = root_fd
        for name, label in (("pangea-data", "data workspace"), ("runs", "runs"),
                            (run_id, "Run"), ("internal", "Run internal")):
            child, identity = _open_dir(parent, name, label)
            fds.append(child)
            bindings.append((parent, name, identity))
            parent = child
        run_fd, internal_fd = fds[-2], fds[-1]
        manifest = _read_json_file(run_fd, "manifest.json", "Run manifest", allow_missing=False)
        if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
            raise DiagnosticArtifactError("Run manifest 无效或 run_id 不匹配")
        try:
            os.mkdir("diagnostics", 0o700, dir_fd=internal_fd)
            os.fsync(internal_fd)
        except FileExistsError:
            pass
        diagnostics_fd, identity = _open_dir(internal_fd, "diagnostics", "diagnostics")
        fds.append(diagnostics_fd)
        bindings.append((internal_fd, "diagnostics", identity))
        flags = os.O_RDWR | os.O_CREAT | _NOFOLLOW
        lock_fd = -1
        # macOS can transiently report ENOENT when concurrent O_CREAT|O_NOFOLLOW
        # opens race on the same previously absent name.  Retry the exact
        # dirfd-bound operation; no path resolution or fallback is permitted.
        for _attempt in range(4):
            try:
                lock_fd = os.open(LOCK_NAME, flags, 0o600, dir_fd=internal_fd)
                break
            except FileNotFoundError:
                continue
        if lock_fd < 0:
            raise DiagnosticArtifactError("无法创建或打开诊断事务锁")
        fds.append(lock_fd)
        lock_info = os.fstat(lock_fd)
        if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
            raise DiagnosticArtifactError("诊断锁必须是单链接普通文件")
        os.fsync(lock_fd)
        os.fsync(internal_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        managed = _ManagedRun(root_path, fds, bindings, run_fd, internal_fd, diagnostics_fd, lock_fd,
                              (lock_info.st_dev, lock_info.st_ino))
        _verify_tree(managed)
        return managed
    except BaseException:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _read_regular(fd: int, name: str, label: str) -> bytes:
    # O_NONBLOCK keeps a FIFO read from waiting before fstat rejects it.
    flags = os.O_RDONLY | os.O_NONBLOCK | _NOFOLLOW
    try:
        child = os.open(name, flags, dir_fd=fd)
    except OSError as exc:
        raise DiagnosticArtifactError(f"无法安全读取 {label}: {exc}") from exc
    try:
        info = os.fstat(child)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise DiagnosticArtifactError(f"{label} 必须是单链接普通文件")
        chunks: list[bytes] = []
        while True:
            block = os.read(child, 1024 * 1024)
            if not block:
                return b"".join(chunks)
            chunks.append(block)
    finally:
        os.close(child)


def _read_json_file(fd: int, name: str, label: str, *, allow_missing: bool) -> Any:
    try:
        raw = _read_regular(fd, name, label)
    except DiagnosticArtifactError as exc:
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            if allow_missing:
                return None
        raise exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiagnosticArtifactError(f"{label} JSON 损坏: {exc}") from exc


def _existing_kind(fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _temp_write(fd: int, prefix: str, content: bytes) -> tuple[str, tuple[int, int]]:
    name = f".{prefix}-{secrets.token_hex(16)}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW
    try:
        child = os.open(name, flags, 0o600, dir_fd=fd)
    except OSError as exc:
        raise DiagnosticArtifactError(f"无法创建私有事务文件: {exc}") from exc
    identity = os.fstat(child)
    try:
        view = memoryview(content)
        while view:
            written = os.write(child, view)
            if written <= 0:
                raise DiagnosticArtifactError("事务文件短写")
            view = view[written:]
        os.fsync(child)
    except BaseException as exc:
        os.close(child)
        try:
            os.unlink(name, dir_fd=fd)
            os.fsync(fd)
        except FileNotFoundError:
            pass
        except OSError as cleanup_exc:
            raise DiagnosticArtifactError(f"事务临时文件清理失败: {cleanup_exc}") from cleanup_exc
        raise exc if isinstance(exc, DiagnosticArtifactError) else DiagnosticArtifactError(f"事务文件写入失败: {exc}") from exc
    os.close(child)
    return name, (identity.st_dev, identity.st_ino)


def _rename_noreplace(fd: int, source: str, destination: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    source_b, destination_b = os.fsencode(source), os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renameatx_np"):
        result = libc.renameatx_np(fd, source_b, fd, destination_b, 0x00000004)
    elif hasattr(libc, "renameat2"):
        result = libc.renameat2(fd, source_b, fd, destination_b, 1)
    else:
        raise DiagnosticArtifactError("平台缺少原子 no-replace rename")
    if result != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise DiagnosticArtifactError(f"拒绝覆盖既有固定工件: {destination}")
        raise DiagnosticArtifactError(f"原子发布失败: {os.strerror(error)}")


def _publish_new(fd: int, name: str, content: bytes, prefix: str,
                 verify: Callable[[], None]) -> None:
    existing = _existing_kind(fd, name)
    if existing is not None:
        if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1:
            raise DiagnosticArtifactError(f"既有 {name} 不是单链接普通文件")
        if _read_regular(fd, name, name) == content:
            return
        raise DiagnosticArtifactError(f"既有固定工件冲突: {name}")
    temp, _identity = _temp_write(fd, prefix, content)
    verify()
    try:
        _rename_noreplace(fd, temp, name)
    except BaseException as exc:
        # An injected/OS error may be reported after the rename took effect.
        current = _existing_kind(fd, name)
        if current is not None and stat.S_ISREG(current.st_mode) and current.st_nlink == 1 \
                and _read_regular(fd, name, name) == content:
            pass
        else:
            raise exc if isinstance(exc, DiagnosticArtifactError) else DiagnosticArtifactError(f"发布失败: {exc}") from exc
    os.fsync(fd)
    verify()
    if _read_regular(fd, name, name) != content:
        raise DiagnosticArtifactError(f"发布后内容不一致: {name}")


def _replace(fd: int, name: str, content: bytes, prefix: str,
             verify: Callable[[], None]) -> None:
    existing = _existing_kind(fd, name)
    if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1):
        raise DiagnosticArtifactError(f"拒绝替换非单链接普通文件: {name}")
    temp, _identity = _temp_write(fd, prefix, content)
    verify()
    try:
        os.replace(temp, name, src_dir_fd=fd, dst_dir_fd=fd)
    except BaseException as exc:
        current = _existing_kind(fd, name)
        if current is not None and stat.S_ISREG(current.st_mode) and current.st_nlink == 1 \
                and _read_regular(fd, name, name) == content:
            pass
        else:
            raise exc if isinstance(exc, DiagnosticArtifactError) else DiagnosticArtifactError(f"原子替换失败: {exc}") from exc
    os.fsync(fd)
    verify()
    if _read_regular(fd, name, name) != content:
        raise DiagnosticArtifactError(f"替换后内容不一致: {name}")


def _validate_index(value: Any, run_id: str) -> dict[str, Any]:
    item = _exact(value, {"artifact_type", "schema_version", "run_id", "state", "artifacts"}, "index")
    if item["artifact_type"] != "diagnostic_artifact_index" or item["schema_version"] != SCHEMA_VERSION \
            or item["run_id"] != run_id or item["state"] != "committed" or not isinstance(item["artifacts"], dict):
        raise DiagnosticArtifactError("诊断工件索引头无效")
    if not set(item["artifacts"]).issubset(ARTIFACT_PATHS):
        raise DiagnosticArtifactError("诊断工件索引含未知类型")
    for kind, record in item["artifacts"].items():
        expected = {"path", "input_sha256", "body_sha256", "content_sha256", "state"}
        _exact(record, expected, f"index.{kind}")
        if record["path"] != f"internal/diagnostics/{ARTIFACT_PATHS[kind]}" or record["state"] != "committed" \
                or not all(_is_hash(record[key]) for key in HASH_KEYS):
            raise DiagnosticArtifactError(f"index.{kind} 绑定无效")
    _draft_validate(item, "diagnostic-artifact-index.schema.json")
    return item


def _load_index(run: _ManagedRun, run_id: str) -> dict[str, Any]:
    value = _read_json_file(run.internal_fd, INDEX_NAME, "诊断工件索引", allow_missing=True)
    if value is None:
        return {"artifact_type": "diagnostic_artifact_index", "schema_version": SCHEMA_VERSION,
                "run_id": run_id, "state": "committed", "artifacts": {}}
    index = _validate_index(value, run_id)
    for kind, record in index["artifacts"].items():
        raw = _read_regular(run.diagnostics_fd, ARTIFACT_PATHS[kind], f"索引工件 {kind}")
        if _sha(raw) != record["content_sha256"]:
            raise DiagnosticArtifactError(f"索引工件内容哈希不一致: {kind}")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DiagnosticArtifactError(f"索引工件 JSON 损坏: {kind}") from exc
        validate_artifact(body)
        if body["run_id"] != run_id or body["artifact_type"] != kind \
                or body["input_sha256"] != record["input_sha256"] \
                or _sha(_canonical(body)) != record["body_sha256"]:
            raise DiagnosticArtifactError(f"索引工件 body 绑定不一致: {kind}")
    return index


def _validate_journal(value: Any, run_id: str) -> dict[str, Any]:
    expected = {"artifact_type", "schema_version", "run_id", "transaction_id", "state",
                "kind", "body", "record"}
    item = _exact(value, expected, "journal")
    if item["artifact_type"] != "diagnostic_artifact_transaction" or item["schema_version"] != SCHEMA_VERSION \
            or item["run_id"] != run_id or item["state"] not in {"prepared", "artifact_published", "committed"} \
            or item["kind"] not in ARTIFACT_PATHS or not isinstance(item["transaction_id"], str) \
            or len(item["transaction_id"]) != 32 or any(char not in _HEX for char in item["transaction_id"]):
        raise DiagnosticArtifactError("事务 journal 头无效")
    body = validate_artifact(item["body"])
    if body["artifact_type"] != item["kind"] or body["run_id"] != run_id:
        raise DiagnosticArtifactError("journal body 绑定无效")
    record = _exact(item["record"], {"path", "input_sha256", "body_sha256", "content_sha256", "state"}, "journal.record")
    content = _pretty(body)
    if record != {"path": f"internal/diagnostics/{ARTIFACT_PATHS[item['kind']]}",
                  "input_sha256": body["input_sha256"], "body_sha256": _sha(_canonical(body)),
                  "content_sha256": _sha(content), "state": "committed"}:
        raise DiagnosticArtifactError("journal record 与 body 不一致")
    return item


def _write_journal(run: _ManagedRun, journal: dict[str, Any]) -> None:
    content = _pretty(journal)
    if _existing_kind(run.internal_fd, JOURNAL_NAME) is None:
        _publish_new(run.internal_fd, JOURNAL_NAME, content, "journal", lambda: _verify_tree(run))
    else:
        _replace(run.internal_fd, JOURNAL_NAME, content, "journal", lambda: _verify_tree(run))


def _finish(run: _ManagedRun, run_id: str, journal: dict[str, Any]) -> dict[str, str]:
    kind, body, record = journal["kind"], journal["body"], journal["record"]
    target_name = ARTIFACT_PATHS[kind]
    content = _pretty(body)
    _publish_new(run.diagnostics_fd, target_name, content, kind, lambda: _verify_tree(run))
    if journal["state"] == "prepared":
        journal = dict(journal, state="artifact_published")
        _write_journal(run, journal)
    index = _load_index(run, run_id)
    prior = index["artifacts"].get(kind)
    if prior is not None and prior != record:
        raise DiagnosticArtifactError(f"索引中已有冲突工件: {kind}")
    index["artifacts"][kind] = record
    _replace(run.internal_fd, INDEX_NAME, _pretty(index), "index", lambda: _verify_tree(run))
    journal = dict(journal, state="committed")
    _write_journal(run, journal)
    _verify_tree(run)
    # Re-read both sides after the final durable state transition.
    committed = _load_index(run, run_id)["artifacts"].get(kind)
    if committed != record or _sha(_read_regular(run.diagnostics_fd, target_name, target_name)) != record["content_sha256"]:
        raise DiagnosticArtifactError("事务提交后工件与索引不一致")
    return dict(record)


def _recover(run: _ManagedRun, run_id: str) -> dict[str, str] | None:
    value = _read_json_file(run.internal_fd, JOURNAL_NAME, "诊断事务 journal", allow_missing=True)
    if value is None:
        return None
    journal = _validate_journal(value, run_id)
    return _finish(run, run_id, journal)


def write_artifact(root: Path, run_id: str, artifact: dict[str, Any]) -> dict[str, str]:
    """Validate and transactionally commit one fixed diagnostic artifact."""
    run: _ManagedRun | None = None
    try:
        run = _open_managed(root, run_id)
        recovered = _recover(run, run_id)
        body = dict(artifact)
        body["run_id"] = run_id
        body["schema_version"] = SCHEMA_VERSION
        validate_artifact(body)
        kind = body["artifact_type"]
        content = _pretty(body)
        record = {"path": f"internal/diagnostics/{ARTIFACT_PATHS[kind]}",
                  "input_sha256": body["input_sha256"], "body_sha256": _sha(_canonical(body)),
                  "content_sha256": _sha(content), "state": "committed"}
        if recovered is not None:
            if recovered == record:
                return recovered
            # A committed old journal is a durable receipt, not an active txn.
        index = _load_index(run, run_id)
        prior = index["artifacts"].get(kind)
        if prior is not None:
            if prior == record:
                return dict(prior)
            raise DiagnosticArtifactError(f"固定诊断工件已存在且绑定冲突: {kind}")
        # A target without a journal/index provenance must never be adopted,
        # even if an untrusted writer guessed byte-identical content.
        if _existing_kind(run.diagnostics_fd, ARTIFACT_PATHS[kind]) is not None:
            raise DiagnosticArtifactError(f"拒绝采用无事务来源的既有固定工件: {kind}")
        journal = {"artifact_type": "diagnostic_artifact_transaction", "schema_version": SCHEMA_VERSION,
                   "run_id": run_id, "transaction_id": secrets.token_hex(16), "state": "prepared",
                   "kind": kind, "body": body, "record": record}
        _write_journal(run, journal)
        return _finish(run, run_id, journal)
    except DiagnosticArtifactError:
        raise
    except BaseException as exc:
        raise DiagnosticArtifactError(f"诊断工件事务失败: {exc}") from exc
    finally:
        if run is not None:
            run.close()


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--root", required=True)
    command.add_argument("--run-id", required=True)
    command.add_argument("--file", required=True)
    return command


def main() -> int:
    args = parser().parse_args()
    try:
        candidate = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise DiagnosticArtifactError("工件 JSON 根必须是对象")
        print(json.dumps(write_artifact(Path(args.root), args.run_id, candidate), ensure_ascii=False))
        return 0
    except (DiagnosticArtifactError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
