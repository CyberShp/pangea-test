from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


evidence_runtime = r'''"""Deterministic material, discovery, MR, and source-evidence provenance."""
from __future__ import annotations

import hashlib
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
        for changed in mr_facts.get("changed_files", []):
            _safe_posix(changed["path"], "MR changed file path")
    elif mr_facts is not None:
        raise EvidenceRuntimeError("模块分析不得伪造 mr_facts")

    return payload
'''
write("runtime/evidence_runtime.py", evidence_runtime)

schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PANGEA Evidence Provenance",
    "type": "object", "additionalProperties": False,
    "required": ["artifact_type", "schema_version", "run_id", "source_commits", "catalog",
                 "material_selection", "discovery", "source_evidence", "mr_facts", "limitations"],
    "properties": {
        "artifact_type": {"const": "evidence_provenance"}, "schema_version": {"const": "1.0"},
        "run_id": {"type": "string", "minLength": 1},
        "source_commits": {"type": "object", "minProperties": 1,
                           "additionalProperties": {"type": "string", "pattern": "^[0-9a-f]{40}$"}},
        "catalog": {"type": ["object", "null"], "additionalProperties": False,
                    "required": ["path", "sha256"], "properties": {
                        "path": {"const": "library/catalog.jsonl"},
                        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}},
        "material_selection": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["material_id", "source_ref", "source_sha256", "decision", "reason",
                         "markdown_path", "markdown_sha256", "consumed_anchors", "limitations"],
            "properties": {
                "material_id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{0,127}$"},
                "source_ref": {"type": "string", "minLength": 1},
                "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "decision": {"enum": ["selected", "out_of_scope", "duplicate", "unreadable", "blocked"]},
                "reason": {"type": "string", "minLength": 8},
                "markdown_path": {"type": ["string", "null"]},
                "markdown_sha256": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
                "consumed_anchors": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["start_line", "end_line", "excerpt_sha256", "claim"],
                    "properties": {"start_line": {"type": "integer", "minimum": 1},
                                   "end_line": {"type": "integer", "minimum": 1},
                                   "excerpt_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                                   "claim": {"type": "string", "minLength": 8}}}},
                "limitations": {"type": "array", "items": {"type": "string", "minLength": 4}}
            }}},
        "discovery": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["discovery_id", "target_kind", "repository", "commit_sha", "method", "query",
                         "scope", "candidate_ids", "disposition", "rationale", "evidence_ids", "limitations"],
            "properties": {
                "discovery_id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{0,127}$"},
                "target_kind": {"enum": sorted(["entrypoint", "registration", "flow", "branch", "state", "resource", "concurrency", "error_path", "protocol_operation", "limit", "coverage_gap"])},
                "repository": {"type": "string", "minLength": 1},
                "commit_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                "method": {"enum": ["source_read", "grep", "gitnexus", "registry_scan", "symbol_scan", "mr_diff", "coverage_input"]},
                "query": {"type": "string", "minLength": 1}, "scope": {"type": "string", "minLength": 4},
                "candidate_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "disposition": {"enum": ["expanded", "merged", "no_match", "out_of_scope", "blocked"]},
                "rationale": {"type": "string", "minLength": 8},
                "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "limitations": {"type": "array", "items": {"type": "string", "minLength": 4}}
            }}},
        "source_evidence": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False,
            "required": ["evidence_id", "repository", "commit_sha", "snapshot_id", "path", "line_start",
                         "line_end", "symbol", "claim", "file_sha256", "excerpt_sha256"],
            "properties": {
                "evidence_id": {"type": "string", "pattern": "^[A-Za-z][A-Za-z0-9._:-]{0,127}$"},
                "repository": {"type": "string", "minLength": 1},
                "commit_sha": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
                "snapshot_id": {"type": "string", "minLength": 1}, "path": {"type": "string", "minLength": 1},
                "line_start": {"type": "integer", "minimum": 1}, "line_end": {"type": "integer", "minimum": 1},
                "symbol": {"type": ["string", "null"]}, "claim": {"type": "string", "minLength": 8},
                "file_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "excerpt_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}
            }}},
        "mr_facts": {"type": ["object", "null"], "additionalProperties": False,
            "required": ["mr_url", "provider", "resolved_commits", "diff_sha256", "changed_files",
                         "developer_self_test", "facts", "inferences", "limitations"],
            "properties": {
                "mr_url": {"type": "string", "minLength": 1}, "provider": {"type": "string", "minLength": 1},
                "resolved_commits": {"type": "object", "minProperties": 1,
                    "additionalProperties": {"type": "string", "pattern": "^[0-9a-f]{40}$"}},
                "diff_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "changed_files": {"type": "array", "minItems": 1, "items": {
                    "type": "object", "additionalProperties": False, "required": ["path", "hunks"],
                    "properties": {"path": {"type": "string", "minLength": 1}, "hunks": {"type": "array", "minItems": 1,
                        "items": {"type": "object", "additionalProperties": False,
                            "required": ["old_start", "old_count", "new_start", "new_count"],
                            "properties": {"old_start": {"type": "integer", "minimum": 0},
                                           "old_count": {"type": "integer", "minimum": 0},
                                           "new_start": {"type": "integer", "minimum": 0},
                                           "new_count": {"type": "integer", "minimum": 0}}}}}}},
                "developer_self_test": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "facts": {"type": "array", "items": {"type": "string", "minLength": 4}},
                "inferences": {"type": "array", "items": {"type": "string", "minLength": 4}},
                "limitations": {"type": "array", "items": {"type": "string", "minLength": 4}}
            }},
        "limitations": {"type": "array", "items": {"type": "string", "minLength": 4}}
    }
}
write("schemas/evidence-provenance.schema.json", json.dumps(schema, ensure_ascii=False, indent=2) + "\n")

