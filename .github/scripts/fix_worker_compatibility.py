from __future__ import annotations

import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]

# Legacy complete Runs retain their pre-P1 Coverage Judge shape. Lifecycle Runs add evidence/worker bindings.
path = root / "schemas/coverage-judge.schema.json"
schema = json.loads(path.read_text(encoding="utf-8"))
for field in ("evidence_artifact", "worker_artifact"):
    while field in schema["required"]:
        schema["required"].remove(field)
while "worker_provenance" in schema["properties"]["checks"]["required"]:
    schema["properties"]["checks"]["required"].remove("worker_provenance")
path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

path = root / "runtime/coverage_judge.py"
text = path.read_text(encoding="utf-8")
old = '''    if workers is None:
        findings["worker_provenance"].append("缺少 worker index")
    else:
        required_workers = set(map(str, workers.get("required_workers", [])))
        rows = {str(item.get("worker")): item for item in workers.get("workers", []) if isinstance(item, dict)}
        for worker in sorted(required_workers - set(rows)):
            findings["worker_provenance"].append(f"缺少 required worker receipt: {worker}")
        if workers.get("identity_verified") is not False or workers.get("provenance_strength") != "repository_declared":
            findings["worker_provenance"].append("worker provenance 强度声明不诚实或字段漂移")
'''
new = '''    if workers is not None:
        required_workers = set(map(str, workers.get("required_workers", [])))
        rows = {str(item.get("worker")): item for item in workers.get("workers", []) if isinstance(item, dict)}
        for worker in sorted(required_workers - set(rows)):
            findings["worker_provenance"].append(f"缺少 required worker receipt: {worker}")
        if workers.get("identity_verified") is not False or workers.get("provenance_strength") != "repository_declared":
            findings["worker_provenance"].append("worker provenance 强度声明不诚实或字段漂移")
'''
if text.count(old) != 1:
    raise SystemExit(f"coverage worker block count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

path = root / "runtime/runctl.py"
text = path.read_text(encoding="utf-8")
start = text.index("def _run_coverage_judge(run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:\n")
end = text.index("\ndef _invalidate_fixed_artifact(path: Path) -> None:\n", start)
replacement = '''def _run_coverage_judge(run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    from runtime import coverage_judge, data_runtime

    analysis_path = _analysis_model_path(run_dir)
    report_path = _fixed_audit_model(run_dir)
    ledger_path = run_dir / "internal" / "risk-ledger.json"
    lifecycle = _evidence_required(run_dir)
    root = run_dir.parents[2]
    plan = _load_v2_workflow_plan(run_dir)
    evidence = _validated_evidence(root, run_dir, contract, required=lifecycle)
    workers = _validated_worker_index(run_dir, plan, required=lifecycle)
    analysis = _validate_analysis_model(data_runtime.read_json(analysis_path), contract, run_dir.name, evidence)
    if lifecycle:
        _assert_worker_contributions(run_dir, plan, analysis)
    report = _assert_report_contract_and_sections(run_dir, data_runtime.read_json(report_path))
    ledger = data_runtime.read_json(ledger_path)
    validate(ledger, "risk-ledger.schema.json")
    judged = coverage_judge.judge(analysis, report, ledger, workers)
    payload = {
        "artifact_type": "coverage_judge", "schema_version": "1.0", "run_id": run_dir.name,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "analysis_artifact": _binding(analysis_path, ANALYSIS_MODEL_RELATIVE),
        "report_artifact": _binding(report_path, AUDITED_MODEL_RELATIVE),
        "risk_ledger_artifact": _binding(ledger_path, "internal/risk-ledger.json"),
        "verdict": judged["verdict"], "checks": judged["checks"],
    }
    if lifecycle:
        payload["evidence_artifact"] = _evidence_binding(root, run_dir, contract, required=True)
        payload["worker_artifact"] = _worker_binding(run_dir, plan, required=True)
    validate(payload, "coverage-judge.schema.json")
    data_runtime.atomic_write_json(_coverage_judge_path(run_dir), payload)
    return payload


def _coverage_judge_binding(run_dir: Path, contract: dict[str, Any], *, required: bool) -> dict[str, Any] | None:
    if not required:
        return None
    path = _coverage_judge_path(run_dir)
    if not path.is_file():
        raise RunCtlError(f"完整型模块分析缺少独立 Coverage Judge 工件: {COVERAGE_JUDGE_RELATIVE}")
    payload = read_json(path)
    validate(payload, "coverage-judge.schema.json")
    expected = {
        "analysis_artifact": _binding(_analysis_model_path(run_dir), ANALYSIS_MODEL_RELATIVE),
        "report_artifact": _binding(_fixed_audit_model(run_dir), AUDITED_MODEL_RELATIVE),
        "risk_ledger_artifact": _binding(run_dir / "internal" / "risk-ledger.json", "internal/risk-ledger.json"),
    }
    if _evidence_required(run_dir):
        expected["evidence_artifact"] = _evidence_binding(run_dir.parents[2], run_dir, contract, required=True)
        expected["worker_artifact"] = _worker_binding(run_dir, _load_v2_workflow_plan(run_dir), required=True)
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RunCtlError(f"Coverage Judge 的 {key} 已过期，必须重新执行")
    if payload.get("verdict") != "PASS":
        raise RunCtlError("独立 Coverage Judge 未通过，禁止提交 auditor 或完成 Run")
    return {"path": COVERAGE_JUDGE_RELATIVE, "sha256": _sha256_file(path), "verdict": "PASS"}

'''
text = text[:start] + replacement + text[end + 1:]

old = '''    if evidence is not None:
        model["evidence_artifact"] = _evidence_binding(root, run_dir, contract, required=True)
        model["worker_artifact"] = _worker_binding(run_dir, plan, required=True)
    normalized = _validate_analysis_model(model, contract, args.run_id, evidence)
    if evidence is not None:
        _assert_worker_contributions(run_dir, plan, normalized)
'''
new = '''    model.pop("worker_artifact", None)
    if evidence is not None:
        model["evidence_artifact"] = _evidence_binding(root, run_dir, contract, required=True)
    # Validate source/material IDs before worker completeness so the closest provenance error is reported first.
    normalized = _validate_analysis_model(model, contract, args.run_id, evidence)
    if evidence is not None:
        normalized["worker_artifact"] = _worker_binding(run_dir, plan, required=True)
        normalized = _validate_analysis_model(normalized, contract, args.run_id, evidence)
        _assert_worker_contributions(run_dir, plan, normalized)
'''
if text.count(old) != 1:
    raise SystemExit(f"stage analysis ordering count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
