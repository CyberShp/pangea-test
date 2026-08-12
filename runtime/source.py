"""Prepare C/C++ source and create output-budget-aware semantic slices."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Iterable

from tree_sitter import Language, Node, Parser
import tree_sitter_c
import tree_sitter_cpp

from runtime import engine, methods, model


SOURCE_SUFFIXES = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc", ".in"}
BUILD_NAMES = {"Makefile", "CMakeLists.txt", "meson.build"}
DEFAULT_MAX_SOURCE_BYTES = 24_000
DEFAULT_MAX_GENERATION_TOKENS = 12_000
GENERATION_MULTIPLIER_NUMERATOR = 5
GENERATION_MULTIPLIER_DENOMINATOR = 2
COMPLEX_NODES = {
    "if_statement", "switch_statement", "case_statement", "for_statement", "while_statement",
    "do_statement", "conditional_expression", "goto_statement",
}


class SourceError(RuntimeError):
    pass


def _within(parent: Path, child: Path) -> Path:
    try:
        return child.resolve().relative_to(parent.resolve())
    except ValueError as exc:
        raise SourceError(f"path is outside repository: {child}") from exc


def _files(scope: Path) -> list[Path]:
    candidates: Iterable[Path] = [scope] if scope.is_file() else scope.rglob("*")
    return sorted(
        path for path in candidates
        if path.is_file() and not path.is_symlink()
        and (path.suffix.lower() in SOURCE_SUFFIXES or path.name in BUILD_NAMES or path.suffix == ".mk")
    )


def _parser(path: Path) -> tuple[Parser, str] | None:
    suffix = path.suffix.lower()
    if suffix == ".c":
        return Parser(Language(tree_sitter_c.language())), "c"
    if suffix in {".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".inc", ".in"}:
        return Parser(Language(tree_sitter_cpp.language())), "cpp"
    return None


def _walk(node: Node) -> Iterable[Node]:
    yield node
    for child in node.named_children:
        yield from _walk(child)


def _name(node: Node, source: bytes) -> str:
    declarator = node.child_by_field_name("declarator")
    if declarator is None:
        return "<anonymous>"
    while declarator.child_by_field_name("declarator") is not None:
        declarator = declarator.child_by_field_name("declarator")
    identifiers = [item for item in _walk(declarator) if item.type in {"identifier", "field_identifier", "operator_name"}]
    return source[identifiers[0].start_byte:identifiers[0].end_byte].decode("utf-8", errors="replace") if identifiers else "<anonymous>"


def _parse_file(path: Path, relative: str) -> dict[str, Any]:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    lines = text.splitlines()
    configured = _parser(path)
    symbols: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    if configured is not None:
        parser, language = configured
        tree = parser.parse(raw)
        if tree.root_node.has_error:
            raise SourceError(f"Tree-sitter could not parse {relative}")
        for node in _walk(tree.root_node):
            if node.type == "function_definition":
                complexity = sum(1 for child in _walk(node) if child.type in COMPLEX_NODES)
                symbol = {
                    "kind": "function",
                    "name": _name(node, raw),
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                    "complexity": complexity,
                }
                symbols.append(symbol)
                inventory.append({
                    "kind": "function", "name": symbol["name"],
                    "line_start": symbol["line_start"], "line_end": symbol["line_end"],
                })
            elif node.type in COMPLEX_NODES:
                inventory.append({
                    "kind": node.type,
                    "name": node.type,
                    "line_start": node.start_point.row + 1,
                    "line_end": node.end_point.row + 1,
                })
    else:
        language = "build"
        if lines:
            inventory.append({"kind": "build", "name": path.name, "line_start": 1, "line_end": len(lines)})
    return {
        "path": relative,
        "language": language,
        "line_count": len(lines),
        "byte_count": len(raw),
        "symbols": symbols,
        "inventory": inventory,
    }


def prepare(root: Path, run_id: str) -> dict[str, Any]:
    run = engine.load_run(root, run_id)
    if run["state"] != "confirmed":
        raise SourceError(f"Run state is {run['state']}, expected confirmed")
    repository_root = (root.resolve() / "pangea-data" / "repositories" / run["task"]["repository"]).resolve()
    scope = (repository_root / run["task"]["scope"]).resolve()
    _within(repository_root, scope)
    if not scope.exists():
        raise SourceError(f"source scope does not exist: {run['task']['scope']}")
    files = _files(scope)
    if not files:
        raise SourceError("source scope contains no supported C/C++ or build files")
    directory = engine.run_dir(root, run_id)
    source_root = directory / "source"
    rows: list[dict[str, Any]] = []
    for source_path in files:
        relative = _within(repository_root, source_path).as_posix()
        destination = source_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
        rows.append(_parse_file(destination, relative))
    source_map = {
        "type": "source_map",
        "run_id": run_id,
        "revision": run["revision"] + 1,
        "repository": run["task"]["repository"],
        "scope": run["task"]["scope"],
        "files": rows,
    }
    engine.atomic_write_json(directory / "source-map.json", source_map)
    engine.commit_stage(
        root,
        run_id,
        expected="confirmed",
        target="prepared",
        changes={"source": {"files": [row["path"] for row in rows], "source_map": "source-map.json", "methods": []}},
        event="source_prepared",
    )
    return source_map


def _estimate_output(file: dict[str, Any], line_start: int, line_end: int) -> int:
    symbols = [
        symbol for symbol in file["symbols"]
        if symbol["line_start"] <= line_end and symbol["line_end"] >= line_start
    ]
    complexity = sum(symbol["complexity"] for symbol in symbols)
    estimate = 2_000 + len(symbols) * 400 + complexity * 120 + (line_end - line_start + 1) * 25
    if file["language"] == "build":
        estimate += 800
    return estimate


def _estimate_generation(output_tokens: int) -> int:
    """Budget provider-side reasoning plus visible output, calibrated by real OpenCode runs."""
    return (
        output_tokens * GENERATION_MULTIPLIER_NUMERATOR
        + GENERATION_MULTIPLIER_DENOMINATOR - 1
    ) // GENERATION_MULTIPLIER_DENOMINATOR


def _line_bytes(path: Path) -> list[int]:
    return [len((line + "\n").encode("utf-8")) for line in path.read_text(encoding="utf-8").splitlines()]


def _ranges(
    file: dict[str, Any], path: Path, max_source_bytes: int, max_generation_tokens: int,
) -> list[tuple[int, int, int, int]]:
    line_count = file["line_count"]
    if line_count == 0:
        return []
    whole_output = _estimate_output(file, 1, line_count)
    if file["byte_count"] <= max_source_bytes and _estimate_generation(whole_output) <= max_generation_tokens:
        return [(1, line_count, whole_output, _estimate_generation(whole_output))]
    weights = _line_bytes(path)
    regions: list[tuple[int, int]] = []
    cursor = 1
    for symbol in file["symbols"]:
        if cursor < symbol["line_start"]:
            regions.append((cursor, symbol["line_start"] - 1))
        regions.append((symbol["line_start"], symbol["line_end"]))
        cursor = symbol["line_end"] + 1
    if cursor <= line_count:
        regions.append((cursor, line_count))
    if not regions:
        regions = [(1, line_count)]
    slices: list[tuple[int, int, int, int]] = []
    start = end = regions[0][0]
    byte_count = 0
    for region_start, region_end in regions:
        region_bytes = sum(weights[region_start - 1:region_end])
        estimated_output = _estimate_output(file, start, region_end)
        estimated_generation = _estimate_generation(estimated_output)
        if byte_count and (
            byte_count + region_bytes > max_source_bytes
            or estimated_generation > max_generation_tokens
        ):
            output = _estimate_output(file, start, end)
            slices.append((start, end, output, _estimate_generation(output)))
            start = region_start
            byte_count = 0
        end = region_end
        byte_count += region_bytes
    output = _estimate_output(file, start, end)
    slices.append((start, end, output, _estimate_generation(output)))
    return slices


def plan(
    root: Path,
    run_id: str,
    *,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_generation_tokens: int = DEFAULT_MAX_GENERATION_TOKENS,
) -> dict[str, Any]:
    run = engine.load_run(root, run_id)
    if run["state"] != "prepared":
        raise SourceError(f"Run state is {run['state']}, expected prepared")
    directory = engine.run_dir(root, run_id)
    source_map = engine.read_json(directory / "source-map.json")
    revision = run["revision"] + 1
    slices: list[dict[str, Any]] = []
    for file in source_map["files"]:
        path = directory / "source" / file["path"]
        for line_start, line_end, estimated_output, estimated_generation in _ranges(
            file, path, max_source_bytes, max_generation_tokens,
        ):
            slice_id = f"S{len(slices) + 1:03d}"
            symbols = [
                item["name"] for item in file["symbols"]
                if item["line_start"] <= line_end and item["line_end"] >= line_start
            ]
            obligation_ids = [
                f"{file['path']}:{item['kind']}:{item['line_start']}-{item['line_end']}"
                for item in file["inventory"]
                if item["line_start"] <= line_end and item["line_end"] >= line_start
            ]
            lines = path.read_text(encoding="utf-8").splitlines()
            context_sources = [{
                "path": file["path"],
                "line_start": line_start,
                "line_end": line_end,
                "lines": [
                    {"line": number, "text": lines[number - 1]}
                    for number in range(line_start, line_end + 1)
                ],
            }]
            selected_methods = methods.select_slice_methods(root, run["task"], context_sources)
            row = {
                "slice_id": slice_id,
                "title": ", ".join(symbols) if symbols else file["path"],
                "sources": [{"path": file["path"], "line_start": line_start, "line_end": line_end}],
                "obligation_ids": obligation_ids,
                "method_names": [item["name"] for item in selected_methods],
                "estimated_output_tokens": estimated_output,
                "estimated_generation_tokens": estimated_generation,
                "status": "pending",
                "error": None,
            }
            slices.append(row)
            context = {
                "type": "analysis_context",
                "run_id": run_id,
                "revision": revision,
                "target": run["task"]["target"],
                "task": run["task"],
                "slice": {key: row[key] for key in ("slice_id", "title", "sources", "obligation_ids")},
                "sources": context_sources,
                "methods": selected_methods,
                "contract": model.slice_result_contract(slice_id, row["method_names"]),
            }
            engine.atomic_write_json(directory / "contexts" / f"{slice_id}.json", context)
    if not slices:
        raise SourceError("source map produced no analyzable slices")
    plan_value = {
        "type": "analysis_plan",
        "run_id": run_id,
        "revision": revision,
        "limits": {"source_bytes": max_source_bytes, "estimated_generation_tokens": max_generation_tokens},
        "slices": slices,
    }
    engine.atomic_write_json(directory / "plan.json", plan_value)
    method_names = sorted({name for row in slices for name in row["method_names"]})
    engine.commit_stage(
        root,
        run_id,
        expected="prepared",
        target="planned",
        changes={
            "source": {**run["source"], "methods": method_names},
            "plan": {"path": "plan.json", "total": len(slices), "completed": 0, "failed": 0},
        },
        event="analysis_planned",
    )
    return plan_value
