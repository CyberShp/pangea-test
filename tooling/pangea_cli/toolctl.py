from __future__ import annotations

import argparse
import sys
from typing import Optional

from runtime import capabilities
from .common import output_json


def probe(_: argparse.Namespace) -> None:
    output_json(capabilities.probe_capabilities())


def plan(args: argparse.Namespace) -> None:
    output_json(capabilities.setup_plan(args.tools))


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA read-only tool capability discovery")
    sub = parser.add_subparsers(dest="command", required=True)
    detect = sub.add_parser("probe", help="read-only local capability probe")
    detect.set_defaults(func=probe)
    setup = sub.add_parser("setup-plan", help="describe explicitly requested setup sources")
    setup.add_argument("tools", nargs="*", help="only these tools receive setup hints")
    setup.set_defaults(func=plan)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
