"""Local-only classification and bounded retrieval for PANGEA's document library.

This module extends ``pangea-data/library/catalog.jsonl`` in place.  It never
moves, renames, or rewrites an inbox source or its content-addressed archive.
Semantic classifications are deliberately labelled as model inference.
"""
from __future__ import annotations

import copy
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable


class LibraryRuntimeError(RuntimeError):
    pass


ROLES = ("design", "requirement", "coverage", "testcase", "defect", "wiki", "reference", "unknown")
CONFIDENCE = ("low", "medium", "high")
CATALOG_RELATIVE = Path("pangea-data/library/catalog.jsonl")
SCHEMA_RELATIVE = Path("schemas/library-record.schema.json")
_IGNORED_LEGACY = {"README", "README.md", ".gitkeep"}

# Ordered from the most specific record types to generic reference material.
_ROLE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("defect", ("defect", "bug", "issue", "incident", "problem", "ticket", "缺陷", "问题单", "故障")),
    ("coverage", ("coverage", "cover-report", "覆盖率", "覆盖报告")),
    ("testcase", ("testcase", "test-case", "test_case", "test plan", "testplan", "用例", "测试方案", "测试计划")),
    ("requirement", ("requirement", "requirements", "prd", "user-story", "需求", "规格需求")),
    ("design", ("design", "architecture", "specification", "spec", "设计", "架构", "概要方案", "详细方案")),
    ("wiki", ("wiki", "knowledge-base", "knowledge", "经验", "知识库", "faq")),
    ("reference", ("reference", "manual", "guide", "release-note", "readme", "参考", "手册", "指南", "发布说明")),
)


def data_root(root: Path) -> Path:
    return root.resolve() / "pangea-data"


def catalog_path(root: Path) -> Path:
    return root.resolve() / CATALOG_RELATIVE


