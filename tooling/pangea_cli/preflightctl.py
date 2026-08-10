from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime import data_runtime, workspace_runtime

REUSE_MAX_AGE_HOURS = 24


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PANGEA portable no-guess preflight")
    parser.add_argument("--root")
    parser.add_argument("--start")
    parser.add_argument("--force", action="store_true", help="忽略已有 receipt，重新执行完整 preflight")
    return parser


def _reusable_result(args: argparse.Namespace) -> dict[str, object] | None:
    if args.force:
        return None
    try:
        located = workspace_runtime.locate_project_root(
            explicit=args.root,
            start=Path(args.start) if args.start else None,
        )
    except workspace_runtime.WorkspaceResolutionError:
        return None

    project_root = Path(located["project_root"])
    path = project_root / workspace_runtime.PREFLIGHT_RECEIPT_RELATIVE
    if path.is_symlink() or not path.is_file():
        return None
    receipt = data_runtime.read_json(path)
    if not isinstance(receipt, dict) or receipt.get("status") != "ready":
        return None
    if receipt.get("artifact_type") != "preflight_receipt" or receipt.get("schema_version") != "1.0":
        return None
    if receipt.get("project_root") != str(project_root):
        return None
    executable = str(Path(sys.executable).resolve())
    if receipt.get("python_executable") != executable:
        return None
    try:
        created = datetime.fromisoformat(str(receipt["created_at"]))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds() / 3600
    except (KeyError, TypeError, ValueError):
        return None
    if age_hours > REUSE_MAX_AGE_HOURS:
        return None

    incomplete_runs = data_runtime.incomplete_runs(project_root)
    if not incomplete_runs:
        return None

    result = {key: value for key, value in receipt.items()
              if key not in {"artifact_type", "schema_version", "created_at"}}
    repository_root = Path(str(result.get("repository_root") or project_root / "pangea-data" / "repositories"))
    known_repositories = sorted(
        item.name for item in repository_root.iterdir()
        if item.is_dir() and not item.is_symlink()
    ) if repository_root.is_dir() else []
    result["known_repositories"] = known_repositories

    step_results = dict(result.get("step_results") or {})
    session_prepare = step_results.get("session_prepare")
    if isinstance(session_prepare, dict):
        session_prepare = dict(session_prepare)
        session_prepare["known_repositories"] = known_repositories
        session_prepare["incomplete_runs"] = incomplete_runs
        step_results["session_prepare"] = session_prepare
    result["step_results"] = step_results
    result["reused_preflight"] = True
    result["receipt"] = {
        "path": "session/preflight-receipt.json",
        "absolute_path": str(path),
        "sha256": data_runtime.sha256_file(path),
        "created_at": receipt["created_at"],
    }
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    reused = _reusable_result(args)
    if reused is not None:
        print(json.dumps(reused, ensure_ascii=False, indent=2))
        return 0

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