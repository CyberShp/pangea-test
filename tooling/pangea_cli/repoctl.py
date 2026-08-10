from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from runtime import data_runtime, repository_runtime
from runtime.process_runtime import run_text
from .common import output_json, root_dir

_SOURCE = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"}


def _repository(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or name in {".", ".."}:
        raise repository_runtime.RepositoryRuntimeError("repository 非法")
    repo = data_runtime.ensure_layout(root) / "repositories" / name
    if repo.is_symlink() or not repo.is_dir():
        raise repository_runtime.RepositoryRuntimeError(f"代码仓不存在: {name}")
    return repo


def _tracked(repo: Path) -> list[str]:
    result = run_text(["git", "-C", str(repo), "ls-files", "-z"], timeout=30)
    if result.returncode:
        raise repository_runtime.RepositoryRuntimeError((result.stderr or result.stdout or "git ls-files 失败").strip())
    return sorted(path for path in result.stdout.split("\0") if path)


def snapshot(args: argparse.Namespace) -> None:
    output_json(repository_runtime.create_snapshot(root_dir(args.root), args.run_id, args.repository, args.ref, args.snapshot_id))


def snapshots(args: argparse.Namespace) -> None:
    specs = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(specs, list) or not all(isinstance(spec, dict) for spec in specs):
        raise repository_runtime.RepositoryRuntimeError("关联仓文件必须是 JSON 对象数组")
    output_json(repository_runtime.create_snapshots(root_dir(args.root), args.run_id, specs))


def cleanup(args: argparse.Namespace) -> None:
    output_json(repository_runtime.cleanup_snapshot(root_dir(args.root), args.run_id, args.snapshot_id))


def locate(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    repo = _repository(root, args.repository)
    query = args.query.strip().casefold()
    if not query:
        raise repository_runtime.RepositoryRuntimeError("query 不能为空")
    tracked = _tracked(repo)
    candidates: dict[str, tuple[int, str]] = {}
    for rel in tracked:
        path = Path(rel)
        low = rel.casefold()
        name = path.name.casefold()
        stem = path.stem.casefold()
        parts = [part.casefold() for part in path.parts]
        score = None
        if query in parts:
            score = 0
        elif stem == query or name == query:
            score = 1
        elif query in name:
            score = 2
        elif query in low:
            score = 3
        if score is None:
            continue
        if path.suffix.lower() in _SOURCE:
            candidates[rel] = min(candidates.get(rel, (99, "file")), (score + 1, "file"))
        for parent in path.parents:
            if str(parent) == ".":
                break
            parent_rel = parent.as_posix()
            parent_parts = [part.casefold() for part in parent.parts]
            parent_score = 0 if query in parent_parts else 3 if query in parent_rel.casefold() else None
            if parent_score is not None:
                candidates[parent_rel] = min(candidates.get(parent_rel, (99, "directory")), (parent_score, "directory"))
    matches = [
        {"path": path, "kind": kind, "score": score}
        for path, (score, kind) in sorted(candidates.items(), key=lambda item: (item[1][0], item[1][1] != "directory", len(item[0]), item[0]))[:20]
    ]
    suggested = []
    for row in matches:
        if row["path"] not in suggested:
            suggested.append(row["path"])
        if len(suggested) >= 5:
            break
    output_json({
        "repository": args.repository,
        "query": args.query,
        "matches": matches,
        "suggested_scopes": suggested,
        "source_scope_args": [f"{args.repository}={path}" for path in suggested],
    })


def _scope_files(tracked: list[str], scope: str) -> list[str]:
    normalized = Path(scope).as_posix().rstrip("/")
    if not normalized or normalized.startswith("../") or Path(normalized).is_absolute():
        raise repository_runtime.RepositoryRuntimeError("scope 必须是仓库内规范相对路径")
    selected = [path for path in tracked if path == normalized or path.startswith(normalized + "/")]
    selected = [path for path in selected if Path(path).suffix.lower() in _SOURCE]
    if not selected:
        raise repository_runtime.RepositoryRuntimeError("scope 下未找到已跟踪 C/C++ 源文件")
    return selected


def related(args: argparse.Namespace) -> None:
    root = root_dir(args.root)
    repo = _repository(root, args.repository)
    tracked = _tracked(repo)
    scope_files = _scope_files(tracked, args.scope)
    source_files = [path for path in tracked if Path(path).suffix.lower() in _SOURCE]
    outside = [path for path in source_files if path not in scope_files]
    outside_text = {
        path: (repo / Path(path)).read_text(encoding="utf-8", errors="replace")
        for path in outside
    }
    relations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, symbol: str, evidence: str, related_paths: list[str]) -> None:
        key = (kind, symbol)
        paths = sorted(dict.fromkeys(related_paths))[:12]
        if not paths or key in seen:
            return
        seen.add(key)
        relations.append({"type": kind, "symbol": symbol, "evidence": evidence, "related_paths": paths})

    include_re = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]')
    identifier_re = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b")
    shared_suffix_re = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*(?:_ops|_queue|_ctx|_state|_list|_pool))\b")
    extern_re = re.compile(r"\bextern\b[^;()]*\b([A-Za-z_][A-Za-z0-9_]*)\b\s*(?:\[[^]]*\])?\s*;")

    for rel in scope_files:
        text = (repo / Path(rel)).read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            include = include_re.match(line)
            if include:
                token = include.group(1)
                base = Path(token).name
                hits = [path for path in outside if path.endswith(token) or Path(path).name == base]
                add("include", token, f"{rel}:{number}", hits)

            low = line.casefold()
            if any(word in low for word in ("register", "callback", "handler")):
                for symbol in identifier_re.findall(line):
                    if symbol.casefold() in {"register", "callback", "handler"}:
                        continue
                    pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
                    hits = [path for path, body in outside_text.items() if pattern.search(body)]
                    add("registration", symbol, f"{rel}:{number}", hits)

            shared = set(shared_suffix_re.findall(line))
            extern = extern_re.search(line)
            if extern:
                shared.add(extern.group(1))
            for symbol in sorted(shared):
                pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
                hits = [path for path, body in outside_text.items() if pattern.search(body)]
                add("shared_symbol", symbol, f"{rel}:{number}", hits)

    output_json({
        "repository": args.repository,
        "scope": args.scope,
        "scope_files": scope_files,
        "relations": relations,
        "related_paths": sorted({path for row in relations for path in row["related_paths"]}),
    })


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA 只读代码仓工具")
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("snapshot"); one.add_argument("--run-id", required=True); one.add_argument("--repository", required=True); one.add_argument("--ref", default="HEAD"); one.add_argument("--snapshot-id"); one.set_defaults(func=snapshot)
    many = sub.add_parser("snapshots"); many.add_argument("--run-id", required=True); many.add_argument("--file", required=True); many.set_defaults(func=snapshots)
    remove = sub.add_parser("cleanup"); remove.add_argument("--run-id", required=True); remove.add_argument("--snapshot-id", required=True); remove.set_defaults(func=cleanup)
    find = sub.add_parser("locate"); find.add_argument("--repository", required=True); find.add_argument("--query", required=True); find.set_defaults(func=locate)
    deps = sub.add_parser("related"); deps.add_argument("--repository", required=True); deps.add_argument("--scope", required=True); deps.set_defaults(func=related)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (repository_runtime.RepositoryRuntimeError, data_runtime.DataRuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())