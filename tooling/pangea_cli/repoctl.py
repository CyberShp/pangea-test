from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime import repository_runtime
from .common import output_json, root_dir


def snapshot(args: argparse.Namespace) -> None:
    output_json(repository_runtime.create_snapshot(root_dir(args.root), args.run_id, args.repository, args.ref, args.snapshot_id))


def snapshots(args: argparse.Namespace) -> None:
    specs = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if not isinstance(specs, list) or not all(isinstance(spec, dict) for spec in specs):
        raise repository_runtime.RepositoryRuntimeError("关联仓文件必须是 JSON 对象数组")
    output_json(repository_runtime.create_snapshots(root_dir(args.root), args.run_id, specs))


def cleanup(args: argparse.Namespace) -> None:
    output_json(repository_runtime.cleanup_snapshot(root_dir(args.root), args.run_id, args.snapshot_id))


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA 只读代码仓快照工具")
    parser.add_argument("--root")
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("snapshot"); one.add_argument("--run-id", required=True); one.add_argument("--repository", required=True); one.add_argument("--ref", default="HEAD"); one.add_argument("--snapshot-id"); one.set_defaults(func=snapshot)
    many = sub.add_parser("snapshots"); many.add_argument("--run-id", required=True); many.add_argument("--file", required=True); many.set_defaults(func=snapshots)
    remove = sub.add_parser("cleanup"); remove.add_argument("--run-id", required=True); remove.add_argument("--snapshot-id", required=True); remove.set_defaults(func=cleanup)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (repository_runtime.RepositoryRuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
