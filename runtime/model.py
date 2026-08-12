"""Single machine-readable contract for the PANGEA semantic pipeline."""
from __future__ import annotations

from typing import Any


FORMAT_VERSION = "2"
STATES = (
    "draft", "confirmed", "prepared", "planned", "analyzing", "composed",
    "reviewing", "completed", "failed",
)
NORMAL_TRANSITIONS = {
    "draft": "confirmed",
    "confirmed": "prepared",
    "prepared": "planned",
    "planned": "analyzing",
    "analyzing": "composed",
    "composed": "reviewing",
    "reviewing": "completed",
}
DFX_DIMENSIONS = (
    "功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性",
)
DFX_FINDING_TYPES = ("strength", "risk", "limitation", "gap", "recommendation")
TESTABILITY = ("blackbox_ready", "graybox_ready", "blocked")
SEVERITIES = ("Critical", "High", "Medium", "Low")
CONFIDENCE = ("high", "medium", "low")

SEMANTIC_FAMILIES = (
    "behaviors", "flows", "branches", "states", "resources", "concurrency",
    "error_chains", "risk_candidates", "gaps",
)
SEMANTIC_KEYS = {
    "behaviors": ["id", "title", "category", "description", "inputs", "outputs", "related_ids", "evidence"],
    "flows": ["id", "title", "trigger", "steps", "result", "related_ids", "evidence"],
    "branches": ["id", "title", "condition", "outcomes", "related_ids", "evidence"],
    "states": ["id", "title", "from_state", "event", "to_state", "action", "related_ids", "evidence"],
    "resources": ["id", "title", "resource", "acquire", "owner", "release", "error_release", "related_ids", "evidence"],
    "concurrency": ["id", "title", "contexts", "shared_state", "coordination", "ordering", "related_ids", "evidence"],
    "error_chains": ["id", "title", "trigger", "propagation", "handling", "recovery", "effect", "related_ids", "evidence"],
    "risk_candidates": ["id", "title", "condition", "propagation", "effect", "observation", "recovery", "confidence", "related_ids", "evidence"],
    "gaps": ["id", "title", "reason", "needed_evidence", "related_ids", "evidence"],
}

