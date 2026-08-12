"""Module composition, one-shot DFX, risk/test design and independent review."""
from __future__ import annotations

import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from runtime import analysis as execution
from runtime import engine, methods, model


class ReviewError(RuntimeError):
    pass


RISK_BATCH_SIZE = 3
BEHAVIOR_TEST_BATCH_SIZE = 1


def _role_prompt(context: dict[str, Any], role: str) -> str:
    instructions = {
        "composer": (
            "Merge the frozen slice semantic objects into one module model. Remove only objects disproved by "
            "cross-slice evidence or slice-local gaps resolved elsewhere. You may drop any semantic family, including "
            "behavior, but explain every drop with concrete cross-slice evidence. Preserve error_chains and "
            "risk_candidates as module semantics unless cross-slice evidence specifically disproves or duplicates them; "
            "never drop them merely because a downstream stage will adjudicate risks. Correct only explicit related_ids. "
            "Use field_updates when full-module evidence makes slice-local phrases such as 'outside this slice' stale; "
            "replace only the affected original semantic field and explain the cross-slice evidence. The compact candidate "
            "shows fields as facts strings, but field_updates.updates must use original keys such as trigger, result, reason, "
            "needed_evidence, description or handling; never use a key named facts. decisions contains only dropped IDs and "
            "their drop reasons; never add decisions for field_updates or relationship_updates. Do not produce DFX, canonical risks, SFMEA or tests."
        ),
        "dfx-analyst": (
            "Analyze the frozen module semantics once across all six dimensions. Each dimension has summary and a "
            "findings array containing zero or more strength, risk, limitation, gap or recommendation findings. "
            "Do not create one finding merely to fill a dimension. A risk finding must include a complete candidate. "
            "risk_candidate.missing_evidence is always a JSON array of strings; use [] when none is missing. "
            "Every finding evidence item has exactly path, line_start, line_end and fact; never add fact_extra or any other key. "
            "Do not supply POSIX, platform, library or framework semantics from memory. When such external semantics "
            "are absent from the frozen context, record a limitation or gap instead of asserting them as facts."
        ),
        "risk-adjudicator": (
            "Decide every candidate in this small frozen batch as confirmed or dismissed using evidence, independent "
            "of testability. Return exactly one decision for every ID in context.candidate_ids. Do not merge candidates, "
            "assign canonical risk IDs, produce SFMEA, scenarios or tests. Candidates with the same causal chain must "
            "receive consistent decisions. Do not supply POSIX, SPDK, library or framework semantics from memory; if "
            "external implementation evidence is missing, explain that boundary instead of inventing a fact."
        ),
        "risk-analyst": (
            "Do not reconsider the completed adjudications. Merge confirmed candidates with the same causal chain into "
            "canonical risks and map every confirmed candidate ID to one risk. Preserve untestable risks as "
            "testability=blocked with a non-empty missing_evidence array. Produce SFMEA for every canonical risk. "
            "Canonical risk.severity must be Critical, High, Medium or Low; only SFMEA severity, occurrence and "
            "detection use integers 1..10. risk.dfx_dimensions may use only the exact six names in "
            "context.contract.field_types; never invent a new dimension. SFMEA observations is always a non-empty "
            "JSON array of strings. Do not produce scenarios or test cases."
        ),
        "test-designer": (
            "Derive scenarios and test cases from the supplied validated canonical risks and relevant frozen behaviors. "
            "Do not decide, merge, rewrite or delete risks. Do not create executable cases for blocked risks. "
            "Every ready risk must be covered by at least one precisely bound test case. Use context.output_id_prefix "
            "for every scenario and test-case id so independently generated batches can be merged without collisions. "
            "Every scenario evidence item has exactly path, line_start, line_end and fact; do not copy projected "
            "semantic objects or add any other evidence key. For build tests using make -C, never use $(CURDIR) as a "
            "shell argument or assume its expansion scope. Compute an explicit absolute repository path in a separate "
            "precondition/setup step, then pass that literal or shell variable to make so the intended comparison can trigger."
            " Each scenarios and test_cases array element is one complete object with every contract key exactly once; "
            "never split one scenario across two array elements, and close the current object before starting the next. "
            "Never place a second id key inside one object. When context.batch_kind is "
            "behavior_only, return exactly one primary scenario and exactly one parameterized test case for that behavior; "
            "put variations in parameters instead of expanding repetitive scenarios or cases."
        ),
        "reviewer": (
            "Independently review evidence accuracy, flows, branches, states, resource and concurrency lifecycles, "
            "error propagation, DFX findings, candidate decisions, blocked-risk preservation, SFMEA and executable tests. "
            "Do not rewrite the analysis. Copy context.expected_metrics exactly into metrics; do not restate or "
            "recalculate those counts in summary. Record a finding when evidence is insufficient."
        ),
    }[role]
    return (
        "Do not call tools or read files: every allowed input is embedded below. "
        + instructions
        + " Use Simplified Chinese for human-readable fields. Before returning, verify internally that the complete "
          "response parses once as JSON, every top-level key appears exactly once, and every value follows the types "
          "in context.contract.field_types. Do not emit partial JSON. Return exactly one JSON object without Markdown "
          "or code fences; the first character is { and the last is }. Follow context.contract exactly.\n\n"
        + json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    )


