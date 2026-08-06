from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime import workspace_runtime


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA portable no-guess preflight")
    parser.add_argument("--root")
    parser.add_argument("--start")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    result = workspace_runtime.run_preflight(
        explicit_root=args.root,
        start=Path(args.start) if args.start else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ready", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
