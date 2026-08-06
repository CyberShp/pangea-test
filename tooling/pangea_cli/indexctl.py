from __future__ import annotations

import argparse
import sys

from runtime import index_runtime
from .common import output_json, root_dir


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="PANGEA 受控 GitNexus 影子仓索引")
    command.add_argument("--root")
    sub = command.add_subparsers(dest="command", required=True)
    one = sub.add_parser("repository")
    one.add_argument("--repository", required=True)
    one.set_defaults(all_repositories=False)
    all_repositories = sub.add_parser("all")
    all_repositories.set_defaults(all_repositories=True)
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = index_runtime.index_all(root_dir(args.root)) if args.all_repositories else index_runtime.index_repository(root_dir(args.root), args.repository)
        output_json(result)
        return 0
    except (index_runtime.IndexRuntimeError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
