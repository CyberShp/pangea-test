"""Retired v1 asset CLI sentinel."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    del argv
    print("ERROR: asset v1 CLI 已退役；请使用 pangea-data/library", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
