"""Deterministic material, discovery, MR, and source-evidence provenance."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from runtime import data_runtime, repository_runtime


class EvidenceRuntimeError(RuntimeError):
    pass


DISCOVERY_KINDS = {
    "entrypoint", "registration", "flow", "branch", "state", "resource",
    "concurrency", "error_path", "protocol_operation", "limit", "coverage_gap",
}
COMPLETE_DISCOVERY_KINDS = {
    "entrypoint", "registration", "flow", "branch", "state", "resource", "concurrency", "error_path",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_posix(value: str, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceRuntimeError(f"{label} 不能为空")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise EvidenceRuntimeError(f"{label} 必须是规范 Run/快照相对 POSIX 路径: {value}")
    return value


def _under(path: Path, parent: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(parent.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise EvidenceRuntimeError(f"{label} 越界或不可解析: {path}") from exc
    if path.is_symlink():
        raise EvidenceRuntimeError(f"{label} 不得是符号链接: {path}")
    return resolved


def _line_slice(path: Path, start: int, end: int, label: str) -> bytes:
    if isinstance(start, bool) or isinstance(end, bool) or start < 1 or end < start:
        raise EvidenceRuntimeError(f"{label} 行号范围无效: {start}-{end}")
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    if end > len(lines):
        raise EvidenceRuntimeError(f"{label} 行号越界: {end} > {len(lines)}")
    return b"".join(lines[start - 1:end])


def _catalog_records(workspace: Path) -> tuple[Path, list[dict[str, Any]]]:
    path = workspace / "library" / data_runtime.CATALOG_NAME
    return path, data_runtime._read_jsonl(path)


def _normalize_input_ref(value: str, workspace: Path) -> str:
    normalized = value.replace("\\", "/")
    for prefix in ("pangea-data/inbox/", "inbox/"):
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to((workspace / "inbox").resolve()).as_posix()
        except (OSError, ValueError):
            pass
    return normalized


def source_evidence_ids(payload: dict[str, Any]) -> set[str]:
    return {str(item["evidence_id"]) for item in payload.get("source_evidence", []) if isinstance(item, dict)}


def reference_ids(payload: dict[str, Any]) -> set[str]:
    result = source_evidence_ids(payload)
    result |= {str(item["material_id"]) for item in payload.get("material_selection", []) if isinstance(item, dict)}
    result |= {str(item["discovery_id"]) for item in payload.get("discovery", []) if isinstance(item, dict)}
    if isinstance(payload.get("mr_facts"), dict):
        result.add("MR-FACTS")
    return result



def _parse_unified_diff(data: bytes) -> list[dict[str, Any]]:
    """Parse canonical Git unified-diff file paths and hunk headers."""
    text = data.decode("utf-8", errors="replace")
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        match = re.match(r"^diff --git a/(.+) b/(.+)$", raw)
        if match:
            if current is not None:
                files.append(current)
            current = {"path": match.group(2), "hunks": []}
            continue
        if current is None:
            continue
        if raw.startswith("+++ b/"):
            current["path"] = raw[6:]
            continue
        hunk = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", raw)
        if hunk:
            current["hunks"].append({
                "old_start": int(hunk.group(1)), "old_count": int(hunk.group(2) or "1"),
                "new_start": int(hunk.group(3)), "new_count": int(hunk.group(4) or "1"),
            })
    if current is not None:
        files.append(current)
    if not files:
        raise EvidenceRuntimeError("固定 MR diff 不包含可解析的 diff --git 文件记录")
    for item in files:
        _safe_posix(item["path"], "MR diff file path")
    return files

def validate_provenance(payload: dict[str, Any], root: Path, run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    if payload.get("run_id") != run_dir.name:
        raise EvidenceRuntimeError("evidence provenance run_id 与当前 Run 不一致")
    if payload.get("source_commits") != contract.get("repository_commits"):
        raise EvidenceRuntimeError("evidence provenance source_commits 与任务契约不一致")
    workspace = data_runtime.ensure_layout(root)

    # Material selection is bound to the content-addressed catalog and exact Markdown line ranges.
    material_items = payload.get("material_selection", [])
    contract_refs = {_normalize_input_ref(value, workspace) for value in contract.get("input_refs", [])}
    material_refs = {item.get("source_ref") for item in material_items if isinstance(item, dict)}
    missing_refs = sorted(contract_refs - material_refs)
    if missing_refs:
        raise EvidenceRuntimeError(
            "任务契约 input_refs 未进入材料选择账本；请先导入 pangea-data/inbox: " + ", ".join(missing_refs)
        )
    record_path = run_dir / "internal" / "contract-record.json"
    confirmation = data_runtime.read_json(record_path, {}).get("confirmation", {}) if record_path.is_file() else {}
    materials_status = confirmation.get("materials_status") if isinstance(confirmation, dict) else None
    if materials_status == "confirmed_none" and (contract_refs or material_items):
        raise EvidenceRuntimeError("任务契约确认无补充材料，但 input_refs/material_selection 非空")
    if materials_status == "provided" and not contract_refs:
        raise EvidenceRuntimeError("确认记录声明 provided，但任务契约 input_refs 为空")

    catalog_path, catalog_records = _catalog_records(workspace)
    catalog_by_source = {item.get("source_path"): item for item in catalog_records if isinstance(item, dict)}
    catalog_binding = payload.get("catalog")
    if material_items:
        if not catalog_path.is_file() or catalog_path.is_symlink():
            raise EvidenceRuntimeError("材料选择非空但 library/catalog.jsonl 不存在")
        expected_catalog = {"path": "library/catalog.jsonl", "sha256": sha256_file(catalog_path)}
        if catalog_binding != expected_catalog:
            raise EvidenceRuntimeError("材料账本未精确绑定当前 catalog SHA-256")
    elif catalog_binding is not None:
        if not catalog_path.is_file() or catalog_binding != {"path": "library/catalog.jsonl", "sha256": sha256_file(catalog_path)}:
            raise EvidenceRuntimeError("空材料账本携带了过期 catalog binding")

    material_ids: set[str] = set()
    seen_sources: set[str] = set()
    for item in material_items:
        material_id = item["material_id"]
        source_ref = _safe_posix(item["source_ref"], f"材料 {material_id} source_ref")
        if material_id in material_ids or source_ref in seen_sources:
            raise EvidenceRuntimeError(f"材料 ID 或 source_ref 重复: {material_id}/{source_ref}")
        material_ids.add(material_id); seen_sources.add(source_ref)
        record = catalog_by_source.get(source_ref)
        if not isinstance(record, dict) or record.get("sha256") != item["source_sha256"]:
            raise EvidenceRuntimeError(f"材料 {material_id} 未绑定当前 catalog source_path/SHA-256")
        decision = item["decision"]
        anchors = item["consumed_anchors"]
        if decision == "selected" and source_ref not in contract_refs:
            raise EvidenceRuntimeError(f"材料 {material_id} 被选择但未声明在任务契约 input_refs")
        if decision != "selected":
            if anchors:
                raise EvidenceRuntimeError(f"未选择材料 {material_id} 不得声明 consumed_anchors")
            continue
        if record.get("conversion_status") != "converted" or not isinstance(record.get("markdown_path"), str):
            raise EvidenceRuntimeError(f"已选择材料 {material_id} 尚未成功转换为 Markdown")
        if item.get("markdown_path") != record["markdown_path"]:
            raise EvidenceRuntimeError(f"材料 {material_id} markdown_path 与 catalog 不一致")
        markdown = _under(workspace / record["markdown_path"], workspace, f"材料 {material_id} Markdown")
        if not markdown.is_file() or item.get("markdown_sha256") != sha256_file(markdown):
            raise EvidenceRuntimeError(f"材料 {material_id} Markdown SHA-256 不匹配")
        if not anchors:
            raise EvidenceRuntimeError(f"已选择材料 {material_id} 必须记录实际消费锚点")
        for index, anchor in enumerate(anchors, 1):
            excerpt = _line_slice(markdown, anchor["start_line"], anchor["end_line"], f"材料 {material_id} 锚点 {index}")
            if hashlib.sha256(excerpt).hexdigest() != anchor["excerpt_sha256"]:
                raise EvidenceRuntimeError(f"材料 {material_id} 锚点 {index} excerpt SHA-256 不匹配")

    # Source claims are verified against immutable Run snapshots, exact files, exact lines, and hashes.
    try:
        snapshot_status = repository_runtime.verify_snapshots_against_source(root, run_dir.name)
    except repository_runtime.RepositoryRuntimeError as exc:
        raise EvidenceRuntimeError(f"源码快照验证失败: {exc}") from exc
    snapshots = {item["repository"]: item for item in snapshot_status.get("snapshots", []) if isinstance(item, dict)}
    expected_commits = contract.get("repository_commits", {})
    evidence_ids: set[str] = set()
    evidence_by_id: dict[str, dict[str, Any]] = {}
    for item in payload.get("source_evidence", []):
        evidence_id = item["evidence_id"]
        if evidence_id in evidence_ids:
            raise EvidenceRuntimeError(f"source evidence ID 重复: {evidence_id}")
        evidence_ids.add(evidence_id); evidence_by_id[evidence_id] = item
        repository = item["repository"]
        binding = snapshots.get(repository)
        if not isinstance(binding, dict):
            raise EvidenceRuntimeError(f"证据 {evidence_id} 引用不可用快照仓库: {repository}")
        if item["commit_sha"] != expected_commits.get(repository) or binding["commit_sha"] != item["commit_sha"]:
            raise EvidenceRuntimeError(f"证据 {evidence_id} commit 与任务契约/快照不一致")
        if item["snapshot_id"] != binding["snapshot_id"]:
            raise EvidenceRuntimeError(f"证据 {evidence_id} snapshot_id 不一致")
        relative = _safe_posix(item["path"], f"证据 {evidence_id} path")
        snapshot_dir = Path(binding["snapshot_dir"])
        source = _under(snapshot_dir / relative, snapshot_dir, f"证据 {evidence_id} 源文件")
        if not source.is_file() or item["file_sha256"] != sha256_file(source):
            raise EvidenceRuntimeError(f"证据 {evidence_id} 文件不存在或 SHA-256 不匹配")
        excerpt = _line_slice(source, item["line_start"], item["line_end"], f"证据 {evidence_id}")
        if hashlib.sha256(excerpt).hexdigest() != item["excerpt_sha256"]:
            raise EvidenceRuntimeError(f"证据 {evidence_id} 行范围摘要不匹配")
        symbol = item.get("symbol")
        if isinstance(symbol, str) and symbol and symbol.encode("utf-8") not in excerpt:
            raise EvidenceRuntimeError(f"证据 {evidence_id} symbol 未出现在声明行范围内")

    # Discovery breadth records what was searched, including proved no-match and blocked scope.
    discovery_ids: set[str] = set()
    kinds: set[str] = set()
    repositories_seen: set[str] = set()
    for item in payload.get("discovery", []):
        discovery_id = item["discovery_id"]
        if discovery_id in discovery_ids:
            raise EvidenceRuntimeError(f"discovery ID 重复: {discovery_id}")
        discovery_ids.add(discovery_id)
        kind = item["target_kind"]
        if kind not in DISCOVERY_KINDS:
            raise EvidenceRuntimeError(f"discovery target_kind 非法: {kind}")
        kinds.add(kind)
        repository = item["repository"]
        repositories_seen.add(repository)
        if item["commit_sha"] != expected_commits.get(repository):
            raise EvidenceRuntimeError(f"discovery {discovery_id} commit 与任务契约不一致")
        unknown = set(item["evidence_ids"]) - evidence_ids
        if unknown:
            raise EvidenceRuntimeError(f"discovery {discovery_id} 引用未知 source evidence: {sorted(unknown)}")
        for evidence_id in item["evidence_ids"]:
            if evidence_by_id[evidence_id]["repository"] != repository:
                raise EvidenceRuntimeError(f"discovery {discovery_id} 跨仓引用了未声明归属的证据 {evidence_id}")
        disposition = item["disposition"]
        if disposition in {"expanded", "merged"} and (not item["candidate_ids"] or not item["evidence_ids"]):
            raise EvidenceRuntimeError(f"discovery {discovery_id} 已展开但缺少候选或证据")
        if disposition == "no_match" and not item["evidence_ids"]:
            raise EvidenceRuntimeError(f"discovery {discovery_id} no_match 必须有已搜索证据")
        if disposition in {"blocked", "out_of_scope"} and not item["limitations"]:
            raise EvidenceRuntimeError(f"discovery {discovery_id} {disposition} 必须记录限制")
    missing_repositories = sorted(set(contract["repositories"]) - repositories_seen)
    if missing_repositories:
        raise EvidenceRuntimeError("discovery ledger 未处置契约仓库: " + ", ".join(missing_repositories))
    if contract.get("mode") == "module_analysis" and contract.get("analysis_depth") == "complete":
        missing_kinds = sorted(COMPLETE_DISCOVERY_KINDS - kinds)
        if missing_kinds:
            raise EvidenceRuntimeError("完整型 discovery ledger 缺少搜索维度: " + ", ".join(missing_kinds))

    mr_facts = payload.get("mr_facts")
    if contract.get("mode") == "mr_regression":
        if not isinstance(mr_facts, dict):
            raise EvidenceRuntimeError("MR 回归必须持久化 mr_facts")
        if mr_facts.get("mr_url") != contract.get("mr_url"):
            raise EvidenceRuntimeError("mr_facts.mr_url 与任务契约不一致")
        if mr_facts.get("resolved_commits") != expected_commits:
            raise EvidenceRuntimeError("mr_facts.resolved_commits 与任务契约不一致")
        diff_binding = mr_facts.get("diff_artifact")
        expected_relative = "internal/mr.diff"
        if not isinstance(diff_binding, dict) or diff_binding.get("path") != expected_relative:
            raise EvidenceRuntimeError("mr_facts 缺少固定 internal/mr.diff binding")
        diff_path = _under(run_dir / expected_relative, run_dir, "固定 MR diff")
        if not diff_path.is_file():
            raise EvidenceRuntimeError("固定 MR diff 不是普通文件")
        digest = sha256_file(diff_path)
        if diff_binding.get("sha256") != digest or mr_facts.get("diff_sha256") != digest:
            raise EvidenceRuntimeError("mr_facts diff SHA-256 与固定 MR diff 不一致")
        parsed_files = _parse_unified_diff(diff_path.read_bytes())
        declared_files = mr_facts.get("changed_files", [])
        for changed in declared_files:
            _safe_posix(changed["path"], "MR changed file path")
        if declared_files != parsed_files:
            raise EvidenceRuntimeError("mr_facts changed_files/hunks 与固定 MR diff 不一致")
    elif mr_facts is not None:
        raise EvidenceRuntimeError("模块分析不得伪造 mr_facts")

    return payload
