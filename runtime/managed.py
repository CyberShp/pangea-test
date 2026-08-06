#!/usr/bin/env python3
"""Retired v1 managed-workflow CLI sentinel."""
from __future__ import annotations

import sys


def main() -> int:
    print(
        "ERROR: managed v1 CLI 已退役；请使用 runtime/runctl.py 的 Architecture v2 命令",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
