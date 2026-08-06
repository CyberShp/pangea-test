from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from runtime import data_runtime, workspace_runtime


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
    if result["status"] in {"ready", "degraded"} and result.get("project_root"):
        project_root = Path(result["project_root"])
        workspace = data_runtime.ensure_layout(project_root)
        session_dir = data_runtime._ensure_managed_directory(
            workspace / "session", workspace.resolve(strict=True), "session 目录"
        )
        receipt = {**result, "artifact_type": "preflight_receipt", "schema_version": "1.0",
                   "created_at": data_runtime.utc_now()}
        path = session_dir / "preflight-receipt.json"
        data_runtime.atomic_write_json(path, receipt)
        result["receipt"] = {"path": "session/preflight-receipt.json",
                             "absolute_path": str(path), "sha256": data_runtime.sha256_file(path),
                             "created_at": receipt["created_at"]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"ready", "degraded"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
