"""Retired v1 workflow CLI sentinel."""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    del argv
    print(
        "ERROR: workflow CLI 已退役；请通过正式 Agent 命令或 runtime/runctl.py create-v2 创建 Run",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