# Permit an exact evidence binding in analysis models without breaking historical models.
analysis_schema = json.loads(read("schemas/analysis-model.schema.json"))
analysis_schema["properties"]["evidence_artifact"] = {
    "type": "object", "additionalProperties": False, "required": ["path", "sha256"],
    "properties": {"path": {"const": "internal/evidence-provenance.json"},
                   "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}
}
write("schemas/analysis-model.schema.json", json.dumps(analysis_schema, ensure_ascii=False, indent=2) + "\n")

runctl = read("runtime/runctl.py")
runctl = replace_once(
    runctl,
    'ACTIVATION_PENDING_RELATIVE = "internal/activation-pending.json"\n',
    'ACTIVATION_PENDING_RELATIVE = "internal/activation-pending.json"\nEVIDENCE_PROVENANCE_RELATIVE = "internal/evidence-provenance.json"\n',
    "evidence constant",
)

insert_before_analysis = r'''

def _evidence_provenance_path(run_dir: Path) -> Path:
    internal = (run_dir / "internal").resolve()
    path = run_dir / EVIDENCE_PROVENANCE_RELATIVE
    if path.is_symlink() or path.resolve().parent != internal:
        raise RunCtlError("evidence provenance 不得通过符号链接指向 Run 外部")
    return path.resolve()


def _evidence_required(run_dir: Path) -> bool:
    manifest = read_json(run_dir / "manifest.json")
    return manifest.get("contract_record_file") == CONTRACT_RECORD_RELATIVE


def _validated_evidence(root: Path, run_dir: Path, contract: dict[str, Any], *, required: bool) -> dict[str, Any] | None:
    from runtime import evidence_runtime
    path = _evidence_provenance_path(run_dir)
    if not path.is_file():
        if required:
            raise RunCtlError(f"正式生命周期 Run 缺少固定证据工件: {EVIDENCE_PROVENANCE_RELATIVE}")
        return None
    payload = read_json(path)
    validate(payload, "evidence-provenance.schema.json")
    try:
        return evidence_runtime.validate_provenance(payload, root, run_dir, contract)
    except evidence_runtime.EvidenceRuntimeError as exc:
        raise RunCtlError(str(exc)) from exc


def _evidence_binding(root: Path, run_dir: Path, contract: dict[str, Any], *, required: bool) -> dict[str, str] | None:
    payload = _validated_evidence(root, run_dir, contract, required=required)
    if payload is None:
        return None
    return {"path": EVIDENCE_PROVENANCE_RELATIVE, "sha256": _sha256_file(_evidence_provenance_path(run_dir))}


def stage_evidence_v2(args: argparse.Namespace) -> None:
    """Validate and atomically stage material/discovery/MR/source provenance."""
    from runtime import data_runtime, evidence_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入 evidence provenance")
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-evidence-v2 仅用于任务契约生命周期创建的新 Run")
    if manifest.get("audit", {}).get("status") == "PASS":
        raise RunCtlError("审计 PASS 后不得改写 evidence provenance")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))
    source = Path(args.file).expanduser()
    if source.is_symlink() or not source.is_file():
        raise RunCtlError(f"evidence provenance 输入必须是普通文件: {source}")
    payload = read_json(source.resolve())
    validate(payload, "evidence-provenance.schema.json")
    try:
        normalized = evidence_runtime.validate_provenance(payload, root, run_dir, contract)
    except evidence_runtime.EvidenceRuntimeError as exc:
        raise RunCtlError(str(exc)) from exc
    target = _evidence_provenance_path(run_dir)
    _invalidate_fixed_artifact(_analysis_model_path(run_dir))
    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
    data_runtime.atomic_write_json(target, normalized)
    digest = _sha256_file(target)
    data_runtime.set_run_state(root, args.run_id, "mapping", "材料、发现过程、MR 和源码证据已完成真实性绑定")
    print(json.dumps({"run_id": args.run_id, "evidence_provenance": str(target),
                      "evidence_artifact": EVIDENCE_PROVENANCE_RELATIVE, "sha256": digest,
                      "next_step": "stage-analysis-v2" if contract["mode"] == "module_analysis" else "stage-report-v2"},
                     ensure_ascii=False))


'''
marker = '\n_ANALYSIS_COLLECTIONS: dict[str, tuple[str, ...]] = {'
runctl = replace_once(runctl, marker, insert_before_analysis + marker, "insert evidence runtime")

# Extend analysis validation with exact evidence IDs for lifecycle Runs.
old_sig = 'def _validate_analysis_model(model: Any, contract: dict[str, Any], run_id: str) -> dict[str, Any]:\n'
new_sig = 'def _validate_analysis_model(model: Any, contract: dict[str, Any], run_id: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:\n'
runctl = replace_once(runctl, old_sig, new_sig, "analysis signature")
analysis_hook = '''    if model.get("source_commits") != contract.get("repository_commits"):\n        raise RunCtlError("分析模型 source_commits 与任务契约 repository_commits 不一致")\n'''
analysis_hook_new = analysis_hook + '''    if evidence is not None:\n        from runtime import evidence_runtime\n        expected_binding = {"path": EVIDENCE_PROVENANCE_RELATIVE,\n                            "sha256": _sha256_file(_evidence_provenance_path(Path(ROOT) if False else Path()))}\n        del expected_binding\n        source_ids = evidence_runtime.source_evidence_ids(evidence)\n        reference_ids = evidence_runtime.reference_ids(evidence)\n        if model.get("evidence_artifact") is None:\n            raise RunCtlError("生命周期分析模型缺少 evidence_artifact binding")\n        for collection in ("entrypoints", "flows", "branches", "states", "resources", "concurrency", "error_chains"):\n            for item in model.get(collection, []):\n                refs = item.get("source_evidence") if isinstance(item, dict) else None\n                if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):\n                    raise RunCtlError(f"{collection} 的 source_evidence 必须只使用固定证据 ID")\n                unknown = set(refs) - source_ids\n                if unknown:\n                    raise RunCtlError(f"{collection} 引用未知固定源码证据: {sorted(unknown)}")\n        for item in model.get("evidence_consumption", []):\n            source_ref = item.get("source_ref") if isinstance(item, dict) else None\n            if source_ref not in reference_ids:\n                raise RunCtlError(f"evidence_consumption 引用未绑定 provenance ID: {source_ref}")\n'''
# Remove the intentionally dead expected-binding lines after insertion; binding is checked by caller with run_dir.
analysis_hook_new = analysis_hook_new.replace('        expected_binding = {"path": EVIDENCE_PROVENANCE_RELATIVE,\n                            "sha256": _sha256_file(_evidence_provenance_path(Path(ROOT) if False else Path()))}\n        del expected_binding\n', '')
runctl = replace_once(runctl, analysis_hook, analysis_hook_new, "analysis evidence hook")

# Revalidate analysis models with evidence when they are loaded later.
old_binding = '''    model = _validate_analysis_model(read_json(path), contract, run_dir.name)\n    del model\n    return {"path": ANALYSIS_MODEL_RELATIVE, "sha256": _sha256_file(path)}\n'''
new_binding = '''    root = run_dir.parents[2]\n    evidence = _validated_evidence(root, run_dir, contract, required=_evidence_required(run_dir))\n    model = _validate_analysis_model(read_json(path), contract, run_dir.name, evidence)\n    if evidence is not None and model.get("evidence_artifact") != _evidence_binding(root, run_dir, contract, required=True):\n        raise RunCtlError("analysis-model 未精确绑定 evidence provenance")\n    return {"path": ANALYSIS_MODEL_RELATIVE, "sha256": _sha256_file(path)}\n'''
runctl = replace_once(runctl, old_binding, new_binding, "analysis binding")

# Reports are also bound to the same fixed evidence artifact.
report_return = '''    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))\n    if binding is not None:\n'''
report_return_new = '''    root = run_dir.parents[2]\n    evidence_binding = _evidence_binding(root, run_dir, canonical, required=_evidence_required(run_dir))\n    if evidence_binding is not None and model.get("evidence_artifact") != evidence_binding:\n        raise RunCtlError("report-model 未精确绑定 evidence provenance")\n    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))\n    if binding is not None:\n'''
runctl = replace_once(runctl, report_return, report_return_new, "report evidence binding")

# Stage analysis injects the evidence binding and validates IDs.
stage_analysis_line = '    normalized = _validate_analysis_model(model, contract, args.run_id)\n'
stage_analysis_new = '''    evidence = _validated_evidence(root, run_dir, contract, required=_evidence_required(run_dir))\n    if evidence is not None:\n        model["evidence_artifact"] = _evidence_binding(root, run_dir, contract, required=True)\n    normalized = _validate_analysis_model(model, contract, args.run_id, evidence)\n'''
runctl = replace_once(runctl, stage_analysis_line, stage_analysis_new, "stage analysis evidence")

# Stage report injects evidence binding for both MR and module lifecycle Runs.
stage_report_contract = '    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))\n    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))\n'
stage_report_contract_new = '''    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))\n    evidence_binding = _evidence_binding(root, run_dir, contract, required=_evidence_required(run_dir))\n    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))\n'''
runctl = replace_once(runctl, stage_report_contract, stage_report_contract_new, "stage report evidence load")
model_inject = '''    if not isinstance(model, dict):\n        raise RunCtlError("报告模型必须是 JSON 对象")\n    if analysis_binding is not None:\n'''
model_inject_new = '''    if not isinstance(model, dict):\n        raise RunCtlError("报告模型必须是 JSON 对象")\n    if evidence_binding is not None:\n        model["evidence_artifact"] = evidence_binding\n    if analysis_binding is not None:\n'''
runctl = replace_once(runctl, model_inject, model_inject_new, "report evidence inject")

# Resume shows whether the fixed evidence provenance is present and current.
resume_line = '    snapshots = repository_runtime.snapshot_status(root, args.run_id)\n'
resume_new = '''    snapshots = repository_runtime.snapshot_status(root, args.run_id)\n    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))\n    try:\n        evidence_artifact = _evidence_binding(root, run_dir, contract, required=False)\n    except RunCtlError as exc:\n        evidence_artifact = {"status": "invalid", "error": str(exc)}\n'''
runctl = replace_once(runctl, resume_line, resume_new, "resume evidence")
resume_output = '                      "snapshots": snapshots}, ensure_ascii=False, indent=2))\n'
resume_output_new = '                      "snapshots": snapshots, "evidence_artifact": evidence_artifact}, ensure_ascii=False, indent=2))\n'
runctl = replace_once(runctl, resume_output, resume_output_new, "resume output")

# Add CLI command.
parser_marker = '    analysis2 = sub.add_parser("stage-analysis-v2", help="校验并实际落盘完整分析模型")\n'
parser_insert = '''    evidence2 = sub.add_parser("stage-evidence-v2", help="校验并落盘材料、发现、MR 与源码证据 provenance")\n    evidence2.add_argument("--run-id", required=True)\n    evidence2.add_argument("--file", required=True)\n    evidence2.add_argument("--root")\n    evidence2.set_defaults(func=stage_evidence_v2)\n'''
runctl = replace_once(runctl, parser_marker, parser_insert + parser_marker, "evidence parser")
write("runtime/runctl.py", runctl)

# Agent contracts.
primary = read(".opencode/agents/pangea-test.md")
anchor = "## 内部编排\n"
policy = '''## 固定证据 Provenance 门禁\n\n任务契约生命周期创建的新 Run，在代码地图/影响链完成后必须生成 `internal/evidence-provenance.json`，并通过 `stage-evidence-v2`。该工件是材料选择、搜索广度、MR facts 和源码行证据的唯一真实性来源。\n\n- 用户材料必须先进入 `pangea-data/inbox` 与 catalog；被选材料绑定 source SHA、转换 Markdown SHA 和实际消费行范围摘要。不能验证的外部材料只能标为 blocked/out_of_scope，不能伪装为已消费。\n- 每条源码事实必须引用固定 evidence ID；运行时验证仓库、commit、snapshot、相对路径、文件 SHA、行范围摘要和可选 symbol。自由文本 `driver.c:123` 不再是正式证据。\n- 完整模块必须记录 entrypoint、registration、flow、branch、state、resource、concurrency、error_path 八类搜索 disposition，包括有证据的 no_match 与 blocked。\n- MR 必须持久化 MR URL、provider、resolved commits、diff SHA、changed files/hunks、自验、事实、推断与限制。\n- `stage-evidence-v2` 失败时不得写 analysis-model、report-model、提交 auditor 或声称分析完成。\n\n'''
primary = replace_once(primary, anchor, policy + anchor, "primary provenance policy")
write(".opencode/agents/pangea-test.md", primary)

module_cmd = read(".opencode/commands/module-analysis.md")
needle = "深度门禁：完成分析阶段后，先调用"
replacement = "证据门禁：完成代码地图、流程、分支与六维搜索后，先调用 `<preflight.python_executable> runtime/runctl.py stage-evidence-v2 --run-id <Run ID> --file <evidence-provenance.json>`。材料、发现广度和源码证据必须通过真实快照与哈希校验；失败时不得继续。\n\n深度门禁：完成分析阶段后，先调用"
module_cmd = replace_once(module_cmd, needle, replacement, "module provenance command")
write(".opencode/commands/module-analysis.md", module_cmd)

mr_cmd = read(".opencode/commands/mr-regression.md")
needle = "审计门禁：主 Agent 先调用"
replacement = "证据门禁：MR facts、diff、changed hunks、材料选择、搜索过程和源码行证据必须先写入 `<evidence-provenance.json>`，并调用 `<preflight.python_executable> runtime/runctl.py stage-evidence-v2 --run-id <Run ID> --file <evidence-provenance.json>`。失败时不得进入报告审计。\n\n审计门禁：主 Agent 先调用"
mr_cmd = replace_once(mr_cmd, needle, replacement, "MR provenance command")
write(".opencode/commands/mr-regression.md", mr_cmd)

resume = read(".opencode/commands/resume-run.md")
resume = resume.replace("读取其任务契约、检查点、风险账本和临时目录状态", "读取其任务契约、固定 evidence provenance、检查点、风险账本和临时目录状态")
write(".opencode/commands/resume-run.md", resume)

excavator = read(".opencode/agents/code-excavator.md")
excavator += '''\n\n正式源码证据输出必须提供可写入 evidence provenance 的字段：`evidence_id`、`repository`、`commit_sha`、`snapshot_id`、规范相对 `path`、`line_start`、`line_end`、可选 `symbol`、具体 `claim`、`file_sha256`、`excerpt_sha256`。不得只返回自由文本文件名和行号。\n'''
write(".opencode/agents/code-excavator.md", excavator)

mr_reader = read(".opencode/agents/mr-reader.md")
mr_reader += '''\n\n输出还必须形成 mr_facts 候选：MR URL、provider、每个仓的 resolved commit、diff SHA-256、changed files 与 hunk 范围、开发自验、事实、推断和限制。主 Agent 将其写入固定 evidence provenance；不要把推断混入 facts。\n'''
write(".opencode/agents/mr-reader.md", mr_reader)

# Regression tests for semantic binding.
test = r'''from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, repository_runtime
from tests.test_analysis_depth_contract import AnalysisDepthContractTests
from tests.test_contract_lifecycle import ContractLifecycleTests

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"
KINDS = ("entrypoint", "registration", "flow", "branch", "state", "resource", "concurrency", "error_path")


class EvidenceProvenanceTests(unittest.TestCase):
    def cli(self, root: Path, *args: str, expected: int = 0) -> dict:
        result = subprocess.run([sys.executable, str(RUNCTL), *args, "--root", str(root)], cwd=ROOT,
                                text=True, capture_output=True, check=False)
        if result.returncode != expected:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {"stderr": result.stderr}

    def activate(self, root: Path, *, contract_id: str = "evidence", depth: str = "complete",
                 input_refs: list[str] | None = None, materials_status: str = "confirmed_none") -> Path:
        ContractLifecycleTests().prepare(root)
        draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                         "--repository", "driver", "--analysis-depth", depth, "--contract-id", contract_id)
        if input_refs:
            contract = draft["task_contract"]; contract["input_refs"] = input_refs
            revised = root / "contract.json"; revised.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            self.cli(root, "revise-contract-v2", "--contract-id", contract_id, "--expected-revision", "1", "--file", str(revised))
            revision = "2"
        else:
            revision = "1"
        self.cli(root, "confirm-contract-v2", "--contract-id", contract_id, "--revision", revision,
                 "--source", "user_reply", "--materials-status", materials_status)
        activated = self.cli(root, "activate-contract-v2", "--contract-id", contract_id, "--run-id", contract_id + "-run")
        return Path(activated["run_dir"])

    @staticmethod
    def source_record(root: Path, run_dir: Path) -> dict:
        status = repository_runtime.snapshot_status(root, run_dir.name)
        binding = status["snapshots"][0]
        source = Path(binding["snapshot_dir"]) / "driver.c"
        content = source.read_bytes(); excerpt = b"".join(content.splitlines(keepends=True)[:1])
        return {"evidence_id": "EV-1", "repository": "driver", "commit_sha": binding["commit_sha"],
                "snapshot_id": binding["snapshot_id"], "path": "driver.c", "line_start": 1, "line_end": 1,
                "symbol": "entry", "claim": "外部入口函数在当前提交中真实存在",
                "file_sha256": hashlib.sha256(content).hexdigest(),
                "excerpt_sha256": hashlib.sha256(excerpt).hexdigest()}

    def payload(self, root: Path, run_dir: Path, *, materials: list[dict] | None = None,
                catalog: dict | None = None, mr_facts: dict | None = None) -> dict:
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        evidence = self.source_record(root, run_dir)
        discovery = [{"discovery_id": f"DISC-{index}", "target_kind": kind, "repository": "driver",
                      "commit_sha": evidence["commit_sha"], "method": "source_read", "query": kind,
                      "scope": "driver.c and registration tables", "candidate_ids": [f"CAND-{index}"],
                      "disposition": "expanded", "rationale": f"已从快照源码展开 {kind} 候选",
                      "evidence_ids": ["EV-1"], "limitations": []}
                     for index, kind in enumerate(KINDS, 1)]
        return {"artifact_type": "evidence_provenance", "schema_version": "1.0", "run_id": run_dir.name,
                "source_commits": contract["repository_commits"], "catalog": catalog,
                "material_selection": materials or [], "discovery": discovery,
                "source_evidence": [evidence], "mr_facts": mr_facts, "limitations": []}

    def stage(self, root: Path, run_dir: Path, payload: dict, *, expected: int = 0) -> dict:
        path = root / "evidence.json"; path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return self.cli(root, "stage-evidence-v2", "--run-id", run_dir.name, "--file", str(path), expected=expected)

    def test_valid_source_and_discovery_provenance_is_staged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root)
            staged = self.stage(root, run_dir, self.payload(root, run_dir))
            self.assertEqual("internal/evidence-provenance.json", staged["evidence_artifact"])
            self.assertTrue((run_dir / "internal/evidence-provenance.json").is_file())

    def test_forged_file_hash_and_line_range_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root, contract_id="forged")
            payload = self.payload(root, run_dir); payload["source_evidence"][0]["file_sha256"] = "0" * 64
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("SHA-256", rejected["stderr"])
            payload = self.payload(root, run_dir); payload["source_evidence"][0]["line_end"] = 99
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("行号越界", rejected["stderr"])

    def test_complete_discovery_requires_all_canonical_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root, contract_id="breadth")
            payload = self.payload(root, run_dir); payload["discovery"] = payload["discovery"][:-1]
            rejected = self.stage(root, run_dir, payload, expected=2)
            self.assertIn("error_path", rejected["stderr"])

    def test_selected_material_binds_catalog_markdown_and_anchor_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ContractLifecycleTests.marked_root(root); ContractLifecycleTests.repository(root)
            workspace = data_runtime.ensure_layout(root)
            (workspace / "inbox/design.md").write_text("# CHAP 设计\n认证失败必须释放会话资源。\n", encoding="utf-8")
            data_runtime.scan_inbox(root); data_runtime.convert_catalog(root); ContractLifecycleTests.receipt(root)
            run_dir = self.activate_existing(root, input_ref="pangea-data/inbox/design.md")
            catalog_path = workspace / "library/catalog.jsonl"
            record = data_runtime._read_jsonl(catalog_path)[0]
            markdown = workspace / record["markdown_path"]
            lines = markdown.read_bytes().splitlines(keepends=True)
            excerpt = b"".join(lines[:2])
            material = {"material_id": "MAT-1", "source_ref": "design.md", "source_sha256": record["sha256"],
                        "decision": "selected", "reason": "该设计定义认证失败后的资源恢复要求",
                        "markdown_path": record["markdown_path"], "markdown_sha256": hashlib.sha256(markdown.read_bytes()).hexdigest(),
                        "consumed_anchors": [{"start_line": 1, "end_line": 2,
                            "excerpt_sha256": hashlib.sha256(excerpt).hexdigest(),
                            "claim": "设计要求认证失败后释放会话资源"}], "limitations": []}
            catalog = {"path": "library/catalog.jsonl", "sha256": hashlib.sha256(catalog_path.read_bytes()).hexdigest()}
            self.stage(root, run_dir, self.payload(root, run_dir, materials=[material], catalog=catalog))

    def activate_existing(self, root: Path, input_ref: str) -> Path:
        draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                         "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "material")
        contract = draft["task_contract"]; contract["input_refs"] = [input_ref]
        path = root / "material-contract.json"; path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
        self.cli(root, "revise-contract-v2", "--contract-id", "material", "--expected-revision", "1", "--file", str(path))
        self.cli(root, "confirm-contract-v2", "--contract-id", "material", "--revision", "2",
                 "--source", "user_reply", "--materials-status", "provided")
        return Path(self.cli(root, "activate-contract-v2", "--contract-id", "material", "--run-id", "material-run")["run_dir"])

    def test_analysis_model_must_use_fixed_evidence_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.activate(root, contract_id="analysis")
            self.stage(root, run_dir, self.payload(root, run_dir))
            AnalysisDepthContractTests.complete_checkpoints(root, run_dir.name)
            model = AnalysisDepthContractTests.model(run_dir)
            model["evidence_consumption"][0]["source_ref"] = "EV-1"
            for collection in ("entrypoints", "flows", "branches", "states", "resources", "concurrency", "error_chains"):
                for item in model[collection]: item["source_evidence"] = ["EV-UNKNOWN"]
            path = root / "analysis.json"; path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("未知固定源码证据", rejected["stderr"])


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_evidence_provenance.py", test)

# Structural tests assert the new hard gate is part of Agent contracts.
agent_test = read("tests/test_agent_v2.py")
marker = '\n    def test_primary_can_dispatch_only_internal_capabilities(self) -> None:\n'
insert = '''\n    def test_lifecycle_runs_require_fixed_evidence_provenance(self) -> None:\n        combined = "\\n".join((AGENTS / "pangea-test.md").read_text(encoding="utf-8") for _ in range(1))\n        combined += "\\n" + (COMMANDS / "module-analysis.md").read_text(encoding="utf-8")\n        combined += "\\n" + (COMMANDS / "mr-regression.md").read_text(encoding="utf-8")\n        for term in ("stage-evidence-v2", "evidence-provenance.json", "file_sha256", "excerpt_sha256", "mr_facts"):\n            self.assertIn(term, combined)\n\n'''
agent_test = replace_once(agent_test, marker, insert + marker, "agent evidence test")
write("tests/test_agent_v2.py", agent_test)