MODULE_CONTRACT = {
    "object_keys": ["type", "run_id", "source_revision", "summary", "drop_ids", "relationship_updates", "field_updates", "decisions"],
    "type": "module_composition",
    "relationship_keys": ["object_id", "related_ids"],
    "field_update_keys": ["object_id", "updates", "reason"],
    "decision_keys": ["object_ids", "reason"],
    "field_types": {
        "context.candidate": "compact projections retaining id, title, key facts, related_ids and source ranges; exact source lines are in context.sources",
        "summary": "non-empty list[string]", "drop_ids": "list[string]",
        "relationship_updates": "list[{object_id:string, related_ids:list[string]}]",
        "field_updates": "list[{object_id:string, updates:object, reason:string}] for replacing stale slice-local wording; updates keys use original semantic field names from semantic_keys, never the compact projection key facts",
        "semantic_keys": SEMANTIC_KEYS,
        "decisions": "list[{object_ids:non-empty list[string], reason:string}] covering dropped objects only; do not repeat field_updates or relationship_updates here",
    },
    "rules": [
        "Every dropped object has exactly one concrete cross-slice evidence decision.",
        "Every decisions.object_ids item is in drop_ids; updates are not decisions.",
        "error_chains and risk_candidates are required module semantic families; downstream adjudication is not a reason to drop them.",
        "Use field_updates to resolve stale slice-local visibility claims when another slice supplies the missing fact.",
        "Do not produce DFX, canonical risks, SFMEA or tests.",
    ],
}
DFX_CONTRACT = {
    "object_keys": ["type", "run_id", "semantic_revision", "assessments"],
    "type": "dfx_result",
    "assessment_keys": ["dimension", "summary", "findings"],
    "finding_keys": ["id", "type", "title", "description", "related_ids", "evidence", "risk_candidate"],
    "risk_candidate_keys": ["condition", "propagation", "effect", "observation", "recovery", "confidence", "missing_evidence"],
    "dimensions": list(DFX_DIMENSIONS),
    "finding_types": list(DFX_FINDING_TYPES),
    "field_types": {
        "context.semantics": "all module semantic objects projected to id, title, key facts, relationships and source ranges",
        "assessments": "exactly six assessment objects, one per dimensions value",
        "assessment.summary": "non-empty string", "assessment.findings": "list[finding], may be empty",
        "finding.type": "strength | risk | limitation | gap | recommendation",
        "finding.related_ids": "list of supplied semantic_ids", "finding.evidence": "list[source evidence]",
        "finding.risk_candidate": "complete risk candidate object for risk; null for other finding types",
        "finding.risk_candidate.missing_evidence": "list[string], use [] when no evidence is missing",
    },
    "rules": ["Do not create findings to satisfy a count. The same causal chain is one candidate even across dimensions."],
}
RISK_CONTRACT = {
    "object_keys": ["type", "run_id", "semantic_revision", "risk_decisions", "risks", "sfmea"],
    "type": "risk_result",
    "decision_keys": ["candidate_id", "status", "reason", "risk_id"],
    "risk_keys": [
        "id", "title", "dfx_dimensions", "severity", "confidence", "condition", "propagation",
        "effect", "observation", "recovery", "testability", "missing_evidence", "related_ids", "evidence",
    ],
    "sfmea_keys": [
        "id", "failure_mode", "mechanism", "effect", "severity", "occurrence", "detection",
        "observations", "recommendation", "risk_ids", "evidence",
    ],
    "field_types": {
        "risk_decisions": "one decision per supplied candidate_id",
        "decision.status": "confirmed | dismissed", "decision.risk_id": "canonical risk id for confirmed; null for dismissed",
        "risk.id/title/condition/propagation/effect/observation/recovery": "non-empty strings",
        "risk.testability": "blackbox_ready | graybox_ready | blocked",
        "risk.dfx_dimensions": "non-empty list using only 功能与状态, 资源与规格, 性能与压力, 并发与异常, 升级与兼容, 可靠性与一致性",
        "risk.severity": "Critical | High | Medium | Low",
        "risk.confidence": "high | medium | low",
        "risk.missing_evidence": "non-empty list[string] for blocked; list[string] otherwise",
        "risk.related_ids": "non-empty list[semantic id]", "risk.evidence": "non-empty list[source evidence]",
        "sfmea.id/failure_mode/mechanism/effect/recommendation": "non-empty strings",
        "sfmea severity/occurrence/detection": "integers from 1 through 10",
        "sfmea.observations": "non-empty list[string]", "sfmea.risk_ids": "non-empty list[canonical risk id]",
        "sfmea.evidence": "non-empty list[source evidence]",
    },
    "rules": [
        "Testing difficulty is never a reason to dismiss an evidence-backed candidate.",
        "Every confirmed candidate maps to one canonical risk; candidates may merge into the same risk.",
        "Every canonical risk remains visible in SFMEA, including blocked risks.",
    ],
}
RISK_ADJUDICATION_CONTRACT = {
    "object_keys": ["type", "run_id", "semantic_revision", "decisions"],
    "type": "risk_adjudication_result",
    "decision_keys": ["candidate_id", "status", "reason"],
    "field_types": {
        "decisions": "exactly one decision per context.candidate_ids",
        "decision.status": "confirmed | dismissed",
    },
    "rules": ["Testability is never a reason to dismiss an evidence-backed candidate."],
}
RISK_SYNTHESIS_CONTRACT = {
    "object_keys": ["type", "run_id", "semantic_revision", "candidate_risk_mappings", "risks", "sfmea"],
    "type": "risk_synthesis_result",
    "mapping_keys": ["candidate_id", "risk_id"],
    "risk_keys": RISK_CONTRACT["risk_keys"],
    "sfmea_keys": RISK_CONTRACT["sfmea_keys"],
    "field_types": {
        "candidate_risk_mappings": "one mapping per confirmed candidate id; many candidates may map to one risk",
        **{key: value for key, value in RISK_CONTRACT["field_types"].items() if not key.startswith("decision") and key != "risk_decisions"},
    },
    "rules": ["Do not reconsider adjudications. Every confirmed candidate maps to one canonical risk and every risk appears in SFMEA."],
}
TEST_DESIGN_CONTRACT = {
    "object_keys": ["type", "run_id", "semantic_revision", "scenarios", "test_cases"],
    "type": "test_design_result",
    "scenario_keys": [
        "id", "title", "risk_ids", "semantic_ids", "parameters", "preconditions", "action",
        "expected", "observation", "evidence",
    ],
    "test_case_keys": [
        "id", "title", "scenario_id", "risk_ids", "semantic_ids", "parameters", "preconditions",
        "steps", "expected", "observations", "cleanup",
    ],
    "field_types": {
        "context.semantics": "compact projections of all behaviors plus objects directly linked by supplied risks",
        "scenario.id/title/action/expected/observation": "non-empty strings",
        "scenario.risk_ids": "list[ready canonical risk id], may be empty for behavior-only scenarios",
        "scenario.semantic_ids": "non-empty list[supplied semantic id]",
        "scenario.parameters": "object whose values are non-empty list[string]",
        "scenario.preconditions": "non-empty list[string]", "scenario.evidence": "non-empty list[source evidence]",
        "test_case.id/title/scenario_id": "non-empty strings",
        "test_case.risk_ids": "list[ready canonical risk id], may be empty for behavior-only cases",
        "test_case.semantic_ids": "non-empty list[supplied semantic id]",
        "test_case.parameters": "object whose values are non-empty string",
        "test_case preconditions/steps/expected/observations/cleanup": "non-empty list[string]",
    },
    "rules": [
        "Derive tests from supplied confirmed risks and behaviors; tests never decide whether a risk exists.",
        "Blocked risks have no executable cases; every ready risk has at least one derived case.",
    ],
}
DESIGN_CONTRACT = {
    "object_keys": ["type", "run_id", "semantic_revision", "risk_decisions", "risks", "sfmea", "scenarios", "test_cases"],
    "type": "design_result",
    "decision_keys": RISK_CONTRACT["decision_keys"], "risk_keys": RISK_CONTRACT["risk_keys"],
    "sfmea_keys": RISK_CONTRACT["sfmea_keys"], "scenario_keys": TEST_DESIGN_CONTRACT["scenario_keys"],
    "test_case_keys": TEST_DESIGN_CONTRACT["test_case_keys"],
    "field_types": {**RISK_CONTRACT["field_types"], **TEST_DESIGN_CONTRACT["field_types"]},
    "rules": [*RISK_CONTRACT["rules"], *TEST_DESIGN_CONTRACT["rules"]],
}
REVIEW_CONTRACT = {
    "object_keys": ["type", "run_id", "analysis_revision", "verdict", "metrics", "summary", "findings"],
    "type": "review_result",
    "metric_keys": [
        "semantic_objects", "dfx_findings", "risk_candidates", "confirmed_candidates",
        "dismissed_candidates", "canonical_risks", "blocked_risks", "scenarios", "test_cases",
    ],
    "finding_keys": ["code", "severity", "message", "object_ids"],
    "verdicts": ["pass", "fail"],
    "severities": ["error", "warning"],
    "field_types": {
        "metrics": "exact copy of context.expected_metrics with non-negative integer values",
        "summary": "non-empty qualitative string; do not restate or recalculate metrics",
        "findings": "list[finding], may be empty", "finding.object_ids": "list[string]",
    },
    "rules": ["A pass verdict cannot contain an error finding.", "Structured metrics are authoritative over prose."],
}


