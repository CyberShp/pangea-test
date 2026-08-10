from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from runtime import data_runtime, semantic_analysis
from .common import output_json, root_dir


def _run(root: Path, run_id: str) -> Path:
    run, _ = data_runtime._load_run(root, run_id)
    return run


def _plan_paths(run: Path) -> tuple[Path, Path]:
    base = run / "internal" / "semantic-analysis"
    return base / "plan.json", base / "plan.original.json"


def _ensure_plan_backup(root: Path, run_id: str) -> tuple[dict[str, Any], Path]:
    run = _run(root, run_id)
    plan_path, original_path = _plan_paths(run)
    if plan_path.is_symlink() or not plan_path.is_file():
        raise semantic_analysis.SemanticAnalysisError("semantic analysis plan is not staged")
    plan = semantic_analysis.validate_plan(root, run_id, data_runtime.read_json(plan_path))
    if original_path.exists():
        original = semantic_analysis.validate_plan(root, run_id, data_runtime.read_json(original_path))
        if original != plan:
            raise semantic_analysis.SemanticAnalysisError(
                "frozen semantic plan differs from plan.original.json; run semantic restore-plan"
            )
    else:
        data_runtime.atomic_write_json(original_path, plan)
    return plan, original_path


def unit_context(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    _ensure_plan_backup(root, args.run_id)
    context = semantic_analysis.unit_context(root, args.run_id, args.unit_id)
    sources = []
    for source in context["sources"]:
        start = source["line_start"]
        lines = source.pop("text").split("\n")
        source["lines"] = [{"line": start + index, "text": text} for index, text in enumerate(lines)]
        sources.append(source)
    context["sources"] = sources
    context["evidence_contract"] = {
        "path": "source_evidence.path 必须逐字复制 sources[].path，不得使用短文件名或自行重建路径",
        "line": "source_evidence.line 必须直接使用 sources[].lines[].line 的正整数值",
    }
    context["instructions"] += (
        " source_evidence.path 必须逐字复制 sources[].path；"
        "source_evidence.line 必须直接取自 sources[].lines[].line，禁止 0、相对偏移或范围外行号。"
    )
    run = _run(root, args.run_id)
    target = run / "internal" / "semantic-analysis" / "contexts" / f"{args.unit_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    data_runtime.atomic_write_json(target, context)
    output_json({"run_id": args.run_id, "unit_id": args.unit_id, "context": str(target),
                 "sha256": data_runtime.sha256_file(target)})


def _evidence_errors(value: Any, selected: dict[str, list[tuple[int, int]]], field: str,
                     errors: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        evidence = value.get("source_evidence")
        if isinstance(evidence, list):
            for index, row in enumerate(evidence):
                location = f"{field}.source_evidence[{index}]"
                if not isinstance(row, dict):
                    errors.append({"field": location, "type": "invalid_evidence"})
                    continue
                path = row.get("path")
                line = row.get("line")
                if path not in selected:
                    errors.append({"field": location + ".path", "type": "invalid_path",
                                   "actual": path, "allowed": sorted(selected)})
                    continue
                if type(line) is not int or line < 1:
                    errors.append({"field": location + ".line", "type": "invalid_line",
                                   "actual": line, "allowed_ranges": selected[path]})
                elif not any(start <= line <= end for start, end in selected[path]):
                    errors.append({"field": location + ".line", "type": "out_of_range",
                                   "actual": line, "allowed_ranges": selected[path]})
        for key, child in value.items():
            if key != "source_evidence":
                _evidence_errors(child, selected, f"{field}.{key}" if field else key, errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _evidence_errors(child, selected, f"{field}[{index}]", errors)


def diagnose(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    run = _run(root, args.run_id)
    plan_path, original_path = _plan_paths(run)
    result: dict[str, Any] = {"run_id": args.run_id, "plan": {}, "units": []}
    try:
        plan = semantic_analysis.validate_plan(root, args.run_id, data_runtime.read_json(plan_path))
        result["plan"] = {"status": "valid", "sha256": semantic_analysis._digest(plan),
                          "original_exists": original_path.is_file()}
        if original_path.is_file():
            original = semantic_analysis.validate_plan(root, args.run_id, data_runtime.read_json(original_path))
            if original != plan:
                result["plan"]["status"] = "modified"
                result["plan"]["original_sha256"] = semantic_analysis._digest(original)
    except (semantic_analysis.SemanticAnalysisError, data_runtime.DataRuntimeError, OSError) as exc:
        result["plan"] = {"status": "invalid", "error": str(exc),
                          "original_exists": original_path.is_file()}
        output_json(result)
        return

    unit_dir = run / "internal" / "semantic-analysis" / "units"
    for planned in plan["units"]:
        unit_id = planned["unit_id"]
        path = unit_dir / f"{unit_id}.json"
        errors: list[dict[str, Any]] = []
        if not path.is_file() or path.is_symlink():
            result["units"].append({"unit_id": unit_id, "status": "missing", "errors": []})
            continue
        unit = data_runtime.read_json(path)
        selected: dict[str, list[tuple[int, int]]] = {}
        for row in planned["source_ranges"]:
            selected.setdefault(row["path"], []).append((row["line_start"], row["line_end"]))
        if not isinstance(unit, dict):
            errors.append({"field": "<root>", "type": "invalid_unit"})
        else:
            plan_sha = semantic_analysis._digest(plan)
            if unit.get("plan_sha256") != plan_sha:
                errors.append({"field": "plan_sha256", "type": "plan_sha_mismatch",
                               "actual": unit.get("plan_sha256"), "expected": plan_sha})
            _evidence_errors(unit, selected, "", errors)
            try:
                semantic_analysis.validate_unit(root, args.run_id, unit)
            except semantic_analysis.SemanticAnalysisError as exc:
                if not errors:
                    errors.append({"field": "<validation>", "type": "validation_error", "message": str(exc)})
        result["units"].append({"unit_id": unit_id, "status": "invalid" if errors else "valid",
                                "errors": errors})
    output_json(result)


def restore_plan(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    run = _run(root, args.run_id)
    plan_path, original_path = _plan_paths(run)
    if original_path.is_symlink() or not original_path.is_file():
        raise semantic_analysis.SemanticAnalysisError("plan.original.json does not exist")
    original = semantic_analysis.validate_plan(root, args.run_id, data_runtime.read_json(original_path))
    data_runtime.atomic_write_json(plan_path, original)
    output_json({"run_id": args.run_id, "plan": str(plan_path),
                 "sha256": semantic_analysis._digest(original), "restored": True})


def reset_unit(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    plan, _ = _ensure_plan_backup(root, args.run_id)
    if not any(row["unit_id"] == args.unit_id for row in plan["units"]):
        raise semantic_analysis.SemanticAnalysisError("semantic analysis unit is not in the frozen plan")
    run = _run(root, args.run_id)
    removed = []
    for path in (
        run / "internal" / "semantic-analysis" / "units" / f"{args.unit_id}.json",
        run / "internal" / "semantic-analysis" / "contexts" / f"{args.unit_id}.json",
    ):
        if path.is_file() and not path.is_symlink():
            path.unlink()
            removed.append(str(path))
    output_json({"run_id": args.run_id, "unit_id": args.unit_id, "reset": True, "removed": removed})


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA semantic analysis diagnostics and recovery")
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="command", required=True)

    context = sub.add_parser("unit-context")
    context.add_argument("--run-id", required=True)
    context.add_argument("--unit-id", required=True)
    context.set_defaults(func=unit_context)

    diag = sub.add_parser("diagnose")
    diag.add_argument("--run-id", required=True)
    diag.set_defaults(func=diagnose)

    restore = sub.add_parser("restore-plan")
    restore.add_argument("--run-id", required=True)
    restore.set_defaults(func=restore_plan)

    reset = sub.add_parser("reset-unit")
    reset.add_argument("--run-id", required=True)
    reset.add_argument("--unit-id", required=True)
    reset.set_defaults(func=reset_unit)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (semantic_analysis.SemanticAnalysisError, data_runtime.DataRuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
