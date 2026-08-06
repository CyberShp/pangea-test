from __future__ import annotations

import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (root / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


# ---------------- data_runtime: immutable content-addressed stage artifacts ----------------
data = read("runtime/data_runtime.py")
start = data.index("def _verify_checkpoint_artifacts(run_dir: Path, manifest: dict[str, Any], checkpoint: dict[str, Any]) -> None:\n")
end = data.index("\ndef append_checkpoint(root: Path, run_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:\n", start)
replacement = r'''def _verify_checkpoint_artifacts(run_dir: Path, manifest: dict[str, Any], checkpoint: dict[str, Any]) -> None:
    """Lifecycle checkpoints bind append-only, content-addressed stage artifacts."""
    if manifest.get("contract_record_file") != "internal/contract-record.json":
        return
    stage = checkpoint.get("stage")
    if checkpoint.get("status", "completed") != "completed" or stage in {"report", "rework"}:
        return
    bindings = checkpoint.get("artifact_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise DataRuntimeError("生命周期 Run 的 completed checkpoint 必须提供 artifact_bindings")
    pattern = re.compile(rf"^internal/stages/{re.escape(str(stage))}-([0-9a-f]{{12}})[.]json$")
    found = False
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise DataRuntimeError("checkpoint artifact binding 必须只包含 path 和 sha256")
        raw = binding.get("path")
        if not isinstance(raw, str) or Path(raw).is_absolute() or ".." in Path(raw).parts or Path(raw).as_posix() != raw:
            raise DataRuntimeError(f"checkpoint artifact 路径不安全: {raw}")
        artifact = run_dir / raw
        _require_regular_file(artifact, run_dir, "checkpoint 绑定工件")
        digest = sha256_file(artifact)
        if digest != binding.get("sha256"):
            raise DataRuntimeError(f"checkpoint artifact SHA-256 已过期: {raw}")
        match = pattern.fullmatch(raw)
        if match:
            if not digest.startswith(match.group(1)):
                raise DataRuntimeError(f"阶段工件文件名摘要与内容不一致: {raw}")
            payload = read_json(artifact)
            if not isinstance(payload, dict):
                raise DataRuntimeError(f"阶段工件必须是 JSON 对象: {raw}")
            validate_runtime_record(payload, "stage-artifact.schema.json")
            if payload.get("run_id") != run_dir.name or payload.get("stage") != stage:
                raise DataRuntimeError(f"阶段工件 run_id/stage 与 checkpoint 不一致: {raw}")
            found = True
    if not found:
        raise DataRuntimeError(f"checkpoint 必须绑定内容寻址阶段工件: internal/stages/{stage}-<sha12>.json")


def _invalidate_lifecycle_after_checkpoint(run_dir: Path, manifest: dict[str, Any], stage: str) -> None:
    if manifest.get("contract_record_file") != "internal/contract-record.json" or stage in {"report", "rework"}:
        return
    paths = [
        run_dir / "internal/analysis-model.json", run_dir / "internal/report-model.json",
        run_dir / "internal/coverage-judge.json", run_dir / "internal/auditor-receipt.json",
    ]
    if stage in {"code_map", "flow", "branches", "dfx_scan", "impact_chain", "mr_baseline", "dfx_route"}:
        paths.append(run_dir / "internal/worker-index.json")
    for path in paths:
        if path.is_symlink():
            raise DataRuntimeError(f"拒绝删除符号链接下游工件: {path}")
        if path.exists():
            if not path.is_file() or path.resolve().parent != (run_dir / "internal").resolve():
                raise DataRuntimeError(f"拒绝删除异常下游工件: {path}")
            path.unlink()


'''
data = data[:start] + replacement + data[end + 1:]
old = '''    validate_runtime_record(manifest, "session-manifest.schema.json")
    atomic_write_json(run_dir / "manifest.json", manifest)
    return checkpoint
'''
new = '''    validate_runtime_record(manifest, "session-manifest.schema.json")
    atomic_write_json(run_dir / "manifest.json", manifest)
    _invalidate_lifecycle_after_checkpoint(run_dir, manifest, checkpoint["stage"])
    return checkpoint
'''
data = replace_once(data, old, new, "checkpoint downstream invalidation")
write("runtime/data_runtime.py", data)

# ---------------- runctl: stage artifact addressing and worker inputs ----------------
runctl = read("runtime/runctl.py")
start = runctl.index("def _stage_artifact_path(run_dir: Path, stage: str) -> Path:\n")
end = runctl.index("\ndef _required_worker_ids(plan: dict[str, Any]) -> list[str]:\n", start)
replacement = r'''def _stage_artifact_path(run_dir: Path, stage: str, digest: str) -> Path:
    if not isinstance(stage, str) or re.fullmatch(r"[a-z_]+", stage) is None:
        raise RunCtlError("stage 名称非法")
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise RunCtlError("阶段工件摘要非法")
    directory = run_dir / "internal" / "stages"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stage}-{digest[:12]}.json"
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise RunCtlError("阶段工件路径异常")
    return path.resolve()


def stage_work_product_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, evidence_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入阶段工件")
    if manifest.get("audit", {}).get("status") == "PASS":
        raise RunCtlError("审计 PASS 后不得写入阶段工件")
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-work-product-v2 仅用于生命周期 Run")
    plan = _load_v2_workflow_plan(run_dir)
    if args.stage not in [stage for stage in plan["stages"] if stage != "report"]:
        raise RunCtlError(f"阶段不属于当前 workflow plan: {args.stage}")
    source = Path(args.file).expanduser()
    if source.is_symlink() or not source.is_file():
        raise RunCtlError("阶段工件输入必须是普通文件")
    payload = read_json(source.resolve())
    validate(payload, "stage-artifact.schema.json")
    if payload.get("run_id") != args.run_id or payload.get("stage") != args.stage:
        raise RunCtlError("阶段工件 run_id/stage 与命令不一致")
    if not payload.get("evidence_ids"):
        raise RunCtlError("阶段工件必须引用至少一条固定 evidence ID")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))
    evidence = _validated_evidence(root, run_dir, contract, required=True)
    unknown = set(payload["evidence_ids"]) - evidence_runtime.reference_ids(evidence)
    if unknown:
        raise RunCtlError(f"阶段工件引用未知 evidence provenance ID: {sorted(unknown)}")
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    target = _stage_artifact_path(run_dir, args.stage, digest)
    if target.is_file():
        if _sha256_file(target) != digest:
            raise RunCtlError("内容寻址阶段工件发生摘要冲突")
    else:
        data_runtime.atomic_write_json(target, payload)
    binding = _safe_run_binding(run_dir, f"internal/stages/{target.name}")
    print(json.dumps({"run_id": args.run_id, "stage": args.stage, "artifact_binding": binding,
                      "next_step": "data checkpoint"}, ensure_ascii=False))


'''
runctl = runctl[:start] + replacement + runctl[end + 1:]

# Add stable prerequisite-stage binding helpers after required worker IDs.
marker = '\ndef _worker_receipt_path(run_dir: Path, worker: str) -> Path:\n'
helpers = r'''

_WORKER_PREREQUISITE_STAGES = {
    "module-analysis": ("code_map", "flow", "branches", "dfx_scan"),
    "mr-regression": ("code_map", "impact_chain", "mr_baseline", "dfx_route"),
}


def _effective_checkpoint_bindings(run_dir: Path, plan: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    # This also validates every historical checkpoint and its immutable stage artifact.
    _v2_progress(run_dir, plan)
    result: dict[str, list[dict[str, str]]] = {}
    directory = run_dir / "checkpoints"
    if not directory.is_dir():
        return result
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        checkpoint = read_json(path)
        if checkpoint.get("status", "completed") == "completed":
            result[str(checkpoint.get("stage"))] = list(checkpoint.get("artifact_bindings") or [])
    return result


def _worker_input_bindings(root: Path, run_dir: Path, contract: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, str]]:
    bindings = [
        _safe_run_binding(run_dir, "internal/task-contract.json"),
        _evidence_binding(root, run_dir, contract, required=True),
    ]
    snapshots = run_dir / "internal/source-snapshots.json"
    if snapshots.is_file():
        bindings.append(_safe_run_binding(run_dir, "internal/source-snapshots.json"))
    effective = _effective_checkpoint_bindings(run_dir, plan)
    for stage in _WORKER_PREREQUISITE_STAGES[plan["workflow"]]:
        stage_bindings = effective.get(stage)
        if not stage_bindings:
            raise RunCtlError(f"worker receipt 缺少前置阶段 checkpoint/artifact: {stage}")
        bindings.extend(stage_bindings)
    return bindings


'''
runctl = replace_once(runctl, marker, helpers + marker, "worker prerequisite helpers")

# Worker and auditor writes cannot mutate terminal/PASS Runs.
old = '''    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-worker-receipt-v2 仅用于生命周期 Run")
    if manifest.get("audit", {}).get("status") == "PASS":
'''
new = '''    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入 worker receipt")
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-worker-receipt-v2 仅用于生命周期 Run")
    if manifest.get("audit", {}).get("status") == "PASS":
'''
runctl = replace_once(runctl, old, new, "worker terminal gate")
old = '''    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))
    input_artifacts = [
        _safe_run_binding(run_dir, "internal/task-contract.json"),
        _evidence_binding(root, run_dir, contract, required=True),
    ]
    snapshots = run_dir / "internal/source-snapshots.json"
    if snapshots.is_file():
        input_artifacts.append(_safe_run_binding(run_dir, "internal/source-snapshots.json"))
'''
new = '''    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))
    input_artifacts = _worker_input_bindings(root, run_dir, contract, plan)
'''
runctl = replace_once(runctl, old, new, "worker input binding")

# Revalidate every receipt's inputs and reject duplicate/extra workers.
old = '''    rows = {row["worker"]: row for row in index["workers"]}
    missing = sorted(set(index["required_workers"]) - set(rows))
    if missing:
        raise RunCtlError("缺少 required worker receipts: " + ", ".join(missing))
    for worker, row in rows.items():
        expected = _safe_run_binding(run_dir, f"internal/workers/{worker}.json")
        if row["receipt"] != expected:
            raise RunCtlError(f"worker receipt binding 已过期: {worker}")
        receipt = read_json(run_dir / expected["path"]); validate(receipt, "worker-receipt.schema.json")
        if receipt["worker"] != worker or receipt["dfx"] != DFX_WORKERS[worker]:
            raise RunCtlError(f"worker receipt 身份字段不一致: {worker}")
'''
new = '''    rows = {row["worker"]: row for row in index["workers"]}
    if len(rows) != len(index["workers"]):
        raise RunCtlError("worker index 存在重复 worker")
    required_workers = set(index["required_workers"])
    missing = sorted(required_workers - set(rows))
    extras = sorted(set(rows) - required_workers)
    if missing:
        raise RunCtlError("缺少 required worker receipts: " + ", ".join(missing))
    if extras:
        raise RunCtlError("worker index 包含 workflow plan 外 worker: " + ", ".join(extras))
    root = run_dir.parents[2]
    contract = _assert_formal_task_contract(read_json(run_dir / "internal/task-contract.json"))
    expected_inputs = _worker_input_bindings(root, run_dir, contract, plan)
    for worker, row in rows.items():
        expected = _safe_run_binding(run_dir, f"internal/workers/{worker}.json")
        if row["receipt"] != expected:
            raise RunCtlError(f"worker receipt binding 已过期: {worker}")
        receipt = read_json(run_dir / expected["path"]); validate(receipt, "worker-receipt.schema.json")
        if receipt["worker"] != worker or receipt["dfx"] != DFX_WORKERS[worker]:
            raise RunCtlError(f"worker receipt 身份字段不一致: {worker}")
        if receipt["input_artifacts"] != expected_inputs:
            raise RunCtlError(f"worker receipt 输入绑定已过期: {worker}")
'''
runctl = replace_once(runctl, old, new, "worker input revalidation")

# Contributions also validate risk IDs and blocked-worker closure.
old = '''    item_ids = _analysis_item_ids(model)
    applicability = {item.get("dfx"): item for item in model.get("model_applicability", []) if isinstance(item, dict)}
    for row in index["workers"]:
        receipt = read_json(run_dir / row["receipt"]["path"])
        unknown = set(receipt["contribution_ids"]) - item_ids
'''
new = '''    item_ids = _analysis_item_ids(model)
    applicability = {item.get("dfx"): item for item in model.get("model_applicability", []) if isinstance(item, dict)}
    unresolved = {str(item.get("item_id")) for item in model.get("unresolved", []) if isinstance(item, dict)}
    ledger = read_json(run_dir / "internal/risk-ledger.json")
    risk_ids = {str(item.get("risk_id")) for item in ledger.get("risks", []) if isinstance(item, dict)}
    for row in index["workers"]:
        receipt = read_json(run_dir / row["receipt"]["path"])
        unknown = set(receipt["contribution_ids"]) - item_ids
'''
runctl = replace_once(runctl, old, new, "worker risk setup")
old = '''        if receipt["status"] == "not_applicable" and applicability.get(receipt["dfx"], {}).get("applicable") is not False:
            raise RunCtlError(f"worker not_applicable 与 model_applicability 不一致: {receipt['worker']}")
'''
new = '''        unknown_risks = set(receipt["risk_ids"]) - risk_ids
        if unknown_risks:
            raise RunCtlError(f"worker {receipt['worker']} 引用风险账本外 risk IDs: {sorted(unknown_risks)}")
        if receipt["status"] == "not_applicable" and applicability.get(receipt["dfx"], {}).get("applicable") is not False:
            raise RunCtlError(f"worker not_applicable 与 model_applicability 不一致: {receipt['worker']}")
        if receipt["status"] == "blocked" and receipt["worker"] not in unresolved:
            raise RunCtlError(f"blocked worker 必须进入 analysis-model unresolved: {receipt['worker']}")
'''
runctl = replace_once(runctl, old, new, "worker blocked/risk checks")

# Auditor receipt write gate.
old = '''    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-auditor-receipt-v2 仅用于生命周期 Run")
'''
new = '''    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入 auditor receipt")
    if manifest.get("audit", {}).get("status") == "PASS":
        raise RunCtlError("审计 PASS 后不得重写 auditor receipt")
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-auditor-receipt-v2 仅用于生命周期 Run")
'''
runctl = replace_once(runctl, old, new, "auditor write gate")
write("runtime/runctl.py", runctl)

# ---------------- tests: content-addressed helper and positive auditor closure ----------------
path = root / "tests/test_analysis_depth_contract.py"
text = path.read_text(encoding="utf-8")
old = '''            artifact = run_dir / "internal" / "stages" / f"{stage}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            data_runtime.atomic_write_json(artifact, {
                "artifact_type": "stage_artifact", "schema_version": "1.0", "run_id": run_id,
                "stage": stage, "summary": f"{stage} 阶段已形成可复核结构化工件",
                "evidence_ids": ["EV-1"], "item_ids": [stage.upper()], "open_items": [],
            })
            return [{"path": f"internal/stages/{stage}.json", "sha256": data_runtime.sha256_file(artifact)}]
'''
new = '''            payload = {
                "artifact_type": "stage_artifact", "schema_version": "1.0", "run_id": run_id,
                "stage": stage, "summary": f"{stage} 阶段已形成可复核结构化工件",
                "evidence_ids": ["EV-1"], "item_ids": [stage.upper()], "open_items": [],
            }
            encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\\n").encode("utf-8")
            digest = hashlib.sha256(encoded).hexdigest()
            artifact = run_dir / "internal" / "stages" / f"{stage}-{digest[:12]}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            data_runtime.atomic_write_json(artifact, payload)
            return [{"path": f"internal/stages/{artifact.name}", "sha256": data_runtime.sha256_file(artifact)}]
'''
if text.count(old) != 1:
    raise SystemExit(f"content addressed helper count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

path = root / "tests/test_worker_audit_provenance.py"
text = path.read_text(encoding="utf-8")
insert = r'''
    @staticmethod
    def risk() -> dict:
        return {"artifact_type": "risk_card", "schema_version": "1.0", "risk_id": "R-1",
                "title": "错误后状态残留", "dfx": ["功能与状态"], "severity": "High", "confidence": "high",
                "trigger": "先发送非法请求", "propagation": "错误路径未恢复状态", "external_impact": "后续正常请求失败",
                "observation": "返回码、日志和后续业务", "recovery": "修正请求后业务应恢复",
                "translation_status": "Blackbox-ready", "test_explanation": "验证错误不影响后续正常业务。",
                "instrumentation_request": None, "evidence": [{"location": "EV-1", "observation": "固定源码证据"}],
                "status": "open"}

    def stage_analysis_and_report(self, root: Path, run_dir: Path) -> Path:
        self.stage_workers(root, run_dir); self.stage_artifacts_and_checkpoints(root, run_dir)
        risk = self.risk(); data_runtime.upsert_risk(root, run_dir.name, risk)
        model = self.lifecycle_model(run_dir)
        model_path = root / "analysis-model.json"; model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(model_path))
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        draft = {"title": "Worker provenance 报告", "summary": "验证固定 worker 与 auditor 输入绑定。",
                 "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                 "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
        draft_path = root / "report-draft.json"; draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        staged = self.cli(root, "stage-report-v2", "--run-id", run_dir.name, "--file", str(draft_path))
        return Path(staged["report_model"])

    def test_auditor_receipt_is_required_and_positive_audit_closes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "audit-positive")
            report_path = self.stage_analysis_and_report(root, run_dir)
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0",
                       "audited_artifact": "internal/report-model.json",
                       "audited_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                       "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            opinion_path = root / "audit.json"; opinion_path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "apply-audit-v2", "--run-id", run_dir.name, "--file", str(opinion_path), expected=2)
            self.assertIn("auditor-receipt", rejected["stderr"])
            receipt = self.cli(root, "stage-auditor-receipt-v2", "--run-id", run_dir.name,
                               "--producer-invocation-id", "producer-declared-01",
                               "--auditor-invocation-id", "auditor-declared-02")
            self.assertFalse(receipt["identity_verified"])
            audited = self.cli(root, "apply-audit-v2", "--run-id", run_dir.name, "--file", str(opinion_path))
            self.assertEqual("PASS", audited["verdict"])

    def test_tampered_auditor_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "audit-stale")
            report_path = self.stage_analysis_and_report(root, run_dir)
            self.cli(root, "stage-auditor-receipt-v2", "--run-id", run_dir.name,
                     "--producer-invocation-id", "producer-declared-01",
                     "--auditor-invocation-id", "auditor-declared-02")
            receipt_path = run_dir / "internal/auditor-receipt.json"
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["audited_inputs"][0]["sha256"] = "0" * 64
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0",
                       "audited_artifact": "internal/report-model.json",
                       "audited_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                       "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            opinion_path = root / "audit-stale.json"; opinion_path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "apply-audit-v2", "--run-id", run_dir.name, "--file", str(opinion_path), expected=2)
            self.assertIn("输入绑定已过期", rejected["stderr"])

'''
marker = '\n    def test_auditor_receipt_rejects_same_declared_invocation_id(self) -> None:\n'
if text.count(marker) != 1:
    raise SystemExit(f"positive auditor test marker count={text.count(marker)}")
text = text.replace(marker, "\n" + insert + marker, 1)
path.write_text(text, encoding="utf-8")

# Document append-only stage paths.
for relative in (".opencode/agents/pangea-test.md", ".opencode/commands/module-analysis.md", ".opencode/commands/mr-regression.md"):
    path = root / relative
    text = path.read_text(encoding="utf-8")
    text = text.replace("internal/stages/<stage>.json", "internal/stages/<stage>-<sha12>.json")
    path.write_text(text, encoding="utf-8")
