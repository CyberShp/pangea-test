#!/usr/bin/env python3
"""Deterministic run-state controller for PANGEA-TEST.

The LLM performs semantic analysis; this module owns IDs, files, schema
validation, manifest transitions, audit rounds, and resume planning.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "registry" / "scenarios.json"
SCHEMAS = ROOT / "schemas"


class RunCtlError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunCtlError(f"文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunCtlError(f"JSON 无效: {path}: {exc}") from exc


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def validate(instance: dict[str, Any], schema_name: str) -> None:
    try:
        import jsonschema  # type: ignore
    except ImportError as exc:
        raise RunCtlError("缺少 jsonschema；执行: python -m pip install -r runtime/requirements.txt") from exc
    schema = read_json(SCHEMAS / schema_name)
    try:
        jsonschema.Draft202012Validator(schema).validate(instance)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise RunCtlError(f"schema 校验失败 [{location}]: {exc.message}") from exc


def slug(value: str) -> str:
    value = re.sub(r"[\\/:\s]+", "-", value.strip())
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value)
    return value.strip("-.") or "target"


def load_scenario(scenario_id: str) -> dict[str, Any]:
    registry = read_json(REGISTRY)
    try:
        return registry["scenarios"][scenario_id]
    except KeyError as exc:
        raise RunCtlError(f"未知 scenario_id: {scenario_id}") from exc


def create_run(args: argparse.Namespace) -> None:
    scenario = load_scenario(args.scenario)
    date = datetime.now().strftime("%Y%m%d")
    base_id = f"{args.scenario}-{slug(args.target)}-{date}"
    runs_root = Path(args.runs_root).resolve()
    task_id = args.task_id or base_id
    run_dir = runs_root / task_id
    if run_dir.exists():
        raise RunCtlError(f"任务目录已存在: {run_dir}；请使用 resume 或显式指定新的 --task-id")

    playbooks = scenario.get("playbooks", [])
    lenses = scenario.get("baseline_lenses", [])
    planned: list[dict[str, Any]] = []
    for index, playbook in enumerate(playbooks, 1):
        planned.append({
            "artifact_id": f"structure-{index:02d}", "artifact_type": "code_evidence",
            "playbook": playbook, "target": args.target, "lens": None,
            "status": "pending", "artifact_file": None, "error": None,
        })
    for index, lens in enumerate(lenses, 1):
        planned.append({
            "artifact_id": f"risk-{index:02d}", "artifact_type": "code_evidence",
            "playbook": "风险扫描", "target": args.target, "lens": lens,
            "status": "pending", "artifact_file": None, "error": None,
        })

    envelope = {
        "schema_version": "1.0", "task_id": task_id, "scenario_id": args.scenario,
        "mode": "deep",
        "target": {"name": args.target, "source_path": str(Path(args.source_path).resolve()), "symbols": args.symbol or []},
        "source": {"commit_sha": args.commit_sha, "mr": None, "dirty": False},
        "constraints": {"max_parallel_tasks": args.max_parallel, "max_audit_rounds": args.max_audit_rounds,
                        "evidence_required": True, "output_language": "zh-CN"},
        "workspace": {"run_dir": str(run_dir)},
        "requested_outputs": ["flow_explanation", "sfmea", "blackbox_scenarios", "test_cases", "coverage_audit"],
    }
    manifest = {
        "artifact_type": "run_manifest", "schema_version": "1.0", "task_id": task_id,
        "scenario_id": args.scenario, "target": args.target, "mode": "deep",
        "inputs_ref": [str(Path(args.source_path).resolve())], "planned_artifacts": planned,
        "summary_status": "pending",
        "audit": {"rounds": 0, "max_rounds": args.max_audit_rounds, "status": "pending", "opinion_file": None},
    }
    validate(envelope, "task-envelope.schema.json")
    validate(manifest, "run-manifest.schema.json")
    atomic_write(run_dir / "task-envelope.json", envelope)
    atomic_write(run_dir / "manifest.json", manifest)
    for name in ("evidence", "audit", "final", "logs"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    print(json.dumps({"task_id": task_id, "run_dir": str(run_dir), "planned_tasks": len(planned)}, ensure_ascii=False))


def put_artifact(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    artifact = read_json(Path(args.file).resolve())
    validate(artifact, "code-evidence.schema.json")
    if artifact.get("artifact_id") != args.artifact_id:
        raise RunCtlError("artifact_id 与命令参数不一致")
    target = next((item for item in manifest["planned_artifacts"] if item["artifact_id"] == args.artifact_id), None)
    if target is None:
        raise RunCtlError(f"manifest 中不存在 artifact_id: {args.artifact_id}")
    out = run_dir / "evidence" / f"{args.artifact_id}.json"
    atomic_write(out, artifact)
    target["status"] = artifact["status"]
    target["artifact_file"] = str(out.relative_to(run_dir))
    target["error"] = None
    validate(manifest, "run-manifest.schema.json")
    atomic_write(manifest_path, manifest)
    print(json.dumps({"accepted": args.artifact_id, "status": artifact["status"]}, ensure_ascii=False))


def apply_audit(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    opinion = read_json(Path(args.file).resolve())
    validate(opinion, "audit-opinion.schema.json")
    round_no = manifest["audit"]["rounds"] + 1
    if round_no > manifest["audit"]["max_rounds"]:
        raise RunCtlError("已达到最大审计轮数，禁止继续自动回挖")
    out = run_dir / "audit" / f"round-{round_no:02d}.json"
    atomic_write(out, opinion)
    manifest["audit"].update({"rounds": round_no, "status": opinion["verdict"], "opinion_file": str(out.relative_to(run_dir))})
    if opinion["verdict"] == "PASS":
        manifest["summary_status"] = "complete"
    validate(manifest, "run-manifest.schema.json")
    atomic_write(manifest_path, manifest)
    print(json.dumps({"round": round_no, "verdict": opinion["verdict"], "required_actions": opinion["required_actions"]}, ensure_ascii=False))


def resume(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    pending = [item for item in manifest["planned_artifacts"] if item["status"] in {"pending", "partial", "failed"}]
    print(json.dumps({"task_id": manifest["task_id"], "summary_status": manifest["summary_status"],
                      "audit": manifest["audit"], "next_tasks": pending}, ensure_ascii=False, indent=2))


def validate_file(args: argparse.Namespace) -> None:
    instance = read_json(Path(args.file).resolve())
    validate(instance, args.schema)
    print(json.dumps({"valid": True, "file": args.file, "schema": args.schema}, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PANGEA-TEST deterministic run controller")
    sub = p.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--scenario", default="module-full-analysis")
    init.add_argument("--target", required=True)
    init.add_argument("--source-path", required=True)
    init.add_argument("--symbol", action="append")
    init.add_argument("--commit-sha")
    init.add_argument("--task-id")
    init.add_argument("--runs-root", default=str(ROOT / "runs"))
    init.add_argument("--max-parallel", type=int, default=4)
    init.add_argument("--max-audit-rounds", type=int, default=2)
    init.set_defaults(func=create_run)
    put = sub.add_parser("put-artifact")
    put.add_argument("--run-dir", required=True); put.add_argument("--artifact-id", required=True); put.add_argument("--file", required=True)
    put.set_defaults(func=put_artifact)
    audit = sub.add_parser("apply-audit")
    audit.add_argument("--run-dir", required=True); audit.add_argument("--file", required=True); audit.set_defaults(func=apply_audit)
    res = sub.add_parser("resume"); res.add_argument("--run-dir", required=True); res.set_defaults(func=resume)
    val = sub.add_parser("validate"); val.add_argument("--file", required=True); val.add_argument("--schema", required=True); val.set_defaults(func=validate_file)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
        return 0
    except RunCtlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
