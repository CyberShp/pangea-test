"""Repository-neutral semantic module-analysis planning and assembly.

The model chooses semantic units from a repository code map.  The runtime owns
source coverage, size limits, deterministic staging, deterministic IDs, and the
final normalized analysis-model projection.  No provider name or repository-
specific flow name appears in this module.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from runtime import analysis_modes, data_runtime, source_inventory


PLAN_ARTIFACT = "semantic_analysis_plan"
UNIT_ARTIFACT = "semantic_analysis_unit"
SCHEMA_VERSION = "1.0"
PLAN_SCHEMA_VERSION = "1.1"
MAX_UNIT_SOURCE_BYTES = 120_000
MAX_UNITS = 64
DFX = ("功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性")
FOCUS = frozenset({"code_map", "flows", "branches", "dfx", "specialist", "sfmea", "scenarios", "test_cases"})
LEGACY_PLAN_KEYS = {"artifact_type", "schema_version", "run_id", "analysis_depth", "units", "mapped_only", "depth_limitations"}
PLAN_KEYS = LEGACY_PLAN_KEYS | {"target"}
UNIT_PLAN_KEYS = {"unit_id", "title", "repository", "priority", "source_ranges", "focus", "dfx", "depth_limitations"}
RANGE_KEYS = {"path", "line_start", "line_end"}
MAPPED_KEYS = {"repository", "path", "reason"}
UNIT_KEYS = {
    "artifact_type", "schema_version", "run_id", "unit_id", "plan_sha256", "summary",
    "code_map", "flows", "dfx", "specialist_findings", "sfmea", "scenarios", "test_cases",
    "depth_limitations", "unresolved",
}
EVIDENCE_KEYS = {"path", "line", "fact"}


class SemanticAnalysisError(ValueError):
    pass


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _cjk(value: Any, label: str, minimum: int = 2) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum or re.search(r"[\u4e00-\u9fff]", value) is None:
        raise SemanticAnalysisError(f"{label} must contain concrete Chinese text")
    return value.strip()


def _text(value: Any, label: str, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise SemanticAnalysisError(f"{label} must be non-empty text")
    return value.strip()


def _list(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        raise SemanticAnalysisError(f"{label} must be {'non-empty ' if nonempty else ''}array")
    return value


def _safe_relative(value: Any, label: str) -> str:
    text = _text(value, label)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != text or "\x00" in text:
        raise SemanticAnalysisError(f"{label} must be a normalized relative path")
    return text


def _key_difference(value: Any, expected: set[str] | frozenset[str]) -> str:
    if not isinstance(value, dict):
        return "expected=object"
    actual = set(value)
    return f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"


def _load_run(root: Path, run_id: str) -> tuple[Path, dict[str, Any]]:
    run, _ = data_runtime._load_run(root.resolve(), run_id)
    contract = data_runtime.read_json(run / "internal/task-contract.json")
    try:
        analysis_modes.require(contract, analysis_modes.SEMANTIC, legacy=False)
    except analysis_modes.AnalysisModeError as exc:
        raise SemanticAnalysisError(str(exc)) from exc
    if contract.get("mode") != "module_analysis" or contract.get("analysis_depth") not in {"complete", "fast"}:
        raise SemanticAnalysisError("semantic analysis requires a module-analysis Run")
    return run, contract


def _source_files(run: Path, contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    repository_root = run.parents[1] / "repositories"
    scopes = contract.get("source_scopes")
    for repo in contract["repositories"]:
        source_root = repository_root / repo
        if not source_root.is_dir():
            raise SemanticAnalysisError(f"semantic analysis repository is unavailable: {repo}")
        selected = source_inventory._scope(source_root, scopes.get(repo) if isinstance(scopes, dict) else None)
        for relative in selected:
            path = source_inventory._safe_file(source_root, relative)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines() or [""]
            key = repo + "\0" + relative
            result[key] = {
                "repository": repo, "path": relative, "root": source_root, "lines": lines,
                "line_count": len(lines), "byte_count": len(path.read_bytes()),
            }
    if not result:
        raise SemanticAnalysisError("semantic analysis source scope is empty")
    return result


def _symbol_map(lines: list[str]) -> tuple[list[list[Any]], list[list[Any]], list[list[Any]]]:
    functions: list[list[Any]] = []
    types: list[list[Any]] = []
    signals: list[list[Any]] = []
    for index, raw in enumerate(lines, 1):
        text = raw.strip()
        match = re.match(r"^(?:struct|enum|union)\s+([A-Za-z_]\w*)", text)
        if match and len(types) < 32:
            types.append([index, match.group(1)])
        if re.search(r"(?i)\b(register|callback|handler|poller|state|error|fail|release|free|close)\b", text) and len(signals) < 32:
            words = re.findall(r"[A-Za-z_]\w*", text)
            signals.append([index, words[-1] if words else "signal"])
        if "(" not in text or text.startswith(("#", "if ", "for ", "while ", "switch ", "return ")):
            continue
        cursor = index - 1
        signature = text
        while cursor + 1 < len(lines) and cursor < index + 2 and "{" not in signature and ";" not in signature:
            cursor += 1
            signature += " " + lines[cursor].strip()
        if "{" not in signature or ";" in signature.split("{", 1)[0]:
            continue
        names = re.findall(r"([A-Za-z_]\w*)\s*$", signature.split("(", 1)[0].strip())
        if names and names[0] not in {"if", "for", "while", "switch"} and len(functions) < 128:
            functions.append([index, names[0]])
    return functions, types, signals


def build_code_map(root: Path, run_id: str) -> dict[str, Any]:
    run, contract = _load_run(root, run_id)
    rows = []
    for value in _source_files(run, contract).values():
        functions, types, signals = _symbol_map(value["lines"])
        rows.append({
            "repository": value["repository"], "path": value["path"],
            "line_count": value["line_count"], "byte_count": value["byte_count"],
            "functions": functions, "types": types, "signals": signals,
        })
    payload = {
        "artifact_type": "semantic_code_map", "schema_version": SCHEMA_VERSION,
        "run_id": run_id, "analysis_depth": contract["analysis_depth"],
        "target": contract["target"], "repositories": contract["repositories"],
        "max_unit_source_bytes": MAX_UNIT_SOURCE_BYTES, "files": rows,
    }
    target = run / "internal/semantic-analysis/code-map.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    data_runtime.atomic_write_json(target, payload)
    return payload


def planner_context(root: Path, run_id: str) -> dict[str, Any]:
    code_map = build_code_map(root, run_id)
    return {
        "request_type": "semantic_plan", "schema_version": SCHEMA_VERSION,
        "instructions": (
            "根据代码地图按业务流程、组件、状态机和异常链生成语义分析单元。禁止逐行出题。"
            "complete必须覆盖全部源码行；fast必须保留代码地图和六维DFX，但可将非关键文件列入mapped_only。"
            "每个单元源码UTF-8字节数不得超过max_unit_source_bytes。focus和dfx都是数组，一个单元可承担多个类型；"
            "所有单元的focus并集必须覆盖全部focus_values，dfx并集必须覆盖全部dfx_values。"
            "计划target必须逐字复制code_map.target。所有title/reason/depth_limitations使用中文。"
        ),
        "output_contract": {
            "schema_version": PLAN_SCHEMA_VERSION,
            "top_keys": sorted(PLAN_KEYS), "unit_keys": sorted(UNIT_PLAN_KEYS),
            "range_keys": sorted(RANGE_KEYS), "mapped_only_keys": sorted(MAPPED_KEYS),
            "focus_values": sorted(FOCUS), "dfx_values": list(DFX),
            "required_focus_union": sorted(FOCUS), "required_dfx_union": list(DFX),
        },
        "code_map": code_map,
    }


def _ranges_for_unit(unit: dict[str, Any], files: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    ranges = _list(unit.get("source_ranges"), "unit.source_ranges", nonempty=True)
    seen: set[tuple[str, str, int, int]] = set()
    normalized: list[dict[str, Any]] = []
    sizes: list[tuple[int, str, int, int]] = []
    total = 0
    unit_id = unit.get("unit_id", "<unknown>")
    for index, row in enumerate(ranges):
        if not isinstance(row, dict) or set(row) != RANGE_KEYS:
            raise SemanticAnalysisError(
                f"semantic plan unit {unit_id} source_ranges[{index}] is invalid: "
                f"{_key_difference(row, RANGE_KEYS)}"
            )
        repo = unit["repository"]
        path = _safe_relative(row.get("path"), "source range path")
        key = repo + "\0" + path
        value = files.get(key)
        start, end = row.get("line_start"), row.get("line_end")
        if value is None or type(start) is not int or type(end) is not int or not 1 <= start <= end <= value["line_count"]:
            available = value["line_count"] if value is not None else "missing"
            raise SemanticAnalysisError(
                f"semantic plan unit {unit_id} source range is outside source scope: "
                f"repository={repo}, path={path}, requested={start}-{end}, available_lines={available}"
            )
        identity = (repo, path, start, end)
        if identity in seen:
            raise SemanticAnalysisError(
                f"semantic plan unit {unit_id} source range is duplicated: {repo}:{path}:{start}-{end}"
            )
        seen.add(identity)
        text = "\n".join(value["lines"][start - 1:end])
        size = len(text.encode("utf-8"))
        total += size
        sizes.append((size, path, start, end))
        normalized.append({"path": path, "line_start": start, "line_end": end})
    if total > MAX_UNIT_SOURCE_BYTES:
        largest = [f"{path}:{start}-{end}={size}" for size, path, start, end
                   in sorted(sizes, reverse=True)[:3]]
        raise SemanticAnalysisError(
            f"semantic plan unit {unit_id} exceeds source byte limit: "
            f"source_bytes={total}, limit={MAX_UNIT_SOURCE_BYTES}, largest_ranges={largest}"
        )
    return normalized, total


def validate_plan(root: Path, run_id: str, plan: Any) -> dict[str, Any]:
    run, contract = _load_run(root, run_id)
    if not isinstance(plan, dict):
        raise SemanticAnalysisError("semantic analysis plan envelope is invalid: expected=object")
    schema_version = plan.get("schema_version")
    expected_keys = LEGACY_PLAN_KEYS if schema_version == SCHEMA_VERSION else PLAN_KEYS
    if schema_version not in {SCHEMA_VERSION, PLAN_SCHEMA_VERSION}:
        raise SemanticAnalysisError(
            f"semantic analysis plan schema_version is invalid: "
            f"actual={schema_version!r}, expected={[SCHEMA_VERSION, PLAN_SCHEMA_VERSION]}"
        )
    if set(plan) != expected_keys:
        raise SemanticAnalysisError(
            f"semantic analysis plan envelope is invalid: {_key_difference(plan, expected_keys)}"
        )
    if plan.get("artifact_type") != PLAN_ARTIFACT:
        raise SemanticAnalysisError(
            f"semantic analysis plan artifact_type is invalid: actual={plan.get('artifact_type')!r}, "
            f"expected={PLAN_ARTIFACT!r}"
        )
    if plan.get("run_id") != run_id or plan.get("analysis_depth") != contract["analysis_depth"]:
        raise SemanticAnalysisError(
            "semantic analysis plan Run/depth binding is invalid: "
            f"run_id={plan.get('run_id')!r}, expected_run_id={run_id!r}, "
            f"analysis_depth={plan.get('analysis_depth')!r}, "
            f"expected_analysis_depth={contract['analysis_depth']!r}"
        )
    if schema_version == PLAN_SCHEMA_VERSION and plan.get("target") != contract["target"]:
        raise SemanticAnalysisError(
            f"semantic analysis plan target is invalid: actual={plan.get('target')!r}, "
            f"expected={contract['target']!r}"
        )
    units = _list(plan.get("units"), "semantic plan units", nonempty=True)
    if len(units) > MAX_UNITS:
        raise SemanticAnalysisError("semantic analysis plan has too many units")
    files = _source_files(run, contract)
    ids: set[str] = set()
    covered: dict[str, list[tuple[int, int]]] = {key: [] for key in files}
    focus_union: set[str] = set()
    dfx_union: set[str] = set()
    normalized_units: list[dict[str, Any]] = []
    for unit_index, row in enumerate(units):
        if not isinstance(row, dict) or set(row) != UNIT_PLAN_KEYS:
            raise SemanticAnalysisError(
                f"semantic analysis units[{unit_index}] envelope is invalid: "
                f"{_key_difference(row, UNIT_PLAN_KEYS)}"
            )
        unit_id = _text(row.get("unit_id"), "unit_id")
        if not re.fullmatch(r"U[0-9]{2,3}", unit_id) or unit_id in ids:
            raise SemanticAnalysisError("semantic analysis unit_id is invalid or duplicated")
        ids.add(unit_id)
        _cjk(row.get("title"), "unit.title")
        repo = row.get("repository")
        if repo not in contract["repositories"] or row.get("priority") not in {"P0", "P1", "P2"}:
            raise SemanticAnalysisError("semantic analysis unit repository or priority is invalid")
        focuses = _list(row.get("focus"), "unit.focus", nonempty=True)
        dimensions = _list(row.get("dfx"), "unit.dfx", nonempty=True)
        limitations = _list(row.get("depth_limitations"), "unit.depth_limitations")
        if set(focuses) - FOCUS or len(focuses) != len(set(focuses)):
            raise SemanticAnalysisError("semantic analysis unit focus is invalid")
        if set(dimensions) - set(DFX) or len(dimensions) != len(set(dimensions)):
            raise SemanticAnalysisError("semantic analysis unit DFX is invalid")
        if contract["analysis_depth"] == "complete" and limitations:
            raise SemanticAnalysisError("complete semantic unit may not declare depth limitations")
        for value in limitations:
            _cjk(value, "unit.depth_limitations")
        normalized_ranges, _ = _ranges_for_unit(row, files)
        for item in normalized_ranges:
            covered[repo + "\0" + item["path"]].append((item["line_start"], item["line_end"]))
        focus_union.update(focuses); dfx_union.update(dimensions)
        normalized_units.append({**row, "source_ranges": normalized_ranges})
    if focus_union != FOCUS or dfx_union != set(DFX):
        raise SemanticAnalysisError(
            "semantic plan does not cover all analysis stages and six DFX dimensions: "
            f"present_focus={sorted(focus_union)}, missing_focus={sorted(FOCUS - focus_union)}, "
            f"present_dfx={[value for value in DFX if value in dfx_union]}, "
            f"missing_dfx={[value for value in DFX if value not in dfx_union]}"
        )
    mapped = _list(plan.get("mapped_only"), "semantic plan mapped_only")
    mapped_paths: set[str] = set()
    for row in mapped:
        if not isinstance(row, dict) or set(row) != MAPPED_KEYS or row.get("repository") not in contract["repositories"]:
            raise SemanticAnalysisError("semantic mapped-only closure is invalid")
        path = _safe_relative(row.get("path"), "mapped-only path")
        key = row["repository"] + "\0" + path
        if key not in files or key in mapped_paths:
            raise SemanticAnalysisError("semantic mapped-only path is unknown or duplicated")
        mapped_paths.add(key); _cjk(row.get("reason"), "mapped-only reason", 4)
    limitations = _list(plan.get("depth_limitations"), "semantic plan depth_limitations")
    if contract["analysis_depth"] == "complete" and (mapped or limitations):
        raise SemanticAnalysisError("complete semantic plan must deeply cover the full source scope")
    if contract["analysis_depth"] == "fast" and not limitations:
        raise SemanticAnalysisError("fast semantic plan must state concrete depth limitations")
    for value in limitations:
        _cjk(value, "semantic plan depth limitation", 4)
    for key, value in files.items():
        intervals = sorted(covered[key])
        if key in mapped_paths:
            if intervals:
                raise SemanticAnalysisError("mapped-only source may not also be deeply assigned")
            continue
        cursor = 1
        for start, end in intervals:
            if start > cursor:
                raise SemanticAnalysisError(
                    f"semantic plan leaves an uncovered source range: "
                    f"repository={value['repository']}, path={value['path']}, lines={cursor}-{start - 1}"
                )
            cursor = max(cursor, end + 1)
        if cursor <= value["line_count"]:
            raise SemanticAnalysisError(
                f"semantic plan leaves an uncovered source range: "
                f"repository={value['repository']}, path={value['path']}, "
                f"lines={cursor}-{value['line_count']}"
            )
    return {**plan, "units": normalized_units}


def check_plan(root: Path, run_id: str, plan: Any) -> dict[str, Any]:
    """Validate a plan without freezing it and report the exact source bytes per unit."""
    normalized = validate_plan(root, run_id, plan)
    run, contract = _load_run(root, run_id)
    files = _source_files(run, contract)
    units = []
    for unit in normalized["units"]:
        _ranges, source_bytes = _ranges_for_unit(unit, files)
        units.append({
            "unit_id": unit["unit_id"], "repository": unit["repository"],
            "source_bytes": source_bytes, "limit": MAX_UNIT_SOURCE_BYTES,
        })
    return {
        "artifact_type": "semantic_plan_check", "schema_version": SCHEMA_VERSION,
        "run_id": run_id, "valid": True, "staged": False,
        "max_unit_source_bytes": MAX_UNIT_SOURCE_BYTES,
        "focus": sorted({value for unit in normalized["units"] for value in unit["focus"]}),
        "dfx": [value for value in DFX if any(value in unit["dfx"] for unit in normalized["units"])],
        "units": units,
    }


def stage_plan(root: Path, run_id: str, plan: Any) -> dict[str, Any]:
    run, _ = _load_run(root, run_id)
    normalized = validate_plan(root, run_id, plan)
    target = run / "internal/semantic-analysis/plan.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and data_runtime.read_json(target) != normalized:
        raise SemanticAnalysisError("semantic analysis plan is already frozen")
    if not target.exists():
        data_runtime.atomic_write_json(target, normalized)
    return {"run_id": run_id, "plan": str(target), "sha256": _digest(normalized), "units": len(normalized["units"])}


def _plan(run: Path) -> dict[str, Any]:
    path = run / "internal/semantic-analysis/plan.json"
    if path.is_symlink() or not path.is_file():
        raise SemanticAnalysisError("semantic analysis plan is not staged")
    return data_runtime.read_json(path)


def unit_context(root: Path, run_id: str, unit_id: str) -> dict[str, Any]:
    run, contract = _load_run(root, run_id)
    plan = validate_plan(root, run_id, _plan(run))
    matches = [row for row in plan["units"] if row["unit_id"] == unit_id]
    if len(matches) != 1:
        raise SemanticAnalysisError("semantic analysis unit is not in the frozen plan")
    unit = matches[0]; files = _source_files(run, contract); sources = []
    for row in unit["source_ranges"]:
        value = files[unit["repository"] + "\0" + row["path"]]
        text = "\n".join(value["lines"][row["line_start"] - 1:row["line_end"]])
        sources.append({**row, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "text": text})
    return {
        "request_type": "semantic_unit", "schema_version": SCHEMA_VERSION,
        "run_id": run_id, "analysis_depth": contract["analysis_depth"],
        "plan_sha256": _digest(plan), "unit": unit, "sources": sources,
        "instructions": (
            "只分析本单元当前登记仓源码，所有人类可读内容使用简体中文。输出完整代码地图、流程、分支、状态、资源、"
            "并发、错误传播、六维DFX、专项结论、SFMEA、场景和用例；不得逐行回答，不得使用模板化无问题结论。"
            "源码证据必须使用sources中的path和真实行号。"
        ),
        "output_keys": sorted(UNIT_KEYS),
    }


def _evidence(rows: Any, selected: dict[str, list[tuple[int, int]]], label: str) -> list[dict[str, Any]]:
    values = _list(rows, label, nonempty=True)
    out = []
    for row in values:
        if not isinstance(row, dict) or set(row) != EVIDENCE_KEYS:
            raise SemanticAnalysisError(f"{label} evidence closure is invalid")
        path = _safe_relative(row.get("path"), label + ".path")
        line = row.get("line")
        if (path not in selected or type(line) is not int
                or not any(start <= line <= end for start, end in selected[path])):
            raise SemanticAnalysisError(f"{label} evidence is outside the semantic unit")
        _cjk(row.get("fact"), label + ".fact", 4)
        out.append(row)
    return out


def _chinese_list(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    rows = _list(value, label, nonempty=nonempty)
    for row in rows:
        _cjk(row, label, 2)
    return rows


def validate_unit(root: Path, run_id: str, unit: Any) -> dict[str, Any]:
    run, contract = _load_run(root, run_id); plan = validate_plan(root, run_id, _plan(run))
    if not isinstance(unit, dict) or set(unit) != UNIT_KEYS:
        raise SemanticAnalysisError(
            f"semantic analysis unit result envelope is invalid: {_key_difference(unit, UNIT_KEYS)}"
        )
    if unit.get("artifact_type") != UNIT_ARTIFACT or unit.get("schema_version") != SCHEMA_VERSION \
            or unit.get("run_id") != run_id or unit.get("plan_sha256") != _digest(plan):
        raise SemanticAnalysisError(
            "semantic analysis unit result binding is invalid: "
            f"artifact_type={unit.get('artifact_type')!r}, schema_version={unit.get('schema_version')!r}, "
            f"run_id={unit.get('run_id')!r}, plan_sha256_matches={unit.get('plan_sha256') == _digest(plan)}"
        )
    unit_id = unit.get("unit_id")
    planned = next((row for row in plan["units"] if row["unit_id"] == unit_id), None)
    if planned is None:
        raise SemanticAnalysisError("semantic analysis unit result is not planned")
    selected: dict[str, list[tuple[int, int]]] = {}
    for row in planned["source_ranges"]:
        selected.setdefault(row["path"], []).append((row["line_start"], row["line_end"]))
    _cjk(unit.get("summary"), "unit.summary", 8)
    code_map = _list(unit.get("code_map"), "unit.code_map", nonempty=True)
    for row in code_map:
        if not isinstance(row, dict) or set(row) != {"title", "role", "source_evidence"}:
            raise SemanticAnalysisError("semantic unit code-map row is invalid")
        _cjk(row["title"], "code_map.title"); _cjk(row["role"], "code_map.role", 4)
        _evidence(row["source_evidence"], selected, "code_map.source_evidence")
    flows = _list(unit.get("flows"), "unit.flows", nonempty=True)
    flow_keys = {"title", "priority", "external_trigger", "registration", "preconditions", "normal_path",
                 "branches", "states", "resources", "concurrency", "errors", "recovery", "controls", "oracles",
                 "source_evidence"}
    nested = {
        "branches": {"condition", "true_path", "false_path", "effect", "controllability", "observability", "source_evidence"},
        "states": {"title", "initial_state", "transitions", "illegal_transitions", "controls", "observables", "source_evidence"},
        "resources": {"title", "acquire", "owner", "release", "abnormal_cleanup", "invariant", "limits", "recovery", "source_evidence"},
        "concurrency": {"title", "actors", "shared_state", "ordering", "race_windows", "cancellation", "recovery", "source_evidence"},
        "errors": {"title", "trigger", "propagation", "masking", "terminal_effect", "recovery", "source_evidence"},
    }
    for row in flows:
        if not isinstance(row, dict) or set(row) != flow_keys or row.get("priority") not in {"P0", "P1", "P2"}:
            raise SemanticAnalysisError("semantic unit flow closure is invalid")
        for key in ("title", "external_trigger", "registration", "preconditions"):
            _cjk(row[key], "flow." + key, 2)
        for key in ("normal_path", "recovery", "controls", "oracles"):
            _chinese_list(row[key], "flow." + key)
        _evidence(row["source_evidence"], selected, "flow.source_evidence")
        for family, keys in nested.items():
            required = family in {"branches", "states", "resources", "errors"}
            for item in _list(row[family], "flow." + family, nonempty=required):
                if not isinstance(item, dict) or set(item) != keys:
                    raise SemanticAnalysisError("semantic unit nested flow closure is invalid")
                for key, value in item.items():
                    if key == "source_evidence": _evidence(value, selected, family + ".source_evidence")
                    elif isinstance(value, list): _chinese_list(value, family + "." + key)
                    else: _cjk(value, family + "." + key, 2)
    dfx = _list(unit.get("dfx"), "unit.dfx", nonempty=True)
    if len(dfx) != len(DFX) or {row.get("dimension") for row in dfx if isinstance(row, dict)} != set(DFX):
        raise SemanticAnalysisError("semantic unit must contain all six DFX dispositions")
    for row in dfx:
        if set(row) != {"dimension", "applicable", "conclusion", "source_evidence"} or not isinstance(row["applicable"], bool):
            raise SemanticAnalysisError("semantic unit DFX closure is invalid")
        _cjk(row["conclusion"], "dfx.conclusion", 4); _evidence(row["source_evidence"], selected, "dfx.source_evidence")
    structured_keys = {
        "specialist_findings": {"title", "conclusion", "severity", "source_evidence"},
        "sfmea": {"title", "failure_mode", "cause", "local_effect", "external_effect", "detection", "recovery", "severity", "source_evidence"},
        "scenarios": {"title", "drivers", "failure_mechanism", "external_construction", "injection", "oracle", "source_evidence"},
        "test_cases": {"title", "scenario_title", "preconditions", "steps", "expected", "observation", "cleanup", "source_evidence"},
    }
    for key in ("specialist_findings", "sfmea", "scenarios", "test_cases"):
        rows = _list(unit.get(key), "unit." + key)
        for row in rows:
            if not isinstance(row, dict) or set(row) != structured_keys[key]:
                raise SemanticAnalysisError("semantic unit structured finding is invalid")
            for field, value in row.items():
                if field == "source_evidence": _evidence(value, selected, key + ".source_evidence")
                elif field in {"severity", "scenario_title"}: _text(value, key + "." + field)
                elif isinstance(value, list): _chinese_list(value, key + "." + field)
                else: _cjk(value, key + "." + field, 2)
    limitations = _chinese_list(unit.get("depth_limitations"), "unit.depth_limitations", nonempty=False)
    if contract["analysis_depth"] == "complete" and limitations:
        raise SemanticAnalysisError("complete semantic unit result may not be truncated")
    if contract["analysis_depth"] == "fast" and not limitations:
        raise SemanticAnalysisError("fast semantic unit result must state depth limitations")
    unresolved = _list(unit.get("unresolved"), "unit.unresolved")
    for row in unresolved:
        if not isinstance(row, dict) or set(row) != {"reason", "impact", "next_action", "source_evidence"}:
            raise SemanticAnalysisError("semantic unit unresolved closure is invalid")
        for key in ("reason", "impact", "next_action"): _cjk(row[key], "unresolved." + key, 4)
        _evidence(row["source_evidence"], selected, "unresolved.source_evidence")
    return unit


def stage_unit(root: Path, run_id: str, unit: Any) -> dict[str, Any]:
    run, _ = _load_run(root, run_id); normalized = validate_unit(root, run_id, unit)
    target = run / "internal/semantic-analysis/units" / f"{normalized['unit_id']}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and data_runtime.read_json(target) != normalized:
        raise SemanticAnalysisError("semantic analysis unit result conflicts with frozen result")
    if not target.exists(): data_runtime.atomic_write_json(target, normalized)
    return {"run_id": run_id, "unit_id": normalized["unit_id"], "sha256": _digest(normalized), "path": str(target)}


def risk_cards(root: Path, run_id: str) -> list[dict[str, Any]]:
    """Project frozen SFMEA rows into the canonical product risk ledger."""
    run, contract = _load_run(root, run_id)
    plan = validate_plan(root, run_id, _plan(run))
    cards: list[dict[str, Any]] = []
    for planned in plan["units"]:
        path = run / "internal/semantic-analysis/units" / f"{planned['unit_id']}.json"
        if path.is_symlink() or not path.is_file():
            raise SemanticAnalysisError("semantic analysis has incomplete units")
        unit = validate_unit(root, run_id, data_runtime.read_json(path))
        dimensions = [row["dimension"] for row in unit["dfx"] if row["applicable"]] or ["可靠性与一致性"]
        for index, row in enumerate(unit["sfmea"], 1):
            evidence = [{"location": f"{item['path']}:{item['line']}", "observation": item["fact"]}
                        for item in row["source_evidence"]]
            cards.append({
                "artifact_type": "risk_card", "schema_version": "1.0",
                "risk_id": f"RISK-{unit['unit_id']}-M{index}", "title": row["title"],
                "dfx": dimensions, "severity": row["severity"], "confidence": "high",
                "trigger": row["cause"], "propagation": row["local_effect"],
                "external_impact": row["external_effect"], "observation": row["detection"],
                "recovery": row["recovery"], "translation_status": "Graybox-ready",
                "test_explanation": "通过外部触发、结果观测和恢复步骤验证该失效模式。",
                "source_scope": {"repository": planned["repository"],
                                 "ref": contract["repository_commits"][planned["repository"]]},
                "inference": None, "instrumentation_request": None,
                "evidence": evidence, "related_risk_ids": [], "status": "open",
            })
    if not cards:
        raise SemanticAnalysisError("semantic analysis must produce at least one SFMEA risk")
    return cards


def _source_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"path": row["path"], "line": row["line"], "fact": row["fact"]} for row in rows]


def _evidence_label(rows: list[dict[str, Any]]) -> str:
    return "、".join(f"{row['path']}:{row['line']}" for row in rows)


def assemble_model(root: Path, run_id: str) -> dict[str, Any]:
    """Merge all staged semantic units into the existing formal model."""
    from runtime import runctl

    run, contract = _load_run(root, run_id); plan = validate_plan(root, run_id, _plan(run))
    unit_dir = run / "internal/semantic-analysis/units"
    units: list[dict[str, Any]] = []
    for planned in plan["units"]:
        path = unit_dir / f"{planned['unit_id']}.json"
        if path.is_symlink() or not path.is_file():
            raise SemanticAnalysisError("semantic analysis has incomplete units")
        units.append(validate_unit(root, run_id, data_runtime.read_json(path)))
    expected_names = {f"{row['unit_id']}.json" for row in plan["units"]}
    actual_names = {path.name for path in unit_dir.iterdir()} if unit_dir.is_dir() else set()
    if actual_names != expected_names:
        raise SemanticAnalysisError("semantic analysis unit directory is not exact")

    model: dict[str, Any] = {
        "artifact_type": "analysis_model", "schema_version": "1.0", "run_id": run_id,
        "analysis_depth": contract["analysis_depth"], "source_commits": contract["repository_commits"],
        "evidence_consumption": [], "entrypoints": [], "flows": [], "branches": [], "states": [],
        "resources": [], "concurrency": [], "error_chains": [], "model_applicability": [],
        "collection_applicability": [], "scenario_candidates": [], "sfmea": [],
        "test_scenarios": [], "test_flows": [], "test_cases": [], "traceability": [],
        "coverage_dispositions": [], "depth_limitations": [], "unresolved": [],
    }
    dfx_rows: dict[str, list[dict[str, Any]]] = {name: [] for name in DFX}
    coverage: list[tuple[str, str, str, list[str]]] = []
    for unit in units:
        uid = unit["unit_id"]
        flow_ids = [f"{uid}-FLOW-F{index}" for index in range(1, len(unit["flows"]) + 1)]
        ranges = [f"{row['path']}:{row['line_start']}-{row['line_end']}"
                  for row in next(item for item in plan["units"] if item["unit_id"] == uid)["source_ranges"]]
        model["evidence_consumption"].append({
            "evidence_id": f"{uid}-EVIDENCE", "source_ref": "、".join(ranges), "status": "parsed",
            "parser": "PANGEA语义分析单元", "consumed_ranges": ranges,
            "conclusions": [unit["summary"]], "used_by": flow_ids,
            "unread_ranges": [], "limitations": unit["depth_limitations"],
        })
        for dfx in unit["dfx"]: dfx_rows[dfx["dimension"]].append(dfx)
        model["depth_limitations"].extend(unit["depth_limitations"])
        for index, item in enumerate(unit["unresolved"], 1):
            model["unresolved"].append({
                "item_id": f"{uid}-OPEN-N{index}", "reason": item["reason"],
                "impact": item["impact"], "next_action": item["next_action"],
            })
        scenario_ids = [f"{uid}-SC-S{index}" for index in range(1, len(unit["scenarios"]) + 1)]
        case_ids = [f"{uid}-TC-C{index}" for index in range(1, len(unit["test_cases"]) + 1)]
        unit_risk_ids = [f"RISK-{uid}-M{index}" for index in range(1, len(unit["sfmea"]) + 1)]
        scenario_by_title = {row["title"]: scenario_ids[index] for index, row in enumerate(unit["scenarios"])}
        cases_by_scenario: dict[str, list[str]] = {sid: [] for sid in scenario_ids}
        for index, case in enumerate(unit["test_cases"]):
            sid = scenario_by_title.get(case["scenario_title"])
            if sid is None: raise SemanticAnalysisError("semantic test case references unknown scenario title")
            cases_by_scenario[sid].append(case_ids[index])
        for index, flow in enumerate(unit["flows"], 1):
            fid = f"{uid}-FLOW-F{index}"; eid = f"{uid}-EP-E{index}"
            branch_ids = [f"{uid}-BR-F{index}-B{n}" for n in range(1, len(flow["branches"]) + 1)]
            state_ids = [f"{uid}-STATE-F{index}-S{n}" for n in range(1, len(flow["states"]) + 1)]
            resource_ids = [f"{uid}-RES-F{index}-R{n}" for n in range(1, len(flow["resources"]) + 1)]
            concurrency_ids = [f"{uid}-CON-F{index}-C{n}" for n in range(1, len(flow["concurrency"]) + 1)]
            error_ids = [f"{uid}-ERR-F{index}-E{n}" for n in range(1, len(flow["errors"]) + 1)]
            evidence = _source_evidence(flow["source_evidence"])
            model["entrypoints"].append({
                "entrypoint_id": eid, "title": flow["title"], "external_trigger": flow["external_trigger"],
                "registration": flow["registration"], "preconditions": flow["preconditions"],
                "flow_ids": [fid], "status": "analyzed", "disposition_reason": "已按登记源码完成语义分析",
                "source_evidence": evidence,
            })
            model["flows"].append({
                "flow_id": fid, "title": flow["title"], "priority": flow["priority"],
                "external_trigger": flow["external_trigger"], "entrypoint_id": eid,
                "registration": flow["registration"], "preconditions": flow["preconditions"],
                "normal_path": flow["normal_path"], "decisions": branch_ids,
                "abnormal_paths": [row["effect"] for row in flow["branches"]],
                "state_changes": state_ids, "resource_lifecycle": resource_ids,
                "timeout_retry_recovery": flow["recovery"], "concurrency": concurrency_ids,
                "error_propagation": error_ids,
                "latent_or_secondary_failures": [row["terminal_effect"] for row in flow["errors"]],
                "blackbox_controls": flow["controls"], "oracles": flow["oracles"],
                "source_evidence": evidence, "status": "analyzed",
                "disposition_reason": "已形成入口、主路径、分支与可观测判据",
            })
            for n, row in enumerate(flow["branches"]):
                model["branches"].append({
                    "branch_id": branch_ids[n], "flow_id": fid, "condition": row["condition"],
                    "true_path": row["true_path"], "false_path": row["false_path"],
                    "external_effect": row["effect"], "controllability": row["controllability"],
                    "observability": row["observability"], "source_evidence": _source_evidence(row["source_evidence"]),
                    "status": "analyzed", "disposition_reason": "已分析条件两侧路径及外部影响",
                })
            for n, row in enumerate(flow["states"]):
                model["states"].append({
                    "state_id": state_ids[n], "title": row["title"], "initial_state": row["initial_state"],
                    "transitions": row["transitions"], "illegal_transitions": row["illegal_transitions"],
                    "external_controls": row["controls"], "observables": row["observables"],
                    "source_evidence": _source_evidence(row["source_evidence"]), "status": "analyzed",
                    "disposition_reason": "已建立状态迁移、非法迁移与观测关系",
                })
            for n, row in enumerate(flow["resources"]):
                model["resources"].append({
                    "resource_id": resource_ids[n], "title": row["title"], "acquire": row["acquire"],
                    "owner": row["owner"], "release": row["release"], "abnormal_cleanup": row["abnormal_cleanup"],
                    "invariant": row["invariant"], "limits": row["limits"], "recovery": row["recovery"],
                    "source_evidence": _source_evidence(row["source_evidence"]), "status": "analyzed",
                    "disposition_reason": "已分析申请、所有权、释放、异常清理与恢复",
                })
            for n, row in enumerate(flow["concurrency"]):
                model["concurrency"].append({
                    "concurrency_id": concurrency_ids[n], "title": row["title"], "actors": row["actors"],
                    "shared_state": row["shared_state"], "ordering": row["ordering"],
                    "race_windows": row["race_windows"], "cancellation": row["cancellation"],
                    "recovery": row["recovery"], "source_evidence": _source_evidence(row["source_evidence"]),
                    "status": "analyzed", "disposition_reason": "已分析执行主体、共享状态、时序窗口与恢复",
                })
            for n, row in enumerate(flow["errors"]):
                model["error_chains"].append({
                    "chain_id": error_ids[n], "title": row["title"], "trigger": row["trigger"],
                    "propagation": row["propagation"], "masking": row["masking"],
                    "terminal_effect": row["terminal_effect"], "recovery": row["recovery"],
                    "source_evidence": _source_evidence(row["source_evidence"]), "status": "analyzed",
                    "disposition_reason": "已分析触发、传播、终态影响与恢复路径",
                })
            for item_type, item_ids in (("entrypoint", [eid]), ("flow", [fid]), ("branch", branch_ids),
                                        ("state", state_ids), ("resource", resource_ids),
                                        ("concurrency", concurrency_ids), ("error_chain", error_ids)):
                coverage.extend((item_type, value, _evidence_label(flow["source_evidence"]), case_ids)
                                for value in item_ids)
        candidate_ids: list[str] = []
        for index, row in enumerate(unit["scenarios"]):
            sid = scenario_ids[index]; cid = f"{uid}-CAND-D{index + 1}"; candidate_ids.append(cid)
            targets = [sid, *cases_by_scenario[sid]]
            model["scenario_candidates"].append({
                "candidate_id": cid, "title": row["title"], "drivers": row["drivers"],
                "source_refs": [_evidence_label(row["source_evidence"])],
                "failure_mechanism": row["failure_mechanism"],
                "external_construction": row["external_construction"], "injection": row["injection"],
                "oracle": row["oracle"], "disposition": "retained", "target_ids": targets,
            })
            matching = [case for case in unit["test_cases"] if case["scenario_title"] == row["title"]]
            observations = sorted({value for case in matching for value in [case["observation"]]}) or [row["oracle"]]
            cleanup = matching[0]["cleanup"] if matching else "按模块恢复流程清理测试环境"
            model["test_scenarios"].append({
                "scenario_id": sid, "title": row["title"], "source_candidate_ids": [cid],
                "risk_ids": unit_risk_ids,
                "preconditions": matching[0]["preconditions"] if matching else "模块处于可接收请求的稳定状态",
                "trigger": row["external_construction"], "expected": row["oracle"],
                "observations": observations, "cleanup": cleanup,
            })
            model["test_flows"].append({
                "test_flow_id": f"{uid}-TF-T{index + 1}", "title": row["title"] + "测试流程",
                "scenario_id": sid, "steps": [case_step for case in matching for case_step in case["steps"]]
                    or [row["external_construction"], row["oracle"]],
                "oracles": [row["oracle"]], "cleanup": cleanup, "test_case_ids": cases_by_scenario[sid],
            })
            coverage.append(("candidate", cid, _evidence_label(row["source_evidence"]), cases_by_scenario[sid]))
        for index, row in enumerate(unit["test_cases"]):
            sid = scenario_by_title[row["scenario_title"]]
            model["test_cases"].append({
                "case_id": case_ids[index], "title": row["title"], "scenario_id": sid,
                "risk_ids": unit_risk_ids,
                "preconditions": row["preconditions"], "steps": row["steps"], "expected": row["expected"],
                "observation": row["observation"], "cleanup": row["cleanup"],
                "source_refs": [_evidence_label(row["source_evidence"])],
            })
        for index, row in enumerate(unit["sfmea"], 1):
            model["sfmea"].append({
                "sfmea_id": f"{uid}-SF-M{index}", "title": row["title"],
                "source_refs": [_evidence_label(row["source_evidence"])], "failure_mode": row["failure_mode"],
                "cause": row["cause"], "local_effect": row["local_effect"],
                "external_effect": row["external_effect"], "detection": row["detection"],
                "recovery": row["recovery"], "severity": row["severity"],
                "scenario_ids": scenario_ids, "test_case_ids": case_ids,
            })
        for index, sid in enumerate(scenario_ids):
            sources = flow_ids + [f"{uid}-SF-M{n}" for n in range(1, len(unit["sfmea"]) + 1)]
            targets = [sid, f"{uid}-TF-T{index + 1}", *cases_by_scenario[sid]]
            model["traceability"].append({
                "trace_id": f"{uid}-TR-X{index + 1}", "source_ids": sources,
                "target_ids": targets, "rationale": "由登记仓源码流程、异常机理和SFMEA推导测试场景与用例",
            })
    for dimension in DFX:
        rows = dfx_rows[dimension]
        if not rows: raise SemanticAnalysisError("semantic units do not cover all six DFX dimensions")
        applicable = any(row["applicable"] for row in rows)
        model["model_applicability"].append({
            "dfx": dimension, "applicable": applicable,
            "reason": "；".join(dict.fromkeys(row["conclusion"] for row in rows)),
            "evidence": "、".join(dict.fromkeys(_evidence_label(row["source_evidence"]) for row in rows)),
        })
    family_dfx = {"states": "功能与状态", "resources": "资源与规格", "concurrency": "并发与异常",
                  "error_chains": "可靠性与一致性", "scenario_candidates": "功能与状态"}
    for family, dimension in family_dfx.items():
        applicable = bool(model[family]); rows = dfx_rows[dimension]
        model["collection_applicability"].append({
            "collection": family, "disposition": "applicable" if applicable else "not_applicable",
            "reason": ("登记仓源码已形成该集合的结构化分析工件" if applicable
                       else "登记仓源码证据未显示该集合在确认范围内适用"),
            "evidence": list(dict.fromkeys(_evidence_label(row["source_evidence"]) for row in rows)),
        })
    for item_type, item_id, evidence, covered_by in coverage:
        model["coverage_dispositions"].append({
            "item_type": item_type, "item_id": item_id, "outcome": "analyzed",
            "evidence": evidence, "covered_by": covered_by, "missing_work": [],
        })
    model["depth_limitations"] = list(dict.fromkeys([*plan["depth_limitations"], *model["depth_limitations"]]))
    if (not any(unit["specialist_findings"] for unit in units) or not model["scenario_candidates"]
            or not model["sfmea"] or not model["test_scenarios"] or not model["test_cases"]):
        raise SemanticAnalysisError("semantic analysis must produce specialist findings, scenarios, SFMEA, and test cases")
    try:
        return runctl._validate_analysis_model(model, contract, run_id)
    except runctl.RunCtlError as exc:
        raise SemanticAnalysisError(str(exc)) from exc
