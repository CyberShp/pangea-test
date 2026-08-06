from __future__ import annotations

import argparse
import importlib


def main() -> int:
    parser = argparse.ArgumentParser(description="PANGEA deterministic command suite")
    parser.add_argument(
        "area",
        choices=[
            "data", "report", "tool", "library", "repo", "index",
        ],
    )
    args, remaining = parser.parse_known_args()
    modules = {
        "data": "datactl", "report": "reportctl",
        "tool": "toolctl", "library": "libraryctl", "repo": "repoctl",
        "index": "indexctl",
    }
    # Other work areas may have optional dependencies; a data session must not
    # fail simply because an unrelated renderer is unavailable.
    module = importlib.import_module(f".{modules[args.area]}", __package__)
    return module.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
