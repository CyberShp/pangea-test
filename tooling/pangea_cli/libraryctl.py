from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from runtime import library_runtime
from .common import output_json, root_dir


def _payload(args: argparse.Namespace) -> dict[str, Any]:
    raw = args.json if args.json else Path(args.file).read_text(encoding="utf-8")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise library_runtime.LibraryRuntimeError(f"分类不是有效 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise library_runtime.LibraryRuntimeError("分类必须是 JSON 对象")
    return value


def hints(args: argparse.Namespace) -> None:
    output_json(library_runtime.refresh_role_hints(root_dir(args.root)))


def classify(args: argparse.Namespace) -> None:
    output_json(library_runtime.write_semantic_classification(root_dir(args.root), args.source_path, _payload(args)))


def search(args: argparse.Namespace) -> None:
    output_json(library_runtime.search_library(root_dir(args.root), args.query, role=args.role, tags=args.tag,
                                                module=args.module, version=args.version, limit=args.limit))


def legacy(args: argparse.Namespace) -> None:
    output_json(library_runtime.legacy_migration_gaps(root_dir(args.root)))


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA library classification and retrieval")
    parser.add_argument("--root")
    commands = parser.add_subparsers(dest="command", required=True)
    hint = commands.add_parser("refresh-hints"); hint.set_defaults(func=hints)
    write = commands.add_parser("classify"); write.add_argument("--source-path", required=True)
    group = write.add_mutually_exclusive_group(required=True); group.add_argument("--json"); group.add_argument("--file")
    write.set_defaults(func=classify)
    find = commands.add_parser("search"); find.add_argument("--query", required=True); find.add_argument("--role", choices=library_runtime.ROLES)
    find.add_argument("--tag", action="append", default=[]); find.add_argument("--module"); find.add_argument("--version"); find.add_argument("--limit", type=int, default=8); find.set_defaults(func=search)
    migration = commands.add_parser("legacy-gaps"); migration.set_defaults(func=legacy)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (library_runtime.LibraryRuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