class ContractError(ValueError):
    """A public PANGEA contract was violated."""


def new_run(run_id: str, task: dict[str, str], timestamp: str) -> dict[str, Any]:
    return {
        "format_version": FORMAT_VERSION,
        "run_id": run_id,
        "revision": 1,
        "state": "draft",
        "failed_stage": None,
        "task": task,
        "source": {"files": [], "source_map": None, "methods": []},
        "plan": {"path": None, "total": 0, "completed": 0, "failed": 0},
        "analysis": {"path": None, "revision": None},
        "review": {"path": None, "analysis_revision": None, "verdict": None},
        "report": {"markdown": None, "html": None, "analysis_revision": None},
        "last_error": None,
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def validate_run(value: Any) -> dict[str, Any]:
    keys = {
        "format_version", "run_id", "revision", "state", "failed_stage", "task", "source",
        "plan", "analysis", "review", "report", "last_error", "created_at", "updated_at",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError("run.json keys do not match the PANGEA contract")
    if value["format_version"] != FORMAT_VERSION:
        raise ContractError("unsupported run format_version")
    _text(value["run_id"], "run_id")
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ContractError("revision must be a positive integer")
    if value["state"] not in STATES:
        raise ContractError("run state is invalid")
    task = value["task"]
    if not isinstance(task, dict) or set(task) != {"kind", "repository", "scope", "target"}:
        raise ContractError("task must contain kind, repository, scope and target")
    if task["kind"] != "module" or not all(isinstance(task[key], str) and task[key] for key in task):
        raise ContractError("task values are invalid")
    source = value["source"]
    if not isinstance(source, dict) or set(source) != {"files", "source_map", "methods"}:
        raise ContractError("source metadata is invalid")
    if value["state"] == "failed" and value["failed_stage"] not in NORMAL_TRANSITIONS:
        raise ContractError("failed Run must record the failed stage")
    if value["state"] != "failed" and value["failed_stage"] is not None:
        raise ContractError("non-failed Run cannot retain failed_stage")
    return value


def slice_result_contract(slice_id: str, method_names: list[str]) -> dict[str, Any]:
    """Exact worker output contract injected into a frozen slice context."""
    return {
        "object_keys": ["type", "run_id", "revision", "slice_id", "summary", "applied_skills", *SEMANTIC_FAMILIES],
        "type": "semantic_fragment",
        "id_prefix": slice_id + "-",
        "method_names": method_names,
        "applied_skill_keys": ["name", "reason"],
        "evidence_keys": ["path", "line_start", "line_end", "fact"],
        "semantic_keys": SEMANTIC_KEYS,
        "field_types": {
            "summary": "non-empty string", "applied_skills": "non-empty list[{name:string, reason:string}]",
            "all semantic collections": "lists; empty is allowed when the type is absent from this slice",
            "all ids": "unique strings starting with id_prefix", "all related_ids": "ids declared in this fragment",
            "all evidence": "list[{path:string,line_start:int,line_end:int,fact:string}] inside context.sources",
            "behavior.category": "non-empty semantic label such as application_entry, build or lifecycle",
            "behavior inputs/outputs": "non-empty list[string]", "flow steps": "non-empty list[string]",
            "branch outcomes": "non-empty list[string]", "concurrency contexts": "non-empty list[string]",
            "concurrency shared_state/coordination/ordering": "non-empty strings; join multiple facts into one precise string",
            "gap evidence": "list[source evidence], may be empty", "risk_candidate confidence": "high | medium | low",
        },
        "rules": [
            "Return semantic objects only; DFX, canonical risks, SFMEA, scenarios and test cases are forbidden.",
            "Every factual object uses evidence inside context.sources and explicit related_ids.",
            "Risk candidates are hypotheses for module-level adjudication, not canonical risks.",
            "Use only method_names supplied by Runtime and return exactly one JSON object.",
        ],
    }


def _object(value: Any, keys: list[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ContractError(f"{label} keys do not match the public contract")
    return value


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label} must be non-empty text")
    return value.strip()


def _nullable_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _texts(value: Any, label: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ContractError(f"{label} must be a {'possibly empty' if allow_empty else 'non-empty'} list")
    for index, item in enumerate(value):
        _text(item, f"{label}[{index}]")
    if len(value) != len(set(value)):
        raise ContractError(f"{label} contains duplicates")
    return value


def _allowed_ranges(context: dict[str, Any]) -> dict[str, list[tuple[int, int]]]:
    allowed: dict[str, list[tuple[int, int]]] = {}
    for source in context.get("sources", []):
        if "line_start" in source:
            allowed.setdefault(source["path"], []).append((source["line_start"], source["line_end"]))
        elif source.get("lines"):
            numbers = [row["line"] for row in source["lines"]]
            allowed.setdefault(source["path"], []).append((min(numbers), max(numbers)))
    return allowed


def _evidence(value: Any, context: dict[str, Any], label: str, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ContractError(f"{label} must contain source evidence")
    allowed = _allowed_ranges(context)
    for index, item in enumerate(value):
        row = _object(item, ["path", "line_start", "line_end", "fact"], f"{label}[{index}]")
        path = _text(row["path"], f"{label}[{index}].path")
        start, end = row["line_start"], row["line_end"]
        if type(start) is not int or type(end) is not int or start > end:
            raise ContractError(f"{label}[{index}] has an invalid line range")
        if path not in allowed or not any(low <= start <= end <= high for low, high in allowed[path]):
            raise ContractError(f"{label}[{index}] is outside supplied source evidence")
        _text(row["fact"], f"{label}[{index}].fact")
    return value


def _parameters(value: Any, label: str, *, list_values: bool) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    for key, item in value.items():
        _text(key, label + " key")
        if list_values:
            _texts(item, f"{label}.{key}")
        else:
            _text(item, f"{label}.{key}")
    return value


def _register(row: dict[str, Any], label: str, ids: set[str], prefix: str | None = None) -> str:
    identifier = _text(row["id"], label + ".id")
    if prefix and not identifier.startswith(prefix):
        raise ContractError(f"{label}.id must start with {prefix}")
    if identifier in ids:
        raise ContractError(f"{label}.id is duplicated")
    ids.add(identifier)
    return identifier


def _validate_semantic_row(
    family: str, row: dict[str, Any], context: dict[str, Any], label: str, ids: set[str], prefix: str | None,
) -> None:
    _object(row, SEMANTIC_KEYS[family], label)
    _register(row, label, ids, prefix)
    _text(row["title"], label + ".title")
    _texts(row["related_ids"], label + ".related_ids", allow_empty=True)
    _evidence(row["evidence"], context, label + ".evidence", allow_empty=family == "gaps")
    text_fields = {
        "behaviors": ("description",),
        "flows": ("trigger", "result"),
        "branches": ("condition",),
        "states": ("from_state", "event", "to_state", "action"),
        "resources": ("resource", "acquire", "owner", "release", "error_release"),
        "concurrency": ("shared_state", "coordination", "ordering"),
        "error_chains": ("trigger", "propagation", "handling", "recovery", "effect"),
        "risk_candidates": ("condition", "propagation", "effect", "observation", "recovery"),
        "gaps": ("reason", "needed_evidence"),
    }[family]
    for key in text_fields:
        _text(row[key], label + "." + key)
    list_fields = {
        "behaviors": ("inputs", "outputs"),
        "flows": ("steps",),
        "branches": ("outcomes",),
        "concurrency": ("contexts",),
    }.get(family, ())
    for key in list_fields:
        _texts(row[key], label + "." + key)
    if family == "behaviors":
        _text(row["category"], label + ".category")
    if family == "risk_candidates" and row["confidence"] not in CONFIDENCE:
        raise ContractError(label + ".confidence is invalid")


def validate_slice_result(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    contract = context["contract"]
    result = _object(value, contract["object_keys"], "semantic fragment")
    if result["type"] != contract["type"]:
        raise ContractError("semantic fragment type is invalid")
    if result["run_id"] != context["run_id"] or result["revision"] != context["revision"]:
        raise ContractError("semantic fragment identity does not match context")
    if result["slice_id"] != context["slice"]["slice_id"]:
        raise ContractError("semantic fragment slice_id does not match context")
    _text(result["summary"], "summary")
    if not isinstance(result["applied_skills"], list) or not result["applied_skills"]:
        raise ContractError("applied_skills must be a non-empty list")
    seen_skills: set[str] = set()
    allowed_skills = set(contract["method_names"])
    for index, item in enumerate(result["applied_skills"]):
        row = _object(item, contract["applied_skill_keys"], f"applied_skills[{index}]")
        name = _text(row["name"], f"applied_skills[{index}].name")
        _text(row["reason"], f"applied_skills[{index}].reason")
        if name not in allowed_skills or name in seen_skills:
            raise ContractError("applied_skills contains unavailable or duplicate method")
        seen_skills.add(name)
    ids: set[str] = set()
    count = 0
    for family in SEMANTIC_FAMILIES:
        rows = result[family]
        if not isinstance(rows, list):
            raise ContractError(f"{family} must be a list")
        count += len(rows)
        for index, row in enumerate(rows):
            _validate_semantic_row(family, row, context, f"{family}[{index}]", ids, contract["id_prefix"])
    if count == 0:
        raise ContractError("semantic fragment must contain at least one semantic object or gap")
    for family in SEMANTIC_FAMILIES:
        for index, row in enumerate(result[family]):
            unknown = set(row["related_ids"]) - ids
            if unknown:
                raise ContractError(f"{family}[{index}].related_ids references unknown ids")
    return result


# Compatibility name for callers from the first v1 draft.
validate_analysis_result = validate_slice_result


def validate_module_composition(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = _object(value, MODULE_CONTRACT["object_keys"], "module composition")
    if result["type"] != MODULE_CONTRACT["type"]:
        raise ContractError("module composition type is invalid")
    if result["run_id"] != context["run_id"] or result["source_revision"] != context["source_revision"]:
        raise ContractError("module composition identity does not match context")
    _texts(result["summary"], "module summary")
    _texts(result["drop_ids"], "drop_ids", allow_empty=True)
    known = {row["id"] for family in SEMANTIC_FAMILIES for row in context["candidate"][family]}
    if set(result["drop_ids"]) - known:
        raise ContractError("module composition drops unknown ids")
    if not isinstance(result["relationship_updates"], list):
        raise ContractError("relationship_updates must be a list")
    updated: set[str] = set()
    remaining = known - set(result["drop_ids"])
    for index, item in enumerate(result["relationship_updates"]):
        row = _object(item, MODULE_CONTRACT["relationship_keys"], f"relationship_updates[{index}]")
        object_id = _text(row["object_id"], f"relationship_updates[{index}].object_id")
        _texts(row["related_ids"], f"relationship_updates[{index}].related_ids", allow_empty=True)
        if object_id not in remaining or object_id in updated or set(row["related_ids"]) - remaining:
            raise ContractError("relationship update references unknown, dropped or duplicate ids")
        updated.add(object_id)
    if not isinstance(result["field_updates"], list):
        raise ContractError("field_updates must be a list")
    field_updated: set[str] = set()
    family_by_id = {row["id"]: family for family in SEMANTIC_FAMILIES for row in context["candidate"][family]}
    for index, item in enumerate(result["field_updates"]):
        label = f"field_updates[{index}]"
        row = _object(item, MODULE_CONTRACT["field_update_keys"], label)
        object_id = _text(row["object_id"], label + ".object_id")
        if object_id not in remaining or object_id in field_updated:
            raise ContractError("field update references unknown, dropped or duplicate id")
        field_updated.add(object_id)
        if not isinstance(row["updates"], dict) or not row["updates"]:
            raise ContractError(label + ".updates must be a non-empty object")
        allowed = set(SEMANTIC_KEYS[family_by_id[object_id]]) - {"id", "related_ids", "evidence"}
        if set(row["updates"]) - allowed:
            raise ContractError(label + ".updates contains immutable or unknown fields")
        for key, value in row["updates"].items():
            if isinstance(value, str):
                _text(value, label + ".updates." + key)
            elif isinstance(value, list):
                _texts(value, label + ".updates." + key)
            else:
                raise ContractError(label + ".updates values must be text or list[text]")
        _text(row["reason"], label + ".reason")
    if not isinstance(result["decisions"], list):
        raise ContractError("decisions must be a list")
    decided: set[str] = set()
    for index, item in enumerate(result["decisions"]):
        row = _object(item, MODULE_CONTRACT["decision_keys"], f"decisions[{index}]")
        _texts(row["object_ids"], f"decisions[{index}].object_ids")
        reason = _text(row["reason"], f"decisions[{index}].reason")
        if any(marker in reason for marker in ("按契约禁止", "不纳入最终模块", "不纳入最终模型", "下游阶段")):
            raise ContractError("composition cannot drop semantic evidence as a downstream concern")
        if set(row["object_ids"]) - set(result["drop_ids"]):
            raise ContractError("composition decision may mention only dropped ids")
        decided.update(row["object_ids"])
    if decided != set(result["drop_ids"]):
        raise ContractError("every dropped id must have one composition decision")
    return result


validate_composition = validate_module_composition


def validate_dfx(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = _object(value, DFX_CONTRACT["object_keys"], "DFX result")
    if result["type"] != DFX_CONTRACT["type"]:
        raise ContractError("DFX result type is invalid")
    if result["run_id"] != context["run_id"] or result["semantic_revision"] != context["semantic_revision"]:
        raise ContractError("DFX result identity does not match context")
    if not isinstance(result["assessments"], list):
        raise ContractError("DFX assessments must be a list")
    dimensions: set[str] = set()
    finding_ids: set[str] = set()
    semantic_ids = set(context["semantic_ids"])
    for index, item in enumerate(result["assessments"]):
        row = _object(item, DFX_CONTRACT["assessment_keys"], f"assessments[{index}]")
        dimension = _text(row["dimension"], f"assessments[{index}].dimension")
        if dimension not in DFX_DIMENSIONS or dimension in dimensions:
            raise ContractError("DFX dimension is invalid or duplicated")
        dimensions.add(dimension)
        _text(row["summary"], f"assessments[{index}].summary")
        if not isinstance(row["findings"], list):
            raise ContractError("DFX findings must be a list and may be empty")
        for number, finding in enumerate(row["findings"]):
            label = f"assessments[{index}].findings[{number}]"
            finding = _object(finding, DFX_CONTRACT["finding_keys"], label)
            _register(finding, label, finding_ids)
            if finding["type"] not in DFX_FINDING_TYPES:
                raise ContractError(label + ".type is invalid")
            _text(finding["title"], label + ".title")
            _text(finding["description"], label + ".description")
            _texts(finding["related_ids"], label + ".related_ids", allow_empty=True)
            if set(finding["related_ids"]) - semantic_ids:
                raise ContractError(label + " references unknown semantic ids")
            _evidence(finding["evidence"], context, label + ".evidence", allow_empty=finding["type"] in {"gap", "recommendation"})
            candidate = finding["risk_candidate"]
            if finding["type"] == "risk":
                candidate = _object(candidate, DFX_CONTRACT["risk_candidate_keys"], label + ".risk_candidate")
                for key in ("condition", "propagation", "effect", "observation", "recovery"):
                    _text(candidate[key], label + ".risk_candidate." + key)
                if candidate["confidence"] not in CONFIDENCE:
                    raise ContractError(label + ".risk_candidate.confidence is invalid")
                _texts(candidate["missing_evidence"], label + ".risk_candidate.missing_evidence", allow_empty=True)
            elif candidate is not None:
                raise ContractError(label + ".risk_candidate is allowed only for risk findings")
    if dimensions != set(DFX_DIMENSIONS):
        raise ContractError("DFX must contain each module dimension exactly once")
    return result


def validate_design(value: Any, context: dict[str, Any], *, require_tests: bool = True) -> dict[str, Any]:
    result = _object(value, DESIGN_CONTRACT["object_keys"], "design result")
    if result["type"] != DESIGN_CONTRACT["type"]:
        raise ContractError("design result type is invalid")
    if result["run_id"] != context["run_id"] or result["semantic_revision"] != context["semantic_revision"]:
        raise ContractError("design result identity does not match context")
    for key in ("risk_decisions", "risks", "sfmea", "scenarios", "test_cases"):
        if not isinstance(result[key], list):
            raise ContractError(key + " must be a list")
    candidate_ids = {row["candidate_id"] for row in context["candidates"]}
    decision_ids: set[str] = set()
    confirmed: dict[str, str] = {}
    for index, item in enumerate(result["risk_decisions"]):
        label = f"risk_decisions[{index}]"
        row = _object(item, DESIGN_CONTRACT["decision_keys"], label)
        candidate_id = _text(row["candidate_id"], label + ".candidate_id")
        if candidate_id not in candidate_ids or candidate_id in decision_ids:
            raise ContractError("risk decision candidate is unknown or duplicated")
        decision_ids.add(candidate_id)
        if row["status"] not in {"confirmed", "dismissed"}:
            raise ContractError(label + ".status is invalid")
        _text(row["reason"], label + ".reason")
        risk_id = _nullable_text(row["risk_id"], label + ".risk_id")
        if row["status"] == "confirmed":
            if risk_id is None:
                raise ContractError("confirmed candidate must map to a canonical risk")
            confirmed[candidate_id] = risk_id
        elif risk_id is not None:
            raise ContractError("dismissed candidate cannot map to a risk")
    if decision_ids != candidate_ids:
        raise ContractError("every risk candidate must receive one decision")

    semantic_ids = set(context["semantic_ids"])
    risk_ids: set[str] = set()
    for index, item in enumerate(result["risks"]):
        label = f"risks[{index}]"
        row = _object(item, DESIGN_CONTRACT["risk_keys"], label)
        _register(row, label, risk_ids)
        _text(row["title"], label + ".title")
        _texts(row["dfx_dimensions"], label + ".dfx_dimensions")
        if set(row["dfx_dimensions"]) - set(DFX_DIMENSIONS):
            raise ContractError(label + ".dfx_dimensions is invalid")
        if row["severity"] not in SEVERITIES or row["confidence"] not in CONFIDENCE:
            raise ContractError(label + " severity or confidence is invalid")
        for key in ("condition", "propagation", "effect", "observation", "recovery"):
            _text(row[key], label + "." + key)
        if row["testability"] not in TESTABILITY:
            raise ContractError(label + ".testability is invalid")
        _texts(row["missing_evidence"], label + ".missing_evidence", allow_empty=True)
        if row["testability"] == "blocked" and not row["missing_evidence"]:
            raise ContractError("blocked risk must preserve missing_evidence")
        _texts(row["related_ids"], label + ".related_ids")
        if set(row["related_ids"]) - semantic_ids:
            raise ContractError(label + " references unknown semantic ids")
        _evidence(row["evidence"], context, label + ".evidence")
    if set(confirmed.values()) != risk_ids:
        raise ContractError("confirmed decisions and canonical risks do not match")

    sfmea_ids: set[str] = set()
    for index, item in enumerate(result["sfmea"]):
        label = f"sfmea[{index}]"
        row = _object(item, DESIGN_CONTRACT["sfmea_keys"], label)
        _register(row, label, sfmea_ids)
        for key in ("failure_mode", "mechanism", "effect", "recommendation"):
            _text(row[key], label + "." + key)
        for key in ("severity", "occurrence", "detection"):
            if type(row[key]) is not int or not 1 <= row[key] <= 10:
                raise ContractError(label + "." + key + " must be 1..10")
        _texts(row["observations"], label + ".observations")
        _texts(row["risk_ids"], label + ".risk_ids")
        if set(row["risk_ids"]) - risk_ids:
            raise ContractError(label + " references unknown risks")
        _evidence(row["evidence"], context, label + ".evidence")

    sfmea_risks = {risk_id for row in result["sfmea"] for risk_id in row["risk_ids"]}
    if risk_ids - sfmea_risks:
        raise ContractError("every canonical risk must remain visible in SFMEA")

    scenario_ids: set[str] = set()
    for index, item in enumerate(result["scenarios"]):
        label = f"scenarios[{index}]"
        row = _object(item, DESIGN_CONTRACT["scenario_keys"], label)
        _register(row, label, scenario_ids)
        _text(row["title"], label + ".title")
        _texts(row["risk_ids"], label + ".risk_ids", allow_empty=True)
        _texts(row["semantic_ids"], label + ".semantic_ids")
        if set(row["risk_ids"]) - risk_ids or set(row["semantic_ids"]) - semantic_ids:
            raise ContractError(label + " references unknown risks or semantics")
        _parameters(row["parameters"], label + ".parameters", list_values=True)
        _texts(row["preconditions"], label + ".preconditions")
        for key in ("action", "expected", "observation"):
            _text(row[key], label + "." + key)
        _evidence(row["evidence"], context, label + ".evidence")

    tested_risks: set[str] = set()
    tested_scenarios: set[str] = set()
    scenarios = {row["id"]: row for row in result["scenarios"]}
    case_ids: set[str] = set()
    for index, item in enumerate(result["test_cases"]):
        label = f"test_cases[{index}]"
        row = _object(item, DESIGN_CONTRACT["test_case_keys"], label)
        _register(row, label, case_ids)
        _text(row["title"], label + ".title")
        scenario_id = _text(row["scenario_id"], label + ".scenario_id")
        if scenario_id not in scenario_ids:
            raise ContractError(label + " references unknown scenario")
        _texts(row["risk_ids"], label + ".risk_ids", allow_empty=True)
        _texts(row["semantic_ids"], label + ".semantic_ids")
        if set(row["risk_ids"]) - risk_ids or set(row["semantic_ids"]) - semantic_ids:
            raise ContractError(label + " references unknown risks or semantics")
        scenario = scenarios[scenario_id]
        if not set(row["risk_ids"]).issubset(scenario["risk_ids"]) or not set(row["semantic_ids"]).issubset(scenario["semantic_ids"]):
            raise ContractError(label + " relationships exceed its scenario")
        _parameters(row["parameters"], label + ".parameters", list_values=False)
        for key in ("preconditions", "steps", "expected", "observations", "cleanup"):
            _texts(row[key], label + "." + key)
        tested_risks.update(row["risk_ids"])
        tested_scenarios.add(scenario_id)
    if require_tests and scenario_ids - tested_scenarios:
        raise ContractError("every scenario must have a test case")
    ready = {row["id"] for row in result["risks"] if row["testability"] != "blocked"}
    blocked = risk_ids - ready
    if require_tests and ready - tested_risks:
        raise ContractError("testable risks must have derived test cases")
    if require_tests and blocked & tested_risks:
        raise ContractError("blocked risks cannot be presented as executable test cases")
    return result


def validate_risk_result(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = _object(value, RISK_CONTRACT["object_keys"], "risk result")
    if result["type"] != RISK_CONTRACT["type"]:
        raise ContractError("risk result type is invalid")
    aggregate = {
        "type": "design_result", "run_id": result["run_id"],
        "semantic_revision": result["semantic_revision"],
        "risk_decisions": result["risk_decisions"], "risks": result["risks"],
        "sfmea": result["sfmea"], "scenarios": [], "test_cases": [],
    }
    validate_design(aggregate, context, require_tests=False)
    return result


def validate_risk_adjudication(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = _object(value, RISK_ADJUDICATION_CONTRACT["object_keys"], "risk adjudication result")
    if result["type"] != RISK_ADJUDICATION_CONTRACT["type"]:
        raise ContractError("risk adjudication result type is invalid")
    if result["run_id"] != context["run_id"] or result["semantic_revision"] != context["semantic_revision"]:
        raise ContractError("risk adjudication identity does not match context")
    if not isinstance(result["decisions"], list):
        raise ContractError("risk adjudication decisions must be a list")
    expected = set(context["candidate_ids"])
    actual: set[str] = set()
    for index, item in enumerate(result["decisions"]):
        label = f"decisions[{index}]"
        row = _object(item, RISK_ADJUDICATION_CONTRACT["decision_keys"], label)
        candidate_id = _text(row["candidate_id"], label + ".candidate_id")
        if candidate_id not in expected or candidate_id in actual:
            raise ContractError("risk adjudication decision is unknown or duplicated")
        actual.add(candidate_id)
        if row["status"] not in {"confirmed", "dismissed"}:
            raise ContractError(label + ".status is invalid")
        _text(row["reason"], label + ".reason")
    if actual != expected:
        raise ContractError("every adjudication candidate must receive one decision")
    return result


def validate_risk_synthesis(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = _object(value, RISK_SYNTHESIS_CONTRACT["object_keys"], "risk synthesis result")
    if result["type"] != RISK_SYNTHESIS_CONTRACT["type"]:
        raise ContractError("risk synthesis result type is invalid")
    if result["run_id"] != context["run_id"] or result["semantic_revision"] != context["semantic_revision"]:
        raise ContractError("risk synthesis identity does not match context")
    if not isinstance(result["candidate_risk_mappings"], list):
        raise ContractError("candidate_risk_mappings must be a list")
    confirmed = {
        row["candidate_id"] for row in context["adjudications"] if row["status"] == "confirmed"
    }
    mappings: dict[str, str] = {}
    for index, item in enumerate(result["candidate_risk_mappings"]):
        label = f"candidate_risk_mappings[{index}]"
        row = _object(item, RISK_SYNTHESIS_CONTRACT["mapping_keys"], label)
        candidate_id = _text(row["candidate_id"], label + ".candidate_id")
        risk_id = _text(row["risk_id"], label + ".risk_id")
        if candidate_id not in confirmed or candidate_id in mappings:
            raise ContractError("risk synthesis mapping is unknown or duplicated")
        mappings[candidate_id] = risk_id
    if set(mappings) != confirmed:
        raise ContractError("every confirmed candidate must map to one canonical risk")
    risk_result = {
        "type": "risk_result", "run_id": result["run_id"],
        "semantic_revision": result["semantic_revision"],
        "risk_decisions": [
            {
                "candidate_id": row["candidate_id"], "status": row["status"],
                "reason": row["reason"],
                "risk_id": mappings[row["candidate_id"]] if row["status"] == "confirmed" else None,
            }
            for row in context["adjudications"]
        ],
        "risks": result["risks"], "sfmea": result["sfmea"],
    }
    validation_context = {
        **context,
        "candidates": [{"candidate_id": row["candidate_id"]} for row in context["adjudications"]],
    }
    validate_risk_result(risk_result, validation_context)
    return result


def validate_test_design(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    result = _object(value, TEST_DESIGN_CONTRACT["object_keys"], "test design result")
    if result["type"] != TEST_DESIGN_CONTRACT["type"]:
        raise ContractError("test design result type is invalid")
    if result["run_id"] != context["run_id"] or result["semantic_revision"] != context["semantic_revision"]:
        raise ContractError("test design result identity does not match context")
    risk_result = context["risk_result"]
    aggregate = {
        "type": "design_result", "run_id": result["run_id"],
        "semantic_revision": result["semantic_revision"],
        "risk_decisions": risk_result["risk_decisions"], "risks": risk_result["risks"],
        "sfmea": risk_result["sfmea"], "scenarios": result["scenarios"],
        "test_cases": result["test_cases"],
    }
    validation_context = {
        **context,
        "candidates": [{"candidate_id": row["candidate_id"]} for row in risk_result["risk_decisions"]],
    }
    validate_design(aggregate, validation_context)
    prefix = context.get("output_id_prefix")
    if prefix and any(
        not row["id"].startswith(prefix)
        for key in ("scenarios", "test_cases") for row in result[key]
    ):
        raise ContractError("test design ids must use output_id_prefix")
    if context.get("batch_kind") == "behavior_only" and (
        len(result["scenarios"]) != 1 or len(result["test_cases"]) != 1
    ):
        raise ContractError("behavior-only test batch must contain exactly one scenario and one test case")
    return result


def validate_review(
    value: Any, *, run_id: str, analysis_revision: int, expected_metrics: dict[str, int],
) -> dict[str, Any]:
    result = _object(value, REVIEW_CONTRACT["object_keys"], "review result")
    if result["type"] != REVIEW_CONTRACT["type"]:
        raise ContractError("review result type is invalid")
    if result["run_id"] != run_id or result["analysis_revision"] != analysis_revision:
        raise ContractError("review result identity does not match current analysis")
    if result["verdict"] not in REVIEW_CONTRACT["verdicts"]:
        raise ContractError("review verdict is invalid")
    metrics = _object(result["metrics"], REVIEW_CONTRACT["metric_keys"], "review metrics")
    if any(type(item) is not int or item < 0 for item in metrics.values()) or metrics != expected_metrics:
        raise ContractError("review metrics do not match Runtime expected_metrics")
    _text(result["summary"], "review summary")
    if not isinstance(result["findings"], list):
        raise ContractError("review findings must be a list")
    for index, item in enumerate(result["findings"]):
        row = _object(item, REVIEW_CONTRACT["finding_keys"], f"review findings[{index}]")
        _text(row["code"], f"review findings[{index}].code")
        if row["severity"] not in REVIEW_CONTRACT["severities"]:
            raise ContractError("review finding severity is invalid")
        _text(row["message"], f"review findings[{index}].message")
        _texts(row["object_ids"], f"review findings[{index}].object_ids", allow_empty=True)
    if result["verdict"] == "pass" and any(row["severity"] == "error" for row in result["findings"]):
        raise ContractError("pass review cannot contain error findings")
    return result
