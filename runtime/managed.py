#!/usr/bin/env python3
"""Managed-workflow extensions that wrap the stable runctl contract."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import runctl

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"
FIXTURE = ROOT / "tests" / "fixtures" / "mini-storage-module"


def emit_process(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode != 0:
        raise runctl.RunCtlError(result.stderr.strip() or result.stdout.strip() or "runctl 执行失败")
    print(result.stdout.strip())


def smoke_init(args: argparse.Namespace) -> None:
    task_id = f"smoke-module-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}"
    command = [
        sys.executable, str(RUNCTL), "init",
        "--scenario", "module-full-analysis",
        "--target", "mini-storage-module",
        "--source-path", str(FIXTURE),
        "--task-id", task_id,
        "--max-parallel", str(args.max_parallel),
        "--max-audit-rounds", str(args.max_audit_rounds),
    ]
    emit_process(subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False))


def put_artifact(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = runctl.read_json(run_dir / "manifest.json")
    runctl.validate(manifest, "run-manifest.schema.json")
    artifact = runctl.read_json(Path(args.file).resolve())
    runctl.validate(artifact, "code-evidence.schema.json")
    item = next((entry for entry in manifest["planned_artifacts"] if entry["artifact_id"] == args.artifact_id), None)
    if item is None:
        raise runctl.RunCtlError(f"manifest 中不存在 artifact_id: {args.artifact_id}")
    if artifact.get("artifact_id") != args.artifact_id:
        raise runctl.RunCtlError("artifact_id 与命令参数不一致")
    for field in ("playbook", "target", "lens"):
        if artifact.get(field) != item.get(field):
            raise runctl.RunCtlError(f"证据字段 {field} 与 manifest 不一致")
    command = [
        sys.executable, str(RUNCTL), "put-artifact",
        "--run-dir", str(run_dir), "--artifact-id", args.artifact_id,
        "--file", str(Path(args.file).resolve()),
    ]
    emit_process(subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False))


def action_key(playbook: str, target: str, lens: str | None) -> str:
    return f"{playbook}|{target}|{lens or ''}"


def matching_artifact(manifest: dict[str, Any], playbook: str, target: str, lens: str | None) -> dict[str, Any] | None:
    matches = [
        item for item in manifest["planned_artifacts"]
        if item.get("playbook") == playbook and item.get("target") == target and item.get("lens") == lens
    ]
    return matches[-1] if matches else None


def plan_rework(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = runctl.read_json(manifest_path)
    runctl.validate(manifest, "run-manifest.schema.json")
    opinion_ref = args.audit_file or manifest["audit"].get("opinion_file")
    if not opinion_ref:
        raise runctl.RunCtlError("manifest 尚无已入库审计意见")
    opinion_path = Path(opinion_ref)
    if not opinion_path.is_absolute():
        opinion_path = run_dir / opinion_path
    opinion = runctl.read_json(opinion_path.resolve())
    runctl.validate(opinion, "audit-opinion.schema.json")
    if opinion["verdict"] == "PASS":
        raise runctl.RunCtlError("PASS 审计不需要回挖计划")
    audit_round = manifest["audit"]["rounds"]
    if audit_round < 1 or manifest["audit"]["status"] != opinion["verdict"]:
        raise runctl.RunCtlError("请先用 runctl.py apply-audit 将该审计意见入库")

    existing_ref = manifest["audit"].get("rework_plan_file")
    if existing_ref:
        existing = runctl.read_json(run_dir / existing_ref)
        if existing.get("audit_round") == audit_round:
            runctl.validate(existing, "rework-plan.schema.json")
            print(json.dumps(existing, ensure_ascii=False, indent=2))
            return

    scenario = runctl.load_scenario(manifest["scenario_id"])
    allowed_playbooks = set(scenario.get("playbooks", [])) | {"风险扫描"}
    allowed_lenses = set(scenario.get("baseline_lenses", []))
    automatic_allowed = audit_round < manifest["audit"]["max_rounds"]
    prior_keys = {
        action_key(item["playbook"], item["target"], item.get("lens"))
        for item in manifest["planned_artifacts"] if item.get("origin_audit_round") is not None
    }

    next_tasks: list[dict[str, Any]] = []
    manual_actions: list[dict[str, Any]] = []
    skipped_duplicates: list[str] = []
    sequence = 1
    for action in opinion["required_actions"]:
        if action["action_type"] != "re_excavate":
            manual_actions.append({**action, "blocked_reason": "该动作不属于自动代码回挖"})
            continue
        playbook = action.get("playbook")
        target = action.get("target")
        lens = action.get("lens")
        blocked: str | None = None
        if not automatic_allowed:
            blocked = "已达到最大审计轮数"
        elif target != manifest["target"]:
            blocked = "target 不属于本次任务"
        elif playbook not in allowed_playbooks:
            blocked = "playbook 未在 Registry 中登记"
        elif playbook == "风险扫描" and lens not in allowed_lenses:
            blocked = "风险扫描 lens 未在本场景登记"
        elif playbook != "风险扫描" and lens is not None:
            blocked = "非风险扫描任务不得指定 lens"
        if blocked:
            manual_actions.append({**action, "blocked_reason": blocked})
            continue
        assert isinstance(playbook, str) and isinstance(target, str)
        key = action_key(playbook, target, lens)
        if key in prior_keys:
            skipped_duplicates.append(key)
            continue
        superseded = matching_artifact(manifest, playbook, target, lens)
        artifact_id = f"rework-r{audit_round:02d}-{sequence:02d}"
        sequence += 1
        task = {
            "artifact_id": artifact_id,
            "action_type": "re_excavate",
            "playbook": playbook,
            "target": target,
            "lens": lens,
            "reason": action["reason"],
            "supersedes": superseded["artifact_id"] if superseded else None,
        }
        next_tasks.append(task)
        manifest["planned_artifacts"].append({
            "artifact_id": artifact_id,
            "artifact_type": "code_evidence",
            "playbook": playbook,
            "target": target,
            "lens": lens,
            "status": "pending",
            "artifact_file": None,
            "error": None,
            "origin_audit_round": audit_round,
            "reason": action["reason"],
            "supersedes": task["supersedes"],
        })
        prior_keys.add(key)

    plan = {
        "artifact_type": "rework_plan",
        "schema_version": "1.0",
        "task_id": manifest["task_id"],
        "audit_round": audit_round,
        "automatic_rework_allowed": automatic_allowed,
        "next_tasks": next_tasks,
        "manual_actions": manual_actions,
        "skipped_duplicates": skipped_duplicates,
    }
    runctl.validate(plan, "rework-plan.schema.json")
    plan_path = run_dir / "audit" / f"rework-round-{audit_round:02d}.json"
    runctl.atomic_write(plan_path, plan)
    manifest["audit"]["rework_plan_file"] = str(plan_path.relative_to(run_dir))
    runctl.validate(manifest, "run-manifest.schema.json")
    runctl.atomic_write(manifest_path, manifest)
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="PANGEA managed workflow extensions")
    sub = root.add_subparsers(dest="command", required=True)
    smoke = sub.add_parser("smoke-init")
    smoke.add_argument("--max-parallel", type=int, default=3)
    smoke.add_argument("--max-audit-rounds", type=int, default=2)
    smoke.set_defaults(func=smoke_init)
    put = sub.add_parser("put-artifact")
    put.add_argument("--run-dir", required=True)
    put.add_argument("--artifact-id", required=True)
    put.add_argument("--file", required=True)
    put.set_defaults(func=put_artifact)
    rework = sub.add_parser("plan-rework")
    rework.add_argument("--run-dir", required=True)
    rework.add_argument("--audit-file")
    rework.set_defaults(func=plan_rework)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
        return 0
    except runctl.RunCtlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