def _invoke_role(
    context: dict[str, Any], role: str, *, model_name: str, timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    command = [
        "opencode", "run", "--pure", "--agent", role, "--model", model_name,
        "--format", "json", "--title", f"PANGEA {role} {context['run_id']}", _role_prompt(context, role),
    ]
    try:
        with tempfile.TemporaryDirectory(prefix=f"pangea-opencode-{role}-") as working_directory:
            return subprocess.run(
                command,
                cwd=working_directory,
                env=execution.isolated_opencode_environment(role),
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
    except subprocess.TimeoutExpired as exc:
        raise ReviewError(f"OpenCode {role} timed out after {timeout_seconds}s") from exc


def invoke_composer(root: Path, context: dict[str, Any], *, model_name: str = execution.DEFAULT_MODEL, timeout_seconds: int = 1800) -> subprocess.CompletedProcess[str]:
    return _invoke_role(context, "composer", model_name=model_name, timeout_seconds=timeout_seconds)


def invoke_dfx(root: Path, context: dict[str, Any], *, model_name: str = execution.DEFAULT_MODEL, timeout_seconds: int = 1800) -> subprocess.CompletedProcess[str]:
    return _invoke_role(context, "dfx-analyst", model_name=model_name, timeout_seconds=timeout_seconds)


def invoke_adjudicator(root: Path, context: dict[str, Any], *, model_name: str = execution.DEFAULT_MODEL, timeout_seconds: int = 1800) -> subprocess.CompletedProcess[str]:
    return _invoke_role(context, "risk-adjudicator", model_name=model_name, timeout_seconds=timeout_seconds)


def invoke_risk(root: Path, context: dict[str, Any], *, model_name: str = execution.DEFAULT_MODEL, timeout_seconds: int = 1800) -> subprocess.CompletedProcess[str]:
    return _invoke_role(context, "risk-analyst", model_name=model_name, timeout_seconds=timeout_seconds)


def invoke_designer(root: Path, context: dict[str, Any], *, model_name: str = execution.DEFAULT_MODEL, timeout_seconds: int = 1800) -> subprocess.CompletedProcess[str]:
    return _invoke_role(context, "test-designer", model_name=model_name, timeout_seconds=timeout_seconds)


def invoke_reviewer(root: Path, context: dict[str, Any], *, model_name: str = execution.DEFAULT_MODEL, timeout_seconds: int = 1800) -> subprocess.CompletedProcess[str]:
    return _invoke_role(context, "reviewer", model_name=model_name, timeout_seconds=timeout_seconds)


def _run_call(
    root: Path,
    directory: Path,
    context: dict[str, Any],
    *,
    role: str,
    call_id: str,
    context_name: str,
    result_name: str,
    validator: Callable[[Any, dict[str, Any]], dict[str, Any]],
    model_name: str,
    timeout_seconds: int,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    engine.atomic_write_json(directory / context_name, context)
    receipt_path = execution.next_execution_path(directory, call_id)
    receipt: dict[str, Any] = {
        "type": "opencode_execution", "run_id": context["run_id"], "call_id": call_id,
        "analysis_revision": context.get("semantic_revision"), "returncode": None, "stderr": "",
        "raw_output": "", "events": [], "session_ids": [], "finish_reasons": [],
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0},
        "tools": [], "status": "failed", "error": None,
    }
    try:
        process = runner(root, context, model_name=model_name, timeout_seconds=timeout_seconds)
        receipt.update({
            "returncode": process.returncode,
            "stderr": process.stderr,
            "raw_output": process.stdout,
            **execution.preview_opencode_jsonl(process.stdout),
        })
        if process.returncode != 0:
            raise ReviewError(f"OpenCode {role} exited with code {process.returncode}: {process.stderr.strip()}")
        parsed = execution.parse_opencode_jsonl(process.stdout, role=role)
        receipt.update({key: parsed[key] for key in ("events", "session_ids", "finish_reasons", "tokens")})
        value = validator(execution._one_json_object(parsed["final_text"], role=role), context)
        receipt["status"] = "succeeded"
        engine.atomic_write_json(directory / result_name, value)
        return value
    except (ReviewError, execution.AnalysisError, model.ContractError) as exc:
        receipt["error"] = str(exc)
        raise ReviewError(str(exc)) from exc
    finally:
        engine.atomic_write_json(receipt_path, receipt)


def _checkpoint(
    path: Path,
    context: dict[str, Any],
    validator: Callable[[Any, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any] | None:
    """Reuse only a staged result that still satisfies the current frozen context."""
    if not path.is_file():
        return None
    try:
        return validator(engine.read_json(path), context)
    except (OSError, ValueError, model.ContractError):
        return None


def _combined_candidate(results: list[dict[str, Any]]) -> dict[str, Any]:
    candidate: dict[str, Any] = {
        "summary": [row["summary"] for row in results],
        "applied_skills": sorted({item["name"] for row in results for item in row["applied_skills"]}),
    }
    ids: set[str] = set()
    for family in model.SEMANTIC_FAMILIES:
        candidate[family] = []
        for result in results:
            for row in result[family]:
                if row["id"] in ids:
                    raise ReviewError(f"duplicate semantic id across slices: {row['id']}")
                ids.add(row["id"])
                candidate[family].append(row)
    return candidate


def _composition_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Compact slice objects for global reconciliation while retaining exact evidence anchors."""
    value: dict[str, Any] = {
        "summary": [f"{len(candidate['summary'])} 个冻结 slice 待全局关系复核。"],
        "applied_skills": candidate["applied_skills"],
    }
    for family in model.SEMANTIC_FAMILIES:
        value[family] = []
        for row in candidate[family]:
            omitted = {"id", "title", "related_ids", "evidence", "inputs", "outputs", "steps"}
            facts = [
                f"{key}={row[key]}" for key in model.SEMANTIC_KEYS[family]
                if key not in omitted and key in row
            ]
            value[family].append({
                "id": row["id"], "title": row["title"], "facts": facts,
                "related_ids": row["related_ids"],
                "evidence": [
                    {key: item[key] for key in ("path", "line_start", "line_end")}
                    for item in row["evidence"]
                ],
            })
    return value


def _apply_composition(candidate: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    dropped = set(result["drop_ids"])
    updates = {row["object_id"]: row["related_ids"] for row in result["relationship_updates"]}
    field_updates = {row["object_id"]: row["updates"] for row in result["field_updates"]}
    value: dict[str, Any] = {
        "summary": result["summary"],
        "applied_skills": candidate["applied_skills"],
    }
    for family in model.SEMANTIC_FAMILIES:
        value[family] = []
        for original in candidate[family]:
            if original["id"] in dropped:
                continue
            row = dict(original)
            row.update(field_updates.get(row["id"], {}))
            row["related_ids"] = updates.get(row["id"], [identifier for identifier in row["related_ids"] if identifier not in dropped])
            value[family].append(row)
    known = {row["id"] for family in model.SEMANTIC_FAMILIES for row in value[family]}
    for family in model.SEMANTIC_FAMILIES:
        for row in value[family]:
            if set(row["related_ids"]) - known:
                raise ReviewError(f"composed {row['id']} has dangling semantic relationships")
    return value


def _evidence_sources(directory: Path, families: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    requested: dict[str, set[int]] = {}
    for rows in families:
        for row in rows:
            for evidence in row.get("evidence", []):
                requested.setdefault(evidence["path"], set()).update(range(evidence["line_start"], evidence["line_end"] + 1))
    sources: list[dict[str, Any]] = []
    for path, numbers in sorted(requested.items()):
        lines = (directory / "source" / path).read_text(encoding="utf-8").splitlines()
        ordered = sorted(numbers)
        start = previous = ordered[0]
        for number in [*ordered[1:], None]:
            if number is not None and number == previous + 1:
                previous = number
                continue
            sources.append({
                "path": path, "line_start": start, "line_end": previous,
                "lines": [{"line": item, "text": lines[item - 1]} for item in range(start, previous + 1)],
            })
            if number is not None:
                start = previous = number
    return sources


def _semantic_ids(semantics: dict[str, Any]) -> list[str]:
    return [row["id"] for family in model.SEMANTIC_FAMILIES for row in semantics[family]]


def _test_semantics(semantics: dict[str, Any], risks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Project behavior plus directly risk-linked semantics without repeating the full module model."""
    relevant = {identifier for risk in risks for identifier in risk["related_ids"]}
    relevant.update(row["id"] for row in semantics["behaviors"])
    projected: dict[str, list[dict[str, Any]]] = {}
    omitted = {"related_ids", "evidence", "id", "title"}
    for family in model.SEMANTIC_FAMILIES:
        if family in {"risk_candidates", "gaps"}:
            continue
        projected[family] = []
        for row in semantics[family]:
            if row["id"] not in relevant:
                continue
            facts = [f"{key}={row[key]}" for key in model.SEMANTIC_KEYS[family] if key not in omitted]
            projected[family].append({
                "id": row["id"], "title": row["title"], "facts": facts, "evidence": row["evidence"],
            })
    return projected


def _risk_subset(risk_result: dict[str, Any], risk_ids: set[str]) -> dict[str, Any]:
    """Create a self-consistent ready-risk subset for one independent test-design batch."""
    decisions = [row for row in risk_result["risk_decisions"] if row["risk_id"] in risk_ids]
    risks = [row for row in risk_result["risks"] if row["id"] in risk_ids]
    sfmea = []
    for row in risk_result["sfmea"]:
        related = [risk_id for risk_id in row["risk_ids"] if risk_id in risk_ids]
        if related:
            sfmea.append({**row, "risk_ids": related})
    return {
        "type": "risk_result", "run_id": risk_result["run_id"],
        "semantic_revision": risk_result["semantic_revision"],
        "risk_decisions": decisions, "risks": risks, "sfmea": sfmea,
    }


def _test_design_batches(
    semantics: dict[str, Any], risk_result: dict[str, Any], base_context: dict[str, Any], directory: Path,
) -> list[dict[str, Any]]:
    """Partition test derivation by ready risk and uncovered behaviors without deleting blocked risks."""
    batches: list[tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]] = []
    ready = [row for row in risk_result["risks"] if row["testability"] != "blocked"]
    behavior_ids_covered: set[str] = set()
    all_behaviors = {row["id"]: row for row in semantics["behaviors"]}
    for risk in ready:
        subset = _risk_subset(risk_result, {risk["id"]})
        projected = _test_semantics(semantics, [risk])
        behavior_ids_covered.update(set(all_behaviors) & set(risk["related_ids"]))
        batches.append((subset, projected))
    remaining = [row for row in semantics["behaviors"] if row["id"] not in behavior_ids_covered]
    for start in range(0, len(remaining), BEHAVIOR_TEST_BATCH_SIZE):
        selected = remaining[start:start + BEHAVIOR_TEST_BATCH_SIZE]
        projected = {family: [] for family in model.SEMANTIC_FAMILIES if family not in {"risk_candidates", "gaps"}}
        projected["behaviors"] = [
            {"id": row["id"], "title": row["title"],
             "facts": [f"category={row['category']}", f"description={row['description']}"],
             "evidence": row["evidence"]}
            for row in selected
        ]
        empty_risks = {
            "type": "risk_result", "run_id": risk_result["run_id"],
            "semantic_revision": risk_result["semantic_revision"],
            "risk_decisions": [], "risks": [], "sfmea": [],
        }
        batches.append((empty_risks, projected))
    contexts: list[dict[str, Any]] = []
    for index, (risk_subset, projected) in enumerate(batches, 1):
        sources = _evidence_sources(directory, [risk_subset["risks"], *projected.values()])
        contexts.append({
            **base_context, "risk_result": risk_subset, "semantics": projected,
            "sources": sources,
            "output_id_prefix": f"TD{index:03d}-",
            "batch_kind": "risk" if risk_subset["risks"] else "behavior_only",
        })
    return contexts


def _dfx_semantics(semantics: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Project every module object for one global DFX pass without duplicating full prose and source facts."""
    projected: dict[str, list[dict[str, Any]]] = {}
    omitted = {"id", "title", "related_ids", "evidence", "inputs", "outputs", "steps"}
    for family in model.SEMANTIC_FAMILIES:
        projected[family] = []
        for row in semantics[family]:
            facts = [
                f"{key}={row[key]}" for key in model.SEMANTIC_KEYS[family]
                if key not in omitted and key in row
            ]
            projected[family].append({
                "id": row["id"], "title": row["title"], "facts": facts,
                "related_ids": row["related_ids"],
                "evidence": [
                    {
                        "path": item["path"], "line_start": item["line_start"], "line_end": item["line_end"],
                        "fact": f"该范围支持语义对象 {row['id']}：{row['title']}。",
                    }
                    for item in row["evidence"]
                ],
            })
    return projected


def _risk_candidates(semantics: dict[str, Any], dfx: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [{
        "candidate_id": row["id"], "origin": "slice", "title": row["title"],
        "condition": row["condition"], "propagation": row["propagation"], "effect": row["effect"],
        "observation": row["observation"], "recovery": row["recovery"], "confidence": row["confidence"],
        "missing_evidence": [], "dfx_dimensions": [], "related_ids": row["related_ids"], "evidence": row["evidence"],
    } for row in semantics["risk_candidates"]]
    for assessment in dfx["assessments"]:
        for finding in assessment["findings"]:
            if finding["type"] != "risk":
                continue
            item = finding["risk_candidate"]
            candidates.append({
                "candidate_id": finding["id"], "origin": "dfx", "title": finding["title"],
                "condition": item["condition"], "propagation": item["propagation"], "effect": item["effect"],
                "observation": item["observation"], "recovery": item["recovery"], "confidence": item["confidence"],
                "missing_evidence": item["missing_evidence"], "dfx_dimensions": [assessment["dimension"]],
                "related_ids": finding["related_ids"], "evidence": finding["evidence"],
            })
    return candidates


def _causal_candidate_batches(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Keep duplicate or explicitly linked candidates in one adjudication batch."""
    by_id = {row["candidate_id"]: row for row in candidates}
    parent = {identifier: identifier for identifier in by_id}

    def find(identifier: str) -> str:
        while parent[identifier] != identifier:
            parent[identifier] = parent[parent[identifier]]
            identifier = parent[identifier]
        return identifier

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    signatures: dict[tuple[str, str, str], str] = {}
    for row in candidates:
        identifier = row["candidate_id"]
        signature = tuple(" ".join(row[key].split()).casefold() for key in ("condition", "propagation", "effect"))
        if signature in signatures:
            union(identifier, signatures[signature])
        else:
            signatures[signature] = identifier
        for related in row.get("related_ids", []):
            if related in by_id:
                union(identifier, related)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in candidates:
        grouped.setdefault(find(row["candidate_id"]), []).append(row)
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for group in grouped.values():
        if current and len(current) + len(group) > RISK_BATCH_SIZE:
            batches.append(current)
            current = []
        if len(group) > RISK_BATCH_SIZE:
            batches.append(group)
        else:
            current.extend(group)
    if current:
        batches.append(current)
    return batches


def _assemble_risk_result(synthesis: dict[str, Any], adjudications: list[dict[str, Any]]) -> dict[str, Any]:
    mappings = {row["candidate_id"]: row["risk_id"] for row in synthesis["candidate_risk_mappings"]}
    return {
        "type": "risk_result", "run_id": synthesis["run_id"],
        "semantic_revision": synthesis["semantic_revision"],
        "risk_decisions": [
            {
                "candidate_id": row["candidate_id"], "status": row["status"], "reason": row["reason"],
                "risk_id": mappings[row["candidate_id"]] if row["status"] == "confirmed" else None,
            }
            for row in adjudications
        ],
        "risks": synthesis["risks"], "sfmea": synthesis["sfmea"],
    }


def _coverage(source_map: dict[str, Any], semantics: dict[str, Any]) -> dict[str, Any]:
    evidence = [
        item
        for family in model.SEMANTIC_FAMILIES
        for row in semantics[family]
        for item in row.get("evidence", [])
    ]
    inventory: list[dict[str, Any]] = []
    uncovered: list[str] = []
    for file in source_map["files"]:
        for item in file.get("inventory", []):
            identifier = f"{file['path']}:{item['kind']}:{item['line_start']}-{item['line_end']}"
            inventory.append({"id": identifier, "path": file["path"], **item})
            if not any(
                row["path"] == file["path"]
                and row["line_start"] <= item["line_end"]
                and row["line_end"] >= item["line_start"]
                for row in evidence
            ):
                uncovered.append(identifier)
    return {
        "source": {
            "files": len(source_map["files"]),
            "lines": sum(row["line_count"] for row in source_map["files"]),
            "frozen_scope": "complete",
            "evidenced_lines": len({
                (row["path"], line)
                for row in evidence for line in range(row["line_start"], row["line_end"] + 1)
            }),
        },
        "semantic": {
            "inventory_total": len(inventory),
            "inventory_evidenced": len(inventory) - len(uncovered),
            "uncovered": uncovered,
            "statement": "源码范围已完整冻结；语义覆盖以 inventory evidence 与 Gap 为准。",
        },
    }


def compose(
    root: Path,
    run_id: str,
    *,
    model_name: str = execution.DEFAULT_MODEL,
    timeout_seconds: int = 1800,
    runner: Callable[..., subprocess.CompletedProcess[str]] = invoke_composer,
    dfx_runner: Callable[..., subprocess.CompletedProcess[str]] = invoke_dfx,
    adjudication_runner: Callable[..., subprocess.CompletedProcess[str]] = invoke_adjudicator,
    risk_runner: Callable[..., subprocess.CompletedProcess[str]] = invoke_risk,
    design_runner: Callable[..., subprocess.CompletedProcess[str]] = invoke_designer,
) -> dict[str, Any]:
    run = engine.load_run(root, run_id)
    if run["state"] != "analyzing":
        raise ReviewError(f"Run state is {run['state']}, expected analyzing")
    directory = engine.run_dir(root, run_id)
    plan = engine.read_json(directory / "plan.json")
    if any(row["status"] != "completed" for row in plan["slices"]):
        raise ReviewError("all semantic slices must complete before module composition")
    results = [engine.read_json(directory / "results" / f"{row['slice_id']}.json") for row in plan["slices"]]
    candidate = _combined_candidate(results)
    staged_dfx = directory / "dfx-result.json"
    semantic_revision = plan["revision"]
    if staged_dfx.is_file():
        previous_revision = engine.read_json(staged_dfx).get("semantic_revision")
        if type(previous_revision) is int and previous_revision > 0:
            semantic_revision = previous_revision
    source_map = engine.read_json(directory / "source-map.json")
    compact_candidate = _composition_candidate(candidate)
    candidate_sources = _evidence_sources(
        directory, [compact_candidate[family] for family in model.SEMANTIC_FAMILIES],
    )
    module_context = {
        "type": "module_context", "run_id": run_id, "source_revision": plan["revision"],
        "task": run["task"], "candidate": compact_candidate, "sources": candidate_sources,
        "contract": model.MODULE_CONTRACT,
    }
    try:
        module_result = _checkpoint(directory / "module-result.json", module_context, model.validate_module_composition)
        if module_result is None:
            module_result = _run_call(
                root, directory, module_context, role="composer", call_id="compose",
                context_name="module-context.json", result_name="module-result.json",
                validator=model.validate_module_composition, model_name=model_name,
                timeout_seconds=timeout_seconds, runner=runner,
            )
        semantics = _apply_composition(candidate, module_result)
        semantic_ids = _semantic_ids(semantics)
        semantic_sources = _evidence_sources(
            directory, [semantics[family] for family in model.SEMANTIC_FAMILIES],
        )
        dfx_semantics = _dfx_semantics(semantics)
        dfx_context = {
            "type": "dfx_context", "run_id": run_id, "semantic_revision": semantic_revision,
            "task": run["task"], "semantics": dfx_semantics, "semantic_ids": semantic_ids,
            "sources": semantic_sources, "methods": methods.dfx_methods(root), "contract": model.DFX_CONTRACT,
        }
        dfx = _checkpoint(directory / "dfx-result.json", dfx_context, model.validate_dfx)
        if dfx is None:
            dfx = _run_call(
                root, directory, dfx_context, role="dfx-analyst", call_id="dfx",
                context_name="dfx-context.json", result_name="dfx-result.json",
                validator=model.validate_dfx, model_name=model_name,
                timeout_seconds=timeout_seconds, runner=dfx_runner,
            )
        candidates = _risk_candidates(semantics, dfx)
        candidate_sources = _evidence_sources(directory, [candidates])
        risk_validation_context = {
            "run_id": run_id, "semantic_revision": semantic_revision, "candidates": candidates,
            "semantic_ids": semantic_ids, "sources": candidate_sources,
        }
        risk_result = _checkpoint(directory / "risk-result.json", risk_validation_context, model.validate_risk_result)
        if risk_result is None:
            adjudications: list[dict[str, Any]] = []
            for number, batch in enumerate(_causal_candidate_batches(candidates), 1):
                adjudication_context = {
                    "type": "risk_adjudication_context", "run_id": run_id,
                    "semantic_revision": semantic_revision, "task": run["task"],
                    "candidate_ids": [row["candidate_id"] for row in batch], "candidates": batch,
                    "sources": _evidence_sources(directory, [batch]),
                    "methods": methods.adjudication_methods(root),
                    "contract": model.RISK_ADJUDICATION_CONTRACT,
                }
                result_path = directory / f"risk-adjudication-{number:03d}-result.json"
                adjudication = _checkpoint(result_path, adjudication_context, model.validate_risk_adjudication)
                if adjudication is None:
                    adjudication = _run_call(
                        root, directory, adjudication_context, role="risk-adjudicator",
                        call_id=f"risk-adjudication-{number:03d}",
                        context_name=f"risk-adjudication-{number:03d}-context.json",
                        result_name=result_path.name, validator=model.validate_risk_adjudication,
                        model_name=model_name, timeout_seconds=timeout_seconds, runner=adjudication_runner,
                    )
                adjudications.extend(adjudication["decisions"])
            confirmed = {row["candidate_id"] for row in adjudications if row["status"] == "confirmed"}
            confirmed_candidates = [row for row in candidates if row["candidate_id"] in confirmed]
            synthesis_context = {
                "type": "risk_synthesis_context", "run_id": run_id,
                "semantic_revision": semantic_revision, "task": run["task"],
                "confirmed_candidate_ids": sorted(confirmed), "confirmed_candidates": confirmed_candidates,
                "adjudications": adjudications, "semantic_ids": semantic_ids,
                "sources": _evidence_sources(directory, [confirmed_candidates]),
                "methods": methods.risk_methods(root), "contract": model.RISK_SYNTHESIS_CONTRACT,
            }
            synthesis = _checkpoint(
                directory / "risk-synthesis-result.json", synthesis_context, model.validate_risk_synthesis,
            )
            if synthesis is None:
                synthesis = _run_call(
                    root, directory, synthesis_context, role="risk-analyst", call_id="risk-synthesis",
                    context_name="risk-synthesis-context.json", result_name="risk-synthesis-result.json",
                    validator=model.validate_risk_synthesis, model_name=model_name,
                    timeout_seconds=timeout_seconds, runner=risk_runner,
                )
            risk_result = _assemble_risk_result(synthesis, adjudications)
            model.validate_risk_result(risk_result, risk_validation_context)
            engine.atomic_write_json(directory / "risk-result.json", risk_result)
        test_semantics = _test_semantics(semantics, risk_result["risks"])
        test_sources = _evidence_sources(
            directory, [risk_result["risks"], *test_semantics.values()],
        )
        design_context = {
            "type": "test_design_context", "run_id": run_id, "semantic_revision": semantic_revision,
            "task": run["task"], "risk_result": risk_result, "semantics": test_semantics,
            "semantic_ids": semantic_ids, "sources": test_sources,
            "methods": methods.test_methods(root), "contract": model.TEST_DESIGN_CONTRACT,
        }
        engine.atomic_write_json(directory / "test-design-context.json", design_context)
        test_design = _checkpoint(directory / "test-design-result.json", design_context, model.validate_test_design)
        if test_design is None:
            parts: list[dict[str, Any]] = []
            for number, batch_context in enumerate(
                _test_design_batches(semantics, risk_result, design_context, directory), 1,
            ):
                result_path = directory / f"test-design-{number:03d}-result.json"
                part = _checkpoint(result_path, batch_context, model.validate_test_design)
                if part is None:
                    part = _run_call(
                        root, directory, batch_context, role="test-designer",
                        call_id=f"test-design-{number:03d}",
                        context_name=f"test-design-{number:03d}-context.json",
                        result_name=result_path.name, validator=model.validate_test_design,
                        model_name=model_name, timeout_seconds=timeout_seconds, runner=design_runner,
                    )
                parts.append(part)
            test_design = {
                "type": "test_design_result", "run_id": run_id,
                "semantic_revision": semantic_revision,
                "scenarios": [row for part in parts for row in part["scenarios"]],
                "test_cases": [row for part in parts for row in part["test_cases"]],
            }
            model.validate_test_design(test_design, design_context)
            engine.atomic_write_json(directory / "test-design-result.json", test_design)
    except ReviewError as exc:
        engine.fail(root, run_id, str(exc))
        raise

    value = {
        "type": "analysis", "run_id": run_id, "analysis_revision": semantic_revision,
        "task": run["task"], "source_map": source_map, "methods": candidate["applied_skills"],
        "summary": semantics["summary"],
        **{family: semantics[family] for family in model.SEMANTIC_FAMILIES},
        "dfx": dfx["assessments"],
        "risk_decisions": risk_result["risk_decisions"], "risks": risk_result["risks"],
        "sfmea": risk_result["sfmea"], "scenarios": test_design["scenarios"],
        "test_cases": test_design["test_cases"],
        "coverage": _coverage(source_map, semantics),
        "composition_decisions": module_result["decisions"],
        "slices": [{"slice_id": row["slice_id"], "result_path": f"results/{row['slice_id']}.json"} for row in plan["slices"]],
    }
    engine.atomic_write_json(directory / "analysis.json", value)
    engine.commit_stage(
        root, run_id, expected="analyzing", target="composed",
        changes={
            "analysis": {"path": "analysis.json", "revision": semantic_revision},
            "review": {"path": None, "analysis_revision": None, "verdict": None},
            "report": {"markdown": None, "html": None, "analysis_revision": None},
        },
        event="analysis_composed",
    )
    return value


def structural_findings(value: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    uncovered = value["coverage"]["semantic"]["uncovered"]
    if uncovered:
        findings.append({
            "code": "semantic-inventory-uncovered", "severity": "error",
            "message": "存在既无语义对象 evidence 也无 Gap evidence 的函数或分支 inventory。",
            "object_ids": uncovered,
        })
    summaries = [row["summary"].strip() for row in value["dfx"]]
    if any(count >= 4 for count in Counter(summaries).values()):
        findings.append({
            "code": "template-dfx-summary", "severity": "error",
            "message": "四个以上 DFX 维度使用相同 summary。", "object_ids": [],
        })
    risk_ids = {row["id"] for row in value["risks"]}
    if len(risk_ids) > 1 and len(value["test_cases"]) > 1:
        if all(set(row["risk_ids"]) == risk_ids for row in value["test_cases"]):
            findings.append({
                "code": "cartesian-risk-case-relationship", "severity": "error",
                "message": "每个用例都绑定全部风险，疑似自动全连接。", "object_ids": sorted(risk_ids),
            })
    blocked = {row["id"] for row in value["risks"] if row["testability"] == "blocked"}
    tested = {risk_id for row in value["test_cases"] for risk_id in row["risk_ids"]}
    if blocked & tested:
        findings.append({
            "code": "blocked-risk-has-executable-case", "severity": "error",
            "message": "blocked 风险不能伪装成可执行测试。", "object_ids": sorted(blocked & tested),
        })
    proc_cases: list[str] = []
    preload_cases: list[str] = []
    spdk_readiness_cases: list[str] = []
    for case in value["test_cases"]:
        body = " ".join([*case["steps"], *case["observations"]]).lower()
        prerequisites = " ".join(case["preconditions"]).lower()
        if "/proc/" in body and not any(token in prerequisites for token in ("/proc", "linux")):
            proc_cases.append(case["id"])
        if "ld_preload" in body and "linux" not in prerequisites:
            preload_cases.append(case["id"])
        if "memzone_dump" in body and not any(
            token in prerequisites for token in ("hugepage", "dpdk", "spdk 运行环境", "启动回调", "startup")
        ):
            spdk_readiness_cases.append(case["id"])
    if proc_cases:
        findings.append({
            "code": "test-proc-platform-prerequisite", "severity": "warning",
            "message": "用例使用 /proc 观测但未声明 Linux 或 /proc 前置条件。", "object_ids": proc_cases,
        })
    if preload_cases:
        findings.append({
            "code": "test-ld-preload-platform-prerequisite", "severity": "warning",
            "message": "用例使用 LD_PRELOAD 注入但未声明 Linux 前置条件。", "object_ids": preload_cases,
        })
    if spdk_readiness_cases:
        findings.append({
            "code": "test-spdk-readiness-prerequisite", "severity": "warning",
            "message": "MEMZONE_DUMP 用例未证明 SPDK/DPDK 环境已使启动回调可达，可能误判。",
            "object_ids": spdk_readiness_cases,
        })
    return findings


def _analysis_evidence_sources(directory: Path, value: dict[str, Any]) -> list[dict[str, Any]]:
    families = [value[family] for family in model.SEMANTIC_FAMILIES]
    families.extend([
        [finding for row in value["dfx"] for finding in row["findings"]],
        value["risks"], value["sfmea"], value["scenarios"],
    ])
    return _evidence_sources(directory, families)


def _review_metrics(value: dict[str, Any]) -> dict[str, int]:
    decisions = value["risk_decisions"]
    return {
        "semantic_objects": sum(len(value[family]) for family in model.SEMANTIC_FAMILIES),
        "dfx_findings": sum(len(row["findings"]) for row in value["dfx"]),
        "risk_candidates": len(decisions),
        "confirmed_candidates": sum(row["status"] == "confirmed" for row in decisions),
        "dismissed_candidates": sum(row["status"] == "dismissed" for row in decisions),
        "canonical_risks": len(value["risks"]),
        "blocked_risks": sum(row["testability"] == "blocked" for row in value["risks"]),
        "scenarios": len(value["scenarios"]), "test_cases": len(value["test_cases"]),
    }


def review(
    root: Path,
    run_id: str,
    *,
    model_name: str = execution.DEFAULT_MODEL,
    timeout_seconds: int = 1800,
    runner: Callable[..., subprocess.CompletedProcess[str]] = invoke_reviewer,
) -> dict[str, Any]:
    run = engine.load_run(root, run_id)
    if run["state"] == "composed":
        engine.transition(root, run_id, "composed", "reviewing")
    elif run["state"] == "completed":
        engine.reopen_review(root, run_id)
    elif run["state"] != "reviewing":
        raise ReviewError(f"Run state is {run['state']}, expected composed or reviewing")
    directory = engine.run_dir(root, run_id)
    value = engine.read_json(directory / "analysis.json")
    expected_metrics = _review_metrics(value)
    context = {
        "type": "review_context", "run_id": run_id,
        "analysis_revision": value["analysis_revision"], "analysis": value,
        "expected_metrics": expected_metrics,
        "sources": _analysis_evidence_sources(directory, value), "contract": model.REVIEW_CONTRACT,
    }
    engine.atomic_write_json(directory / "review-context.json", context)
    receipt_path = execution.next_execution_path(directory, "review")
    receipt: dict[str, Any] = {
        "type": "opencode_execution", "run_id": run_id, "call_id": "review",
        "analysis_revision": value["analysis_revision"], "returncode": None, "stderr": "", "raw_output": "",
        "events": [], "session_ids": [], "finish_reasons": [],
        "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0},
        "tools": [], "status": "failed", "error": None,
    }
    try:
        process = runner(root, context, model_name=model_name, timeout_seconds=timeout_seconds)
        receipt.update({"returncode": process.returncode, "stderr": process.stderr, "raw_output": process.stdout, **execution.preview_opencode_jsonl(process.stdout)})
        if process.returncode != 0:
            raise ReviewError(f"OpenCode reviewer exited with code {process.returncode}: {process.stderr.strip()}")
        parsed = execution.parse_opencode_jsonl(process.stdout, role="reviewer")
        receipt.update({key: parsed[key] for key in ("events", "session_ids", "finish_reasons", "tokens")})
        reviewer = model.validate_review(
            execution._one_json_object(parsed["final_text"], role="reviewer"), run_id=run_id,
            analysis_revision=value["analysis_revision"], expected_metrics=expected_metrics,
        )
        receipt["status"] = "succeeded"
    except (ReviewError, execution.AnalysisError, model.ContractError) as exc:
        receipt["error"] = str(exc)
        engine.fail(root, run_id, str(exc))
        raise ReviewError(str(exc)) from exc
    finally:
        engine.atomic_write_json(receipt_path, receipt)
    deterministic = structural_findings(value)
    findings = [*deterministic, *reviewer["findings"]]
    verdict = "fail" if reviewer["verdict"] == "fail" or any(row["severity"] == "error" for row in deterministic) else "pass"
    result = {
        "type": "review", "run_id": run_id, "analysis_revision": value["analysis_revision"],
        "verdict": verdict, "metrics": reviewer["metrics"], "summary": reviewer["summary"], "findings": findings,
        "execution": {"session_ids": parsed["session_ids"], "finish_reasons": parsed["finish_reasons"], "tokens": parsed["tokens"]},
    }
    engine.atomic_write_json(directory / "review.json", result)
    engine.update_metadata(
        root, run_id, expected_state="reviewing",
        changes={"review": {"path": "review.json", "analysis_revision": value["analysis_revision"], "verdict": verdict}},
        event="analysis_reviewed", details={"verdict": verdict, "findings": len(findings)},
    )
    return result
