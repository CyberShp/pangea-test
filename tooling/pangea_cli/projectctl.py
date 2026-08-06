"""Retired v1 project CLI sentinel."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    del argv
    print("ERROR: project v1 CLI 已退役；请使用 pangea-data", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
