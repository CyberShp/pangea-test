"""Trusted, reproducible inventory of scoped C/C++ source evidence.

Regex signals are discovery hints, not the coverage denominator.  Fixed source
chunks cover every byte of every scoped translation unit, including empty files.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1.0"
CHUNK_LINES = 200
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_SOURCE = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}
_FUNC = re.compile(r"^\s*(?:[\w:*<>]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|$)")
_DECL = re.compile(r"^\s*(?:[\w:*<>]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*;")
_RULES = (
    ("registration", r"\b(register|dispatch|callback|handler)\b", "registration_dispatch"),
    ("branch", r"\b(if|switch|case|while|for)\b", "control_branch"),
    ("state", r"\b(state|connect|disconnect|recover|reset|ready)\b", "state_transition"),
    ("resource", r"\b(malloc|calloc|realloc|free|alloc|release|close|open|put)\b", "resource_lifecycle"),
    ("concurrency", r"\b(lock|mutex|atomic|thread|poller|completion)\b", "concurrency_signal"),
    ("error", r"\b(errno|error|fail|return\s*-)", "error_signal"),
)
_CAPS = {
    "source_chunk": ["源文件覆盖"], "entrypoint": ["功能与状态"],
    "registration": ["功能与状态"], "branch": ["功能与状态"],
    "state": ["功能与状态"], "resource": ["资源与规格"],
    "concurrency": ["并发与异常"], "error": ["错误与异常"],
}
_SKILLS = {
    "storage-spdk", "storage-nvmeof", "storage-iscsi", "storage-nvme-cli",
    "storage-destructive-cli", "storage-resource-recovery",
}


class InventoryError(ValueError):
    pass


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _safe_root(root: str | Path) -> Path:
    raw = Path(root).absolute()
    try:
        mode = raw.lstat().st_mode
    except OSError as exc:
        raise InventoryError("trusted root is missing") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise InventoryError("trusted root must be a real directory")
    return raw.resolve()


def _safe_file(root: Path, rel: str) -> Path:
    current = root
    for part in Path(rel).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            raise InventoryError("scope path is missing") from exc
        if stat.S_ISLNK(mode):
            raise InventoryError("scope crosses symlink")
    resolved = current.resolve()
    if not stat.S_ISREG(mode) or root not in resolved.parents:
        raise InventoryError("scope path is not a regular in-root file")
    return current


def _default_scope(root: Path) -> list[str]:
    found: list[str] = []
    for directory, names, files in os.walk(root, followlinks=False):
        base = Path(directory)
        # os.walk lists symlinked directories even when it will not descend.
        names[:] = [name for name in names if not (base / name).is_symlink()]
        for name in files:
            path = base / name
            if not path.is_symlink() and path.suffix.lower() in _SOURCE:
                found.append(path.relative_to(root).as_posix())
    return found


def _scope(root: Path, scope: list[str] | None) -> list[str]:
    if scope is None:
        scope = _default_scope(root)
    if not isinstance(scope, list) or not scope:
        raise InventoryError("scope is empty")
    out: list[str] = []
    for rel in scope:
        if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
                or ".." in Path(rel).parts or Path(rel).as_posix() != rel):
            raise InventoryError("scope must be normalized relative paths")
        path = _safe_file(root, rel)
        if path.suffix.lower() not in _SOURCE:
            raise InventoryError("scope contains non-source path")
        if rel in out:
            raise InventoryError("duplicate scope")
        out.append(rel)
    return sorted(out)


def snapshot_sha256(root: Path, scope: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in scope:
        digest.update(rel.encode() + b"\0" + _safe_file(root, rel).read_bytes() + b"\0")
    return digest.hexdigest()


def _triggers(repo: str, rel: str, text: str, kind: str) -> list[str]:
    low = (repo + " " + rel + " " + text).lower().replace("_", "-")
    result: list[str] = []
    if "spdk" in low:
        result.append("storage-spdk")
    if any(value in low for value in ("nvmf", "nvme-tcp", "rdma")):
        result.append("storage-nvmeof")
    if "iscsi" in low:
        result.append("storage-iscsi")
    if "nvme-cli" in low or ("nvme" in low and any(value in low for value in (" cli", "command", "admin"))):
        result.append("storage-nvme-cli")
    if any(value in low for value in ("format", "sanitize", "delete-ns", "destroy", "erase")):
        result.append("storage-destructive-cli")
    if kind == "resource":
        result.append("storage-resource-recovery")
    return sorted(set(result))


def _row(repo: str, commit: str, rel: str, start: int, end: int, symbol: str,
         kind: str, rule: str, text: str) -> dict[str, Any]:
    seed = f"{repo}\0{commit}\0{rel}\0{start}\0{end}\0{symbol}\0{kind}\0{rule}"
    return {
        "inventory_id": "INV-" + sha256_text(seed)[:16], "repository": repo,
        "commit": commit, "path": rel, "line_start": start, "line_end": end,
        "symbol": symbol, "kind": kind, "excerpt_sha256": sha256_text(text),
        "discovery_rule": rule, "applicable_capabilities": list(_CAPS[kind]),
        "storage_skill_triggers": _triggers(repo, rel, text, kind),
    }


def _derive(root: Path, repo: str, commit: str, scope: list[str]) -> list[dict[str, Any]]:
    public: set[str] = set()
    for rel in scope:
        if Path(rel).suffix.lower() in {".h", ".hpp"}:
            for line in _safe_file(root, rel).read_text(errors="replace").splitlines():
                match = _DECL.match(line)
                if match:
                    public.add(match.group("name"))
    rows: list[dict[str, Any]] = []
    for rel in scope:
        lines = _safe_file(root, rel).read_text(errors="replace").splitlines() or [""]
        for start in range(1, len(lines) + 1, CHUNK_LINES):
            end = min(len(lines), start + CHUNK_LINES - 1)
            rows.append(_row(repo, commit, rel, start, end, "<source-chunk>",
                             "source_chunk", "fixed_source_chunk", "\n".join(lines[start - 1:end])))
        for number, text in enumerate(lines, 1):
            match = _FUNC.match(text)
            if match and (match.group("name") in public or match.group("name") == "main"):
                rows.append(_row(repo, commit, rel, number, number, match.group("name"),
                                 "entrypoint", "public_api_definition", text))
            for kind, pattern, rule in _RULES:
                if re.search(pattern, text, re.I):
                    rows.append(_row(repo, commit, rel, number, number,
                                     match.group("name") if match else "<line>", kind, rule, text))
    return sorted(rows, key=lambda row: row["inventory_id"])


def _valid_repository(value: Any) -> bool:
    return isinstance(value, str) and bool(_REPOSITORY.fullmatch(value))


def build(snapshot_root: str | Path, repository: str, commit: str,
          scope: list[str] | None = None) -> dict[str, Any]:
    root = _safe_root(snapshot_root)
    if not _valid_repository(repository) or not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise InventoryError("invalid repository or commit")
    selected = _scope(root, scope)
    result = {
        "artifact_type": "source_inventory", "schema_version": SCHEMA_VERSION,
        "repository": repository, "commit": commit,
        "snapshot_sha256": snapshot_sha256(root, selected), "scope": selected,
        "items": _derive(root, repository, commit, selected),
    }
    validate(result, root)
    return result


def validate(inventory: dict[str, Any], trusted_root: str | Path) -> None:
    required = {"artifact_type", "schema_version", "repository", "commit", "snapshot_sha256", "scope", "items"}
    if (not isinstance(inventory, dict) or set(inventory) != required
            or inventory.get("artifact_type") != "source_inventory"
            or inventory.get("schema_version") != SCHEMA_VERSION
            or not _valid_repository(inventory.get("repository"))
            or not isinstance(inventory.get("commit"), str)
            or not _COMMIT.fullmatch(inventory["commit"])
            or not isinstance(inventory.get("snapshot_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", inventory["snapshot_sha256"])):
        raise InventoryError("invalid inventory envelope")
    root = _safe_root(trusted_root)
    scope = _scope(root, inventory["scope"])
    if inventory["scope"] != scope or inventory["snapshot_sha256"] != snapshot_sha256(root, scope):
        raise InventoryError("scope or snapshot hash mismatch")
    expected = _derive(root, inventory["repository"], inventory["commit"], scope)
    if inventory.get("items") != expected:
        raise InventoryError("inventory is not the complete trusted derivation")
