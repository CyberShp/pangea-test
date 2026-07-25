from __future__ import annotations

import argparse

from . import assetctl, inputctl, projectctl, workflowctl


def main() -> int:
    parser = argparse.ArgumentParser(description="PANGEA deterministic command suite")
    parser.add_argument("area", choices=["project", "input", "asset", "workflow"])
    args, remaining = parser.parse_known_args()
    if args.area == "project":
        return projectctl.main(remaining)
    if args.area == "input":
        return inputctl.main(remaining)
    if args.area == "asset":
        return assetctl.main(remaining)
    return workflowctl.main(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
