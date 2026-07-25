from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import PangeaError, atomic_write_json, load_project, output_json, relative_to_root, resolve_root_path, root_dir, utc_now


def load_workflows(root: Path) -> dict[str, Any]:
    path = root / "registry" / "workflows.json"
    if not path.exists(): raise PangeaError(f"工作流注册表不存在: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def next_run_id(parent: Path) -> str:
    date = datetime.now().strftime("%Y%m%d")
    numbers = []
    for path in parent.glob(f"{date}-*"):
        if path.is_dir():
            try: numbers.append(int(path.name.rsplit("-", 1)[1]))
            except (IndexError, ValueError): pass
    return f"{date}-{(max(numbers) + 1) if numbers else 1:03d}"


def _source_snapshot(root: Path, project: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = []
    for source in project.get("source_roots", []):
        path = resolve_root_path(root, source["path"]); commit = None; dirty = None
        if (path / ".git").exists():
            result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], text=True, capture_output=True, check=False)
            if result.returncode == 0: commit = result.stdout.strip()
            result = subprocess.run(["git", "-C", str(path), "status", "--porcelain"], text=True, capture_output=True, check=False)
            if result.returncode == 0: dirty = bool(result.stdout.strip())
        snapshots.append({"source_id": source["id"], "path": source["path"], "access": source["access"], "commit": commit, "dirty": dirty})
    return snapshots


def _select_inputs(root: Path, project: dict[str, Any], roles: list[str]) -> list[dict[str, Any]]:
    path = resolve_root_path(root, project["input_root"]) / "catalog.json"
    if not path.exists(): return []
    items = json.loads(path.read_text(encoding="utf-8")).get("artifacts", [])
    return [item for item in items if not roles or item.get("role") in roles]


def _select_assets(root: Path, project: dict[str, Any], types: list[str]) -> list[dict[str, Any]]:
    path = root / "assets" / "catalog.json"
    if not path.exists(): return []
    profiles = set(project.get("asset_profiles", [])); selected = []
    for item in json.loads(path.read_text(encoding="utf-8")).get("assets", []):
        if item.get("status") != "approved": continue
        if types and item.get("asset_type") not in types: continue
        item_profiles = set(item.get("profiles", []))
        if item_profiles and profiles.isdisjoint(item_profiles): continue
        selected.append(item)
    return selected


def start_workflow(args: argparse.Namespace) -> None:
    root = root_dir(args.root); project = load_project(root, args.project_id)
    workflow = load_workflows(root).get("workflows", {}).get(args.workflow_id)
    if not workflow: raise PangeaError(f"工作流未登记: {args.workflow_id}")
    if not workflow.get("managed", False): raise PangeaError(f"工作流尚未机器化: {args.workflow_id}")

    workspace_parent = resolve_root_path(root, project["workspace_root"]) / args.workflow_id
    output_parent = resolve_root_path(root, project["output_root"]) / args.workflow_id
    workspace_parent.mkdir(parents=True, exist_ok=True); output_parent.mkdir(parents=True, exist_ok=True)
    run_id = args.run_id or next_run_id(workspace_parent); run_dir = workspace_parent / run_id; output_dir = output_parent / run_id
    if run_dir.exists(): raise PangeaError(f"运行目录已存在: {run_dir}")

    source_path = resolve_root_path(root, project["source_roots"][0]["path"])
    command = [sys.executable, str(root / "runtime" / "runctl.py"), "init", "--scenario", workflow["scenario_id"],
               "--target", args.target, "--source-path", str(source_path), "--runs-root", str(workspace_parent),
               "--task-id", run_id, "--max-parallel", str(args.max_parallel), "--max-audit-rounds", str(args.max_audit_rounds)]
    result = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
    if result.returncode != 0: raise PangeaError(f"runctl 初始化失败: {result.stderr.strip() or result.stdout.strip()}")
    output_dir.mkdir(parents=True, exist_ok=True)

    input_roles = args.input_role or workflow.get("default_input_roles", [])
    asset_types = args.asset_type or workflow.get("default_asset_types", [])
    lock = {"schema_version": "1.0", "project_id": project["project_id"], "workflow_id": args.workflow_id, "run_id": run_id,
            "created_at": utc_now(), "sources": _source_snapshot(root, project),
            "inputs": _select_inputs(root, project, input_roles), "assets": _select_assets(root, project, asset_types)}
    atomic_write_json(run_dir / "inputs.lock.json", lock)
    artifacts = {"schema_version": "1.0", "project_id": project["project_id"], "workflow_id": args.workflow_id, "run_id": run_id,
                 "artifacts": [{"artifact_id": item, "artifact_type": item, "stage": "final", "producer": workflow["owner_agent"],
                                "path": f"final/{item}", "status": "pending", "visibility": "deliverable"}
                               for item in workflow.get("deliverables", [])]}
    atomic_write_json(run_dir / "artifacts.json", artifacts)
    context = {"project_id": project["project_id"], "workflow_id": args.workflow_id, "run_id": run_id, "target": args.target,
               "run_dir": str(run_dir), "output_dir": str(output_dir), "source_path": str(source_path)}
    atomic_write_json(run_dir / "run-context.json", context)
    atomic_write_json(output_parent / "latest.json", {"run_id": run_id, "path": relative_to_root(output_dir, root), "updated_at": utc_now()})
    output_json({**context, "runctl": json.loads(result.stdout), "locked_inputs": len(lock["inputs"]), "locked_assets": len(lock["assets"])})


def publish_outputs(args: argparse.Namespace) -> None:
    root = root_dir(args.root); run_dir = resolve_root_path(root, args.run_dir)
    context = json.loads((run_dir / "run-context.json").read_text(encoding="utf-8")); output_dir = Path(context["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True); published = []
    final_dir = run_dir / "final"
    if final_dir.exists():
        for source in final_dir.rglob("*"):
            if not source.is_file(): continue
            target = output_dir / source.relative_to(final_dir); target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(source, target); published.append(str(target))
    output_json({"output_dir": str(output_dir), "published": published})


def workflow_status(args: argparse.Namespace) -> None:
    root = root_dir(args.root); run_dir = resolve_root_path(root, args.run_dir)
    context = json.loads((run_dir / "run-context.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    artifacts = json.loads((run_dir / "artifacts.json").read_text(encoding="utf-8"))
    output_json({"context": context, "summary_status": manifest["summary_status"], "audit": manifest["audit"], "artifacts": artifacts["artifacts"]})


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PANGEA workflow workspace manager"); p.add_argument("--root")
    sub = p.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start"); start.add_argument("--project-id"); start.add_argument("--workflow-id", required=True); start.add_argument("--target", required=True)
    start.add_argument("--run-id"); start.add_argument("--input-role", action="append"); start.add_argument("--asset-type", action="append")
    start.add_argument("--max-parallel", type=int, default=4); start.add_argument("--max-audit-rounds", type=int, default=2); start.set_defaults(func=start_workflow)
    publish = sub.add_parser("publish"); publish.add_argument("--run-dir", required=True); publish.set_defaults(func=publish_outputs)
    status = sub.add_parser("status"); status.add_argument("--run-dir", required=True); status.set_defaults(func=workflow_status)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try: args.func(args); return 0
    except (PangeaError, json.JSONDecodeError) as exc: print(f"ERROR: {exc}", file=sys.stderr); return 2