def _read_catalog(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LibraryRuntimeError(f"catalog 第 {line_number} 行无效: {exc}") from exc
        if not isinstance(row, dict):
            raise LibraryRuntimeError(f"catalog 第 {line_number} 行必须是对象")
        rows.append(row)
    return rows


def _atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        temporary = Path(handle.name)
    temporary.replace(path)


def role_hint(source_path: str) -> dict[str, str]:
    """Return a conservative filename/path hint, never a semantic fact."""
    value = source_path.replace("\\", "/").casefold()
    filename = Path(value).stem
    for role, patterns in _ROLE_PATTERNS:
        for pattern in patterns:
            candidate = pattern.casefold()
            if candidate in filename:
                return {"role": role, "rule": f"filename_contains:{pattern}", "confidence": "high"}
            if candidate in value:
                return {"role": role, "rule": f"path_contains:{pattern}", "confidence": "medium"}
    return {"role": "unknown", "rule": "no_clear_path_or_filename_signal", "confidence": "low"}


def _validate_list(value: Any, field: str) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise LibraryRuntimeError(f"{field} 必须是非空字符串数组")
    if len(set(value)) != len(value):
        raise LibraryRuntimeError(f"{field} 不允许重复")


def _classification_schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / SCHEMA_RELATIVE
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibraryRuntimeError(f"资料库 schema 无法读取: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LibraryRuntimeError(f"资料库 schema 必须是对象: {path}")
    return value


def validate_semantic_classification(value: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    """Validate the schema subset without a third-party jsonschema dependency."""
    schema = _classification_schema()
    properties = schema.get("properties", {})
    required = set(schema.get("required", ()))
    if not isinstance(properties, dict) or not required:
        raise LibraryRuntimeError("资料库 schema 缺少 properties 或 required")
    candidate = copy.deepcopy(value)
    candidate.setdefault("source_backed", False)
    candidate.setdefault("provenance", "model_inference")
    candidate.setdefault("inherited", False)
    missing, unexpected = required - set(candidate), set(candidate) - set(properties)
    if missing:
        raise LibraryRuntimeError(f"分类缺少字段: {', '.join(sorted(missing))}")
    if unexpected:
        raise LibraryRuntimeError(f"分类包含未知字段: {', '.join(sorted(unexpected))}")
    if candidate["role"] not in properties["role"]["enum"] or candidate["confidence"] not in properties["confidence"]["enum"]:
        raise LibraryRuntimeError("role 或 confidence 不在允许范围")
    for field in ("tags", "applicable_modules", "versions"):
        _validate_list(candidate[field], field)
    for field in ("summary", "rationale"):
        if not isinstance(candidate[field], str) or not candidate[field].strip():
            raise LibraryRuntimeError(f"{field} 必须是非空字符串")
    if candidate["source_backed"] is not False:
        raise LibraryRuntimeError("Agent 语义分类必须标记 source_backed=false，不能将模型推断伪装成事实")
    if candidate["provenance"] not in properties["provenance"]["enum"]:
        raise LibraryRuntimeError("provenance 必须是 model_inference 或 inherited")
    result = candidate
    result["source_backed"] = False
    if result["inherited"]:
        if not isinstance(result.get("inherited_from"), str) or not result["inherited_from"]:
            raise LibraryRuntimeError("继承分类必须说明 inherited_from")
        result["provenance"] = "inherited"
    else:
        result.pop("inherited_from", None)
        result["provenance"] = "model_inference"
    return result


def refresh_role_hints(root: Path) -> dict[str, Any]:
    """Apply hints only to records whose source content has not been handled."""
    path = catalog_path(root)
    rows = _read_catalog(path)
    updated = inherited = unchanged = 0
    by_hash: dict[str, dict[str, Any]] = {}
    for row in rows:
        checksum = row.get("sha256")
        source_path = row.get("source_path")
        if not isinstance(checksum, str) or not isinstance(source_path, str):
            unchanged += 1
            continue
        needs_refresh = row.get("classification_sha256") != checksum
        if needs_refresh:
            donor = by_hash.get(checksum)
            if donor and isinstance(donor.get("semantic_classification"), dict):
                classification = copy.deepcopy(donor["semantic_classification"])
                classification.update({"inherited": True, "inherited_from": str(donor.get("source_path")), "provenance": "inherited", "source_backed": False})
                row["semantic_classification"] = validate_semantic_classification(classification)
                inherited += 1
            row["role_hint"] = role_hint(source_path)
            row["classification_sha256"] = checksum
            updated += 1
        else:
            unchanged += 1
        by_hash.setdefault(checksum, row)
    if updated:
        _atomic_write_jsonl(path, rows)
    return {"catalog": str(path), "updated": updated, "inherited": inherited, "unchanged": unchanged, "count": len(rows)}


def write_semantic_classification(root: Path, source_path: str, classification: dict[str, Any]) -> dict[str, Any]:
    path = catalog_path(root)
    rows = _read_catalog(path)
    matches = [row for row in rows if row.get("source_path") == source_path]
    if len(matches) != 1:
        raise LibraryRuntimeError(f"未找到唯一资料源: {source_path}")
    normalized = validate_semantic_classification(classification, root)
    target = matches[0]
    checksum = target.get("sha256")
    if not isinstance(checksum, str) or not checksum:
        raise LibraryRuntimeError("资料缺少 sha256，无法写入分类")
    target["semantic_classification"] = normalized
    target["classification_sha256"] = checksum
    target["role_hint"] = target.get("role_hint") or role_hint(source_path)
    _atomic_write_jsonl(path, rows)
    return {"catalog": str(path), "source_path": source_path, "sha256": checksum, "semantic_classification": normalized}


def _terms(value: str) -> list[str]:
    return [term for term in re.findall(r"[\w.-]+|[\u4e00-\u9fff]+", value.casefold()) if term]


def _matches_query(query: str, value: str) -> bool:
    haystack = value.casefold()
    terms = _terms(query)
    return bool(terms) and all(term in haystack for term in terms)


def _anchor_for(markdown: str, position: int) -> str:
    prefix = markdown[:position]
    anchors = re.findall(r"<!--\s*([^>]+?)\s*-->", prefix)
    if anchors:
        return anchors[-1]
    headings = re.findall(r"^#{1,6}\s+(.+)$", prefix, re.MULTILINE)
    return f"heading:{headings[-1]}" if headings else "document:start"


def _snippet(markdown: str, query: str, limit: int = 360) -> tuple[str, str]:
    lower = markdown.casefold()
    positions = [lower.find(term) for term in _terms(query)]
    position = min((point for point in positions if point >= 0), default=0)
    start = max(0, position - limit // 3)
    end = min(len(markdown), start + limit)
    text = re.sub(r"\s+", " ", markdown[start:end]).strip()
    if start:
        text = "..." + text
    if end < len(markdown):
        text += "..."
    return text, _anchor_for(markdown, position)


def _as_filter(values: Iterable[str] | None) -> set[str]:
    return {value.casefold() for value in values or () if value}


def search_library(root: Path, query: str, *, role: str | None = None, tags: Iterable[str] | None = None,
                   module: str | None = None, version: str | None = None, limit: int = 8) -> dict[str, Any]:
    if not query.strip():
        raise LibraryRuntimeError("query 不能为空")
    if limit < 1:
        raise LibraryRuntimeError("limit 必须大于 0")
    workspace = data_root(root)
    wanted_tags = _as_filter(tags)
    results: list[dict[str, Any]] = []
    for row in _read_catalog(catalog_path(root)):
        classification = row.get("semantic_classification") if isinstance(row.get("semantic_classification"), dict) else {}
        actual_role = classification.get("role") or (row.get("role_hint") or {}).get("role", "unknown")
        if role and actual_role != role:
            continue
        if wanted_tags and not wanted_tags.issubset(_as_filter(classification.get("tags", []))):
            continue
        if module and module.casefold() not in _as_filter(classification.get("applicable_modules", [])):
            continue
        if version and version.casefold() not in _as_filter(classification.get("versions", [])):
            continue
        metadata = " ".join(str(row.get(field, "")) for field in ("source_path", "format")) + " " + " ".join(
            str(classification.get(field, "")) for field in ("role", "tags", "summary", "applicable_modules", "versions", "rationale")
        )
        markdown = ""
        markdown_path = row.get("markdown_path")
        if isinstance(markdown_path, str):
            candidate = workspace / markdown_path
            if candidate.is_file():
                markdown = candidate.read_text(encoding="utf-8", errors="replace")
        if not _matches_query(query, metadata + "\n" + markdown):
            continue
        source = str(row.get("source_path", ""))
        if _matches_query(query, markdown):
            snippet, anchor = _snippet(markdown, query)
        else:
            snippet, anchor = re.sub(r"\s+", " ", metadata).strip()[:360], "metadata"
        results.append({"source_path": source, "sha256": row.get("sha256"), "role": actual_role,
                        "markdown_anchor": anchor, "snippet": snippet,
                        "markdown_path": markdown_path, "source_backed": classification.get("source_backed", False)})
        if len(results) >= limit:
            break
    return {"query": query, "count": len(results), "results": results}


def legacy_migration_gaps(root: Path) -> dict[str, Any]:
    root = root.resolve()
    gaps: list[dict[str, str]] = []
    for name in ("source", "inputs", "workspace", "outputs"):
        directory = root / name
        if not directory.is_dir():
            continue
        for item in sorted(directory.rglob("*")):
            if item.is_dir() or item.name in _IGNORED_LEGACY:
                continue
            gaps.append({"legacy_root": name, "path": item.relative_to(root).as_posix(), "kind": "file"})
    return {"legacy_migration_gaps": gaps, "count": len(gaps), "action": "detected_only_no_files_moved"}
