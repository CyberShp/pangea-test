from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, got {count}")
    return text.replace(old, new, 1)


DFX_WORKERS = {
    "dfx-function-state": "功能与状态",
    "dfx-resource-spec": "资源与规格",
    "dfx-performance-pressure": "性能与压力",
    "dfx-concurrency-exception": "并发与异常",
    "dfx-upgrade-compatibility": "升级与兼容",
    "dfx-reliability-consistency": "可靠性与一致性",
}

# ---------- Schemas ----------
binding = {
    "type": "object", "additionalProperties": False, "required": ["path", "sha256"],
    "properties": {"path": {"type": "string", "minLength": 1},
                   "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
}

stage_artifact = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "PANGEA Stage Artifact",
    "type": "object", "additionalProperties": False,
    "required": ["artifact_type", "schema_version", "run_id", "stage", "summary", "evidence_ids", "item_ids", "open_items"],
    "properties": {
        "artifact_type": {"const": "stage_artifact"}, "schema_version": {"const": "1.0"},
        "run_id": {"type": "string", "minLength": 1},
        "stage": {"type": "string", "pattern": "^[a-z_]+$"},
        "summary": {"type": "string", "minLength": 12},
        "evidence_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "item_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "open_items": {"type": "array", "items": {"type": "string", "minLength": 4}},
    },
}
write("schemas/stage-artifact.schema.json", json.dumps(stage_artifact, ensure_ascii=False, indent=2) + "\n")

worker_result = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "PANGEA Worker Result",
    "type": "object", "additionalProperties": False,
    "required": ["worker", "invocation_id", "assigned_scope", "searched_scope", "contribution_ids", "risk_ids", "status", "remaining_scope"],
    "properties": {
        "worker": {"enum": list(DFX_WORKERS)}, "invocation_id": {"type": "string", "minLength": 8},
        "assigned_scope": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 4}},
        "searched_scope": {"type": "array", "items": {"type": "string", "minLength": 4}},
        "contribution_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "risk_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
        "status": {"enum": ["completed", "not_applicable", "blocked"]},
        "remaining_scope": {"type": "array", "items": {"type": "string", "minLength": 4}},
    },
}
write("schemas/worker-result.schema.json", json.dumps(worker_result, ensure_ascii=False, indent=2) + "\n")

worker_receipt = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "PANGEA Worker Receipt",
    "type": "object", "additionalProperties": False,
    "required": ["artifact_type", "schema_version", "run_id", "worker", "dfx", "invocation_id",
                 "provenance_strength", "identity_verified", "identity_attestation", "input_artifacts",
                 "assigned_scope", "searched_scope", "contribution_ids", "risk_ids", "status", "remaining_scope",
                 "limitations", "completed_at"],
    "properties": {
        "artifact_type": {"const": "worker_receipt"}, "schema_version": {"const": "1.0"},
        "run_id": {"type": "string", "minLength": 1}, "worker": {"enum": list(DFX_WORKERS)},
        "dfx": {"enum": list(DFX_WORKERS.values())}, "invocation_id": {"type": "string", "minLength": 8},
        "provenance_strength": {"const": "repository_declared"}, "identity_verified": {"const": False},
        "identity_attestation": {"type": "null"},
        "input_artifacts": {"type": "array", "minItems": 2, "items": binding},
        "assigned_scope": worker_result["properties"]["assigned_scope"],
        "searched_scope": worker_result["properties"]["searched_scope"],
        "contribution_ids": worker_result["properties"]["contribution_ids"],
        "risk_ids": worker_result["properties"]["risk_ids"], "status": worker_result["properties"]["status"],
        "remaining_scope": worker_result["properties"]["remaining_scope"],
        "limitations": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 12}},
        "completed_at": {"type": "string", "minLength": 1},
    },
}
write("schemas/worker-receipt.schema.json", json.dumps(worker_receipt, ensure_ascii=False, indent=2) + "\n")

worker_index = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "PANGEA Worker Index",
    "type": "object", "additionalProperties": False,
    "required": ["artifact_type", "schema_version", "run_id", "provenance_strength", "identity_verified", "required_workers", "workers", "limitations", "updated_at"],
    "properties": {
        "artifact_type": {"const": "worker_index"}, "schema_version": {"const": "1.0"},
        "run_id": {"type": "string", "minLength": 1}, "provenance_strength": {"const": "repository_declared"},
        "identity_verified": {"const": False},
        "required_workers": {"type": "array", "items": {"enum": list(DFX_WORKERS)}, "uniqueItems": True},
        "workers": {"type": "array", "items": {"type": "object", "additionalProperties": False,
            "required": ["worker", "dfx", "status", "receipt"],
            "properties": {"worker": {"enum": list(DFX_WORKERS)}, "dfx": {"enum": list(DFX_WORKERS.values())},
                           "status": {"enum": ["completed", "not_applicable", "blocked"]}, "receipt": binding}}},
        "limitations": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 12}},
        "updated_at": {"type": "string", "minLength": 1},
    },
}
write("schemas/worker-index.schema.json", json.dumps(worker_index, ensure_ascii=False, indent=2) + "\n")

auditor_receipt = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "title": "PANGEA Auditor Receipt",
    "type": "object", "additionalProperties": False,
    "required": ["artifact_type", "schema_version", "run_id", "producer_invocation_id", "auditor_invocation_id",
                 "provenance_strength", "identity_verified", "identity_attestation", "audited_inputs", "limitations", "created_at"],
    "properties": {
        "artifact_type": {"const": "auditor_receipt"}, "schema_version": {"const": "1.0"},
        "run_id": {"type": "string", "minLength": 1},
        "producer_invocation_id": {"type": "string", "minLength": 8},
        "auditor_invocation_id": {"type": "string", "minLength": 8},
        "provenance_strength": {"const": "repository_declared"}, "identity_verified": {"const": False},
        "identity_attestation": {"type": "null"},
        "audited_inputs": {"type": "array", "minItems": 5, "items": binding},
        "limitations": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 12}},
        "created_at": {"type": "string", "minLength": 1},
    },
}
write("schemas/auditor-receipt.schema.json", json.dumps(auditor_receipt, ensure_ascii=False, indent=2) + "\n")

# Checkpoint schema gains exact artifact bindings (optional for historical Runs, mandatory in runtime for lifecycle Runs).
checkpoint_schema = json.loads(read("schemas/stage-checkpoint.schema.json"))
checkpoint_schema["properties"]["artifact_bindings"] = {"type": "array", "items": binding}
write("schemas/stage-checkpoint.schema.json", json.dumps(checkpoint_schema, ensure_ascii=False, indent=2) + "\n")

# Analysis models carry the deterministic worker index binding.
analysis_schema = json.loads(read("schemas/analysis-model.schema.json"))
analysis_schema["properties"]["worker_artifact"] = {
    "type": "object", "additionalProperties": False, "required": ["path", "sha256"],
    "properties": {"path": {"const": "internal/worker-index.json"},
                   "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}},
}
write("schemas/analysis-model.schema.json", json.dumps(analysis_schema, ensure_ascii=False, indent=2) + "\n")

# Coverage Judge binds evidence and workers and adds worker-provenance check.
judge_schema = json.loads(read("schemas/coverage-judge.schema.json"))
for name in ("evidence_artifact", "worker_artifact"):
    judge_schema["required"].insert(judge_schema["required"].index("verdict"), name)
    judge_schema["properties"][name] = {"$ref": "#/$defs/binding"}
judge_schema["properties"]["checks"]["required"].append("worker_provenance")
judge_schema["properties"]["checks"]["properties"]["worker_provenance"] = {"$ref": "#/$defs/check"}
write("schemas/coverage-judge.schema.json", json.dumps(judge_schema, ensure_ascii=False, indent=2) + "\n")

# ---------- data_runtime checkpoint hash enforcement ----------
data = read("runtime/data_runtime.py")
insert_before_append = r'''

def _verify_checkpoint_artifacts(run_dir: Path, manifest: dict[str, Any], checkpoint: dict[str, Any]) -> None:
    """Lifecycle completed stages must bind their fixed stage artifact by current SHA-256."""
    if manifest.get("contract_record_file") != "internal/contract-record.json":
        return
    if checkpoint.get("status", "completed") != "completed" or checkpoint.get("stage") in {"report", "rework"}:
        return
    bindings = checkpoint.get("artifact_bindings")
    if not isinstance(bindings, list) or not bindings:
        raise DataRuntimeError("生命周期 Run 的 completed checkpoint 必须提供 artifact_bindings")
    expected_path = f"internal/stages/{checkpoint.get('stage')}.json"
    found = False
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise DataRuntimeError("checkpoint artifact binding 必须只包含 path 和 sha256")
        raw = binding.get("path")
        if not isinstance(raw, str) or Path(raw).is_absolute() or ".." in Path(raw).parts or Path(raw).as_posix() != raw:
            raise DataRuntimeError(f"checkpoint artifact 路径不安全: {raw}")
        artifact = run_dir / raw
        _require_regular_file(artifact, run_dir, "checkpoint 绑定工件")
        if sha256_file(artifact) != binding.get("sha256"):
            raise DataRuntimeError(f"checkpoint artifact SHA-256 已过期: {raw}")
        if raw == expected_path:
            found = True
    if not found:
        raise DataRuntimeError(f"checkpoint 必须绑定当前阶段固定工件: {expected_path}")


'''
marker = '\ndef append_checkpoint(root: Path, run_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:\n'
data = replace_once(data, marker, insert_before_append + marker, "checkpoint verifier")
# Verify historical checkpoints and new checkpoint.
data = replace_once(data, '        validate_runtime_record(existing, "stage-checkpoint.schema.json")\n',
                    '        validate_runtime_record(existing, "stage-checkpoint.schema.json")\n        _verify_checkpoint_artifacts(run_dir, manifest, existing)\n',
                    "historical binding validation")
data = replace_once(data, '    validate_runtime_record(checkpoint, "stage-checkpoint.schema.json")\n    _write_json_exclusive',
                    '    validate_runtime_record(checkpoint, "stage-checkpoint.schema.json")\n    _verify_checkpoint_artifacts(run_dir, manifest, checkpoint)\n    _write_json_exclusive',
                    "new binding validation")
write("runtime/data_runtime.py", data)

# ---------- runctl workers, stage artifacts, auditor receipt ----------
runctl = read("runtime/runctl.py")
runctl = replace_once(runctl, 'MR_DIFF_RELATIVE = "internal/mr.diff"\n',
                      'MR_DIFF_RELATIVE = "internal/mr.diff"\nWORKER_INDEX_RELATIVE = "internal/worker-index.json"\nAUDITOR_RECEIPT_RELATIVE = "internal/auditor-receipt.json"\n',
                      "provenance constants")
runctl = replace_once(runctl, 'DFX_AGENTS = ["功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性"]\n',
                      'DFX_AGENTS = ["功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性"]\nDFX_WORKERS = ' + repr(DFX_WORKERS) + '\n',
                      "worker map")

marker = '\ndef _evidence_provenance_path(run_dir: Path) -> Path:\n'
functions = r'''

def _safe_run_binding(run_dir: Path, relative: str) -> dict[str, str]:
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != relative:
        raise RunCtlError(f"Run 工件路径不安全: {relative}")
    path = run_dir / relative
    if path.is_symlink() or not path.is_file():
        raise RunCtlError(f"Run 工件不存在或不是普通文件: {relative}")
    try:
        path.resolve().relative_to(run_dir.resolve())
    except ValueError as exc:
        raise RunCtlError(f"Run 工件越界: {relative}") from exc
    return {"path": relative, "sha256": _sha256_file(path)}


def _stage_artifact_path(run_dir: Path, stage: str) -> Path:
    if not isinstance(stage, str) or re.fullmatch(r"[a-z_]+", stage) is None:
        raise RunCtlError("stage 名称非法")
    directory = run_dir / "internal" / "stages"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stage}.json"
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise RunCtlError("阶段工件路径异常")
    return path.resolve()


def stage_work_product_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
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
    target = _stage_artifact_path(run_dir, args.stage)
    data_runtime.atomic_write_json(target, payload)
    binding = _safe_run_binding(run_dir, f"internal/stages/{args.stage}.json")
    print(json.dumps({"run_id": args.run_id, "stage": args.stage, "artifact_binding": binding,
                      "next_step": "data checkpoint"}, ensure_ascii=False))


def _required_worker_ids(plan: dict[str, Any]) -> list[str]:
    by_dfx = {value: key for key, value in DFX_WORKERS.items()}
    required: list[str] = []
    for dfx in plan.get("dfx_agents", []):
        worker = by_dfx.get(dfx)
        if worker is None:
            raise RunCtlError(f"workflow plan 包含未知 DFX worker: {dfx}")
        required.append(worker)
    return required


def _worker_receipt_path(run_dir: Path, worker: str) -> Path:
    if worker not in DFX_WORKERS:
        raise RunCtlError(f"未知 worker: {worker}")
    directory = run_dir / "internal" / "workers"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{worker}.json"
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise RunCtlError("worker receipt 路径异常")
    return path.resolve()


def _worker_index_path(run_dir: Path) -> Path:
    path = run_dir / WORKER_INDEX_RELATIVE
    if path.is_symlink() or path.resolve().parent != (run_dir / "internal").resolve():
        raise RunCtlError("worker index 路径异常")
    return path.resolve()


def _write_worker_index(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    from runtime import data_runtime
    rows = []
    for worker in sorted(DFX_WORKERS):
        path = _worker_receipt_path(run_dir, worker)
        if not path.is_file():
            continue
        receipt = read_json(path); validate(receipt, "worker-receipt.schema.json")
        rows.append({"worker": worker, "dfx": DFX_WORKERS[worker], "status": receipt["status"],
                     "receipt": _safe_run_binding(run_dir, f"internal/workers/{worker}.json")})
    payload = {"artifact_type": "worker_index", "schema_version": "1.0", "run_id": run_dir.name,
               "provenance_strength": "repository_declared", "identity_verified": False,
               "required_workers": _required_worker_ids(plan), "workers": rows,
               "limitations": ["仓库运行时仅验证声明 ID、工件哈希和职责覆盖，无法认证客户端真实子 Agent 身份。"],
               "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    validate(payload, "worker-index.schema.json")
    data_runtime.atomic_write_json(_worker_index_path(run_dir), payload)
    return payload


def stage_worker_receipt_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-worker-receipt-v2 仅用于生命周期 Run")
    if manifest.get("audit", {}).get("status") == "PASS":
        raise RunCtlError("审计 PASS 后不得改写 worker receipt")
    plan = _load_v2_workflow_plan(run_dir)
    source = Path(args.file).expanduser()
    if source.is_symlink() or not source.is_file():
        raise RunCtlError("worker result 输入必须是普通文件")
    result = read_json(source.resolve()); validate(result, "worker-result.schema.json")
    worker = result["worker"]
    if worker not in _required_worker_ids(plan):
        raise RunCtlError(f"worker 未被当前 workflow plan 路由: {worker}")
    if result["status"] == "completed" and (not result["searched_scope"] or not result["contribution_ids"]):
        raise RunCtlError("completed worker 必须提供 searched_scope 和 contribution_ids")
    if result["status"] in {"blocked", "not_applicable"} and not result["remaining_scope"]:
        raise RunCtlError(f"{result['status']} worker 必须说明 remaining_scope")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))
    input_artifacts = [
        _safe_run_binding(run_dir, "internal/task-contract.json"),
        _evidence_binding(root, run_dir, contract, required=True),
    ]
    snapshots = run_dir / "internal/source-snapshots.json"
    if snapshots.is_file():
        input_artifacts.append(_safe_run_binding(run_dir, "internal/source-snapshots.json"))
    payload = {"artifact_type": "worker_receipt", "schema_version": "1.0", "run_id": args.run_id,
               "worker": worker, "dfx": DFX_WORKERS[worker], "invocation_id": result["invocation_id"],
               "provenance_strength": "repository_declared", "identity_verified": False,
               "identity_attestation": None, "input_artifacts": input_artifacts,
               "assigned_scope": result["assigned_scope"], "searched_scope": result["searched_scope"],
               "contribution_ids": result["contribution_ids"], "risk_ids": result["risk_ids"],
               "status": result["status"], "remaining_scope": result["remaining_scope"],
               "limitations": ["invocation_id 由调用方声明；当前仓库运行时无法验证真实子 Agent 会话身份。"],
               "completed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    validate(payload, "worker-receipt.schema.json")
    data_runtime.atomic_write_json(_worker_receipt_path(run_dir, worker), payload)
    index = _write_worker_index(run_dir, plan)
    for path in (_analysis_model_path(run_dir), _fixed_audit_model(run_dir), _coverage_judge_path(run_dir),
                 run_dir / AUDITOR_RECEIPT_RELATIVE):
        _invalidate_fixed_artifact(path)
    print(json.dumps({"run_id": args.run_id, "worker": worker, "receipt": f"internal/workers/{worker}.json",
                      "worker_index": _safe_run_binding(run_dir, WORKER_INDEX_RELATIVE),
                      "remaining_workers": sorted(set(index["required_workers"]) - {row["worker"] for row in index["workers"]})},
                     ensure_ascii=False))


def _validated_worker_index(run_dir: Path, plan: dict[str, Any], *, required: bool) -> dict[str, Any] | None:
    path = _worker_index_path(run_dir)
    if not path.is_file():
        if required:
            raise RunCtlError("生命周期 Run 缺少固定 worker-index.json")
        return None
    index = read_json(path); validate(index, "worker-index.schema.json")
    if index.get("run_id") != run_dir.name or index.get("required_workers") != _required_worker_ids(plan):
        raise RunCtlError("worker index 与 Run/workflow plan 不一致")
    rows = {row["worker"]: row for row in index["workers"]}
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
    return index


def _worker_binding(run_dir: Path, plan: dict[str, Any], *, required: bool) -> dict[str, str] | None:
    index = _validated_worker_index(run_dir, plan, required=required)
    return _safe_run_binding(run_dir, WORKER_INDEX_RELATIVE) if index is not None else None


def _analysis_item_ids(model: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for collection, field in _ANALYSIS_ID_FIELDS.items():
        ids |= {str(item.get(field)) for item in model.get(collection, []) if isinstance(item, dict) and item.get(field)}
    return ids


def _assert_worker_contributions(run_dir: Path, plan: dict[str, Any], model: dict[str, Any]) -> None:
    index = _validated_worker_index(run_dir, plan, required=True)
    item_ids = _analysis_item_ids(model)
    applicability = {item.get("dfx"): item for item in model.get("model_applicability", []) if isinstance(item, dict)}
    for row in index["workers"]:
        receipt = read_json(run_dir / row["receipt"]["path"])
        unknown = set(receipt["contribution_ids"]) - item_ids
        if unknown:
            raise RunCtlError(f"worker {receipt['worker']} 声明的 contribution_ids 未进入 analysis-model: {sorted(unknown)}")
        if receipt["status"] == "completed" and not receipt["contribution_ids"]:
            raise RunCtlError(f"completed worker 缺少分析贡献: {receipt['worker']}")
        if receipt["status"] == "not_applicable" and applicability.get(receipt["dfx"], {}).get("applicable") is not False:
            raise RunCtlError(f"worker not_applicable 与 model_applicability 不一致: {receipt['worker']}")


def _auditor_receipt_path(run_dir: Path) -> Path:
    path = run_dir / AUDITOR_RECEIPT_RELATIVE
    if path.is_symlink() or path.resolve().parent != (run_dir / "internal").resolve():
        raise RunCtlError("auditor receipt 路径异常")
    return path.resolve()


def _audited_input_bindings(root: Path, run_dir: Path, contract: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, str]]:
    bindings = [
        _safe_run_binding(run_dir, "internal/task-contract.json"),
        _evidence_binding(root, run_dir, contract, required=True),
        _worker_binding(run_dir, plan, required=True),
        _safe_run_binding(run_dir, AUDITED_MODEL_RELATIVE),
        _safe_run_binding(run_dir, "internal/risk-ledger.json"),
    ]
    if (run_dir / ANALYSIS_MODEL_RELATIVE).is_file():
        bindings.append(_safe_run_binding(run_dir, ANALYSIS_MODEL_RELATIVE))
    if _judge_required(contract):
        bindings.append(_safe_run_binding(run_dir, COVERAGE_JUDGE_RELATIVE))
    return bindings


def stage_auditor_receipt_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if not _evidence_required(run_dir):
        raise RunCtlError("stage-auditor-receipt-v2 仅用于生命周期 Run")
    if args.producer_invocation_id == args.auditor_invocation_id:
        raise RunCtlError("producer_invocation_id 与 auditor_invocation_id 必须不同")
    plan = _load_v2_workflow_plan(run_dir)
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal/task-contract.json"))
    if not _fixed_audit_model(run_dir).is_file():
        raise RunCtlError("必须先 stage-report-v2 再创建 auditor receipt")
    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))
    payload = {"artifact_type": "auditor_receipt", "schema_version": "1.0", "run_id": args.run_id,
               "producer_invocation_id": args.producer_invocation_id,
               "auditor_invocation_id": args.auditor_invocation_id,
               "provenance_strength": "repository_declared", "identity_verified": False,
               "identity_attestation": None, "audited_inputs": _audited_input_bindings(root, run_dir, contract, plan),
               "limitations": ["仓库运行时无法认证真实客户端或子 Agent 身份；仅验证不同声明 ID 与固定输入哈希。"],
               "created_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    validate(payload, "auditor-receipt.schema.json")
    data_runtime.atomic_write_json(_auditor_receipt_path(run_dir), payload)
    print(json.dumps({"run_id": args.run_id, "auditor_receipt": AUDITOR_RECEIPT_RELATIVE,
                      "provenance_strength": "repository_declared", "identity_verified": False,
                      "binding": _safe_run_binding(run_dir, AUDITOR_RECEIPT_RELATIVE)}, ensure_ascii=False))


def _auditor_receipt_binding(root: Path, run_dir: Path, contract: dict[str, Any], plan: dict[str, Any], *, required: bool) -> dict[str, str] | None:
    if not required:
        return None
    path = _auditor_receipt_path(run_dir)
    if not path.is_file():
        raise RunCtlError("生命周期 Run 缺少 auditor-receipt.json")
    receipt = read_json(path); validate(receipt, "auditor-receipt.schema.json")
    if receipt.get("run_id") != run_dir.name or receipt.get("identity_verified") is not False:
        raise RunCtlError("auditor receipt provenance 字段无效")
    if receipt.get("producer_invocation_id") == receipt.get("auditor_invocation_id"):
        raise RunCtlError("auditor receipt 的 producer/auditor 声明 ID 相同")
    if receipt.get("audited_inputs") != _audited_input_bindings(root, run_dir, contract, plan):
        raise RunCtlError("auditor receipt 输入绑定已过期，必须重新创建")
    return _safe_run_binding(run_dir, AUDITOR_RECEIPT_RELATIVE)


'''
runctl = replace_once(runctl, marker, functions + marker, "worker/auditor functions")

# Analysis lifecycle binding.
old = '''    evidence = _validated_evidence(root, run_dir, contract, required=_evidence_required(run_dir))
    model = _validate_analysis_model(read_json(path), contract, run_dir.name, evidence)
    if evidence is not None and model.get("evidence_artifact") != _evidence_binding(root, run_dir, contract, required=True):
        raise RunCtlError("analysis-model 未精确绑定 evidence provenance")
'''
new = '''    evidence = _validated_evidence(root, run_dir, contract, required=_evidence_required(run_dir))
    plan = _load_v2_workflow_plan(run_dir)
    model = _validate_analysis_model(read_json(path), contract, run_dir.name, evidence)
    if evidence is not None and model.get("evidence_artifact") != _evidence_binding(root, run_dir, contract, required=True):
        raise RunCtlError("analysis-model 未精确绑定 evidence provenance")
    if evidence is not None:
        if model.get("worker_artifact") != _worker_binding(run_dir, plan, required=True):
            raise RunCtlError("analysis-model 未精确绑定 worker index")
        _assert_worker_contributions(run_dir, plan, model)
'''
runctl = replace_once(runctl, old, new, "analysis worker binding")

# Report contract binds workers for lifecycle Runs.
old = '''    evidence_binding = _evidence_binding(root, run_dir, canonical, required=_evidence_required(run_dir))
    if evidence_binding is not None and model.get("evidence_artifact") != evidence_binding:
        raise RunCtlError("report-model 未精确绑定 evidence provenance")
'''
new = '''    evidence_binding = _evidence_binding(root, run_dir, canonical, required=_evidence_required(run_dir))
    if evidence_binding is not None and model.get("evidence_artifact") != evidence_binding:
        raise RunCtlError("report-model 未精确绑定 evidence provenance")
    if evidence_binding is not None:
        plan = _load_v2_workflow_plan(run_dir)
        worker_binding = _worker_binding(run_dir, plan, required=True)
        if model.get("worker_artifact") != worker_binding:
            raise RunCtlError("report-model 未精确绑定 worker index")
'''
runctl = replace_once(runctl, old, new, "report worker binding")

# Stage analysis injects and validates workers.
old = '''    if evidence is not None:
        model["evidence_artifact"] = _evidence_binding(root, run_dir, contract, required=True)
    normalized = _validate_analysis_model(model, contract, args.run_id, evidence)
'''
new = '''    if evidence is not None:
        model["evidence_artifact"] = _evidence_binding(root, run_dir, contract, required=True)
        model["worker_artifact"] = _worker_binding(run_dir, plan, required=True)
    normalized = _validate_analysis_model(model, contract, args.run_id, evidence)
    if evidence is not None:
        _assert_worker_contributions(run_dir, plan, normalized)
'''
runctl = replace_once(runctl, old, new, "stage analysis workers")

# Stage report injects worker binding and invalidates auditor receipt.
old = '''    evidence_binding = _evidence_binding(root, run_dir, contract, required=_evidence_required(run_dir))
    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))
'''
new = '''    evidence_binding = _evidence_binding(root, run_dir, contract, required=_evidence_required(run_dir))
    worker_binding = _worker_binding(run_dir, plan, required=_evidence_required(run_dir))
    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))
'''
runctl = replace_once(runctl, old, new, "stage report worker load")
old = '''    if evidence_binding is not None:
        model["evidence_artifact"] = evidence_binding
    if analysis_binding is not None:
'''
new = '''    if evidence_binding is not None:
        model["evidence_artifact"] = evidence_binding
        model["worker_artifact"] = worker_binding
    if analysis_binding is not None:
'''
runctl = replace_once(runctl, old, new, "stage report worker inject")
runctl = replace_once(runctl, '    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))\n    data_runtime.atomic_write_json(target, model)\n',
                      '    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))\n    _invalidate_fixed_artifact(_auditor_receipt_path(run_dir))\n    data_runtime.atomic_write_json(target, model)\n',
                      "invalidate auditor on report")

# Coverage Judge consumes evidence and workers.
old = '''    judged = coverage_judge.judge(analysis, report, ledger)
    payload = {
        "artifact_type": "coverage_judge", "schema_version": "1.0", "run_id": run_dir.name,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "analysis_artifact": _binding(analysis_path, ANALYSIS_MODEL_RELATIVE),
        "report_artifact": _binding(report_path, AUDITED_MODEL_RELATIVE),
        "risk_ledger_artifact": _binding(ledger_path, "internal/risk-ledger.json"),
'''
new = '''    plan = _load_v2_workflow_plan(run_dir)
    workers = _validated_worker_index(run_dir, plan, required=True)
    evidence = _validated_evidence(run_dir.parents[2], run_dir, contract, required=True)
    judged = coverage_judge.judge(analysis, report, ledger, workers)
    payload = {
        "artifact_type": "coverage_judge", "schema_version": "1.0", "run_id": run_dir.name,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "analysis_artifact": _binding(analysis_path, ANALYSIS_MODEL_RELATIVE),
        "report_artifact": _binding(report_path, AUDITED_MODEL_RELATIVE),
        "risk_ledger_artifact": _binding(ledger_path, "internal/risk-ledger.json"),
        "evidence_artifact": _evidence_binding(run_dir.parents[2], run_dir, contract, required=True),
        "worker_artifact": _worker_binding(run_dir, plan, required=True),
'''
runctl = replace_once(runctl, old, new, "judge provenance")
old = '''        "risk_ledger_artifact": _binding(run_dir / "internal" / "risk-ledger.json", "internal/risk-ledger.json"),
    }
'''
new = '''        "risk_ledger_artifact": _binding(run_dir / "internal" / "risk-ledger.json", "internal/risk-ledger.json"),
        "evidence_artifact": _evidence_binding(run_dir.parents[2], run_dir, contract, required=True),
        "worker_artifact": _worker_binding(run_dir, _load_v2_workflow_plan(run_dir), required=True),
    }
'''
runctl = replace_once(runctl, old, new, "judge stale bindings")

# Apply audit and finalize require the current declared auditor receipt for lifecycle Runs.
needle = '''    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))
    _assert_report_gap_binding(report_model, snapshot_gaps)
'''
replacement = '''    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))
    _auditor_receipt_binding(root, run_dir, contract, plan, required=_evidence_required(run_dir))
    _assert_report_gap_binding(report_model, snapshot_gaps)
'''
runctl = replace_once(runctl, needle, replacement, "apply audit receipt")
needle = '''    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))
    if manifest.get("audit", {}).get("status") != "PASS":
'''
replacement = '''    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))
    _auditor_receipt_binding(root, run_dir, contract, plan, required=_evidence_required(run_dir))
    if manifest.get("audit", {}).get("status") != "PASS":
'''
runctl = replace_once(runctl, needle, replacement, "finalize auditor receipt")

# Resume includes worker/auditor status.
old = '''    try:
        evidence_artifact = _evidence_binding(root, run_dir, contract, required=False)
    except RunCtlError as exc:
        evidence_artifact = {"status": "invalid", "error": str(exc)}
'''
new = '''    try:
        evidence_artifact = _evidence_binding(root, run_dir, contract, required=False)
    except RunCtlError as exc:
        evidence_artifact = {"status": "invalid", "error": str(exc)}
    try:
        worker_artifact = _worker_binding(run_dir, plan, required=False)
    except RunCtlError as exc:
        worker_artifact = {"status": "invalid", "error": str(exc)}
    auditor_artifact = (_safe_run_binding(run_dir, AUDITOR_RECEIPT_RELATIVE)
                        if _auditor_receipt_path(run_dir).is_file() else None)
'''
runctl = replace_once(runctl, old, new, "resume provenance")
runctl = replace_once(runctl, '                      "snapshots": snapshots, "evidence_artifact": evidence_artifact}, ensure_ascii=False, indent=2))\n',
                      '                      "snapshots": snapshots, "evidence_artifact": evidence_artifact,\n                      "worker_artifact": worker_artifact, "auditor_artifact": auditor_artifact}, ensure_ascii=False, indent=2))\n',
                      "resume provenance output")

# CLI parsers.
marker = '    mrdiff2 = sub.add_parser("stage-mr-diff-v2", help="将 MR unified diff 落盘为固定 Run 工件")\n'
parsers = '''    product2 = sub.add_parser("stage-work-product-v2", help="落盘当前阶段的固定结构化工件")\n    product2.add_argument("--run-id", required=True)\n    product2.add_argument("--stage", required=True)\n    product2.add_argument("--file", required=True)\n    product2.add_argument("--root")\n    product2.set_defaults(func=stage_work_product_v2)\n    worker2 = sub.add_parser("stage-worker-receipt-v2", help="落盘 DFX worker 声明与贡献 receipt")\n    worker2.add_argument("--run-id", required=True)\n    worker2.add_argument("--file", required=True)\n    worker2.add_argument("--root")\n    worker2.set_defaults(func=stage_worker_receipt_v2)\n    auditor2 = sub.add_parser("stage-auditor-receipt-v2", help="绑定 auditor 的固定输入并记录声明 provenance")\n    auditor2.add_argument("--run-id", required=True)\n    auditor2.add_argument("--producer-invocation-id", required=True)\n    auditor2.add_argument("--auditor-invocation-id", required=True)\n    auditor2.add_argument("--root")\n    auditor2.set_defaults(func=stage_auditor_receipt_v2)\n'''
runctl = replace_once(runctl, marker, parsers + marker, "provenance parsers")
write("runtime/runctl.py", runctl)

# ---------- coverage_judge worker check ----------
judge = read("runtime/coverage_judge.py")
judge = replace_once(judge,
    '    "test_traceability", "report_projection",\n',
    '    "test_traceability", "report_projection", "worker_provenance",\n',
    "judge check list")
judge = replace_once(judge,
    'def judge(analysis: dict[str, Any], report: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:\n',
    'def judge(analysis: dict[str, Any], report: dict[str, Any], ledger: dict[str, Any], workers: dict[str, Any] | None = None) -> dict[str, Any]:\n',
    "judge signature")
insert = '''\n    if workers is None:\n        findings["worker_provenance"].append("缺少 worker index")\n    else:\n        required_workers = set(map(str, workers.get("required_workers", [])))\n        rows = {str(item.get("worker")): item for item in workers.get("workers", []) if isinstance(item, dict)}\n        for worker in sorted(required_workers - set(rows)):\n            findings["worker_provenance"].append(f"缺少 required worker receipt: {worker}")\n        if workers.get("identity_verified") is not False or workers.get("provenance_strength") != "repository_declared":\n            findings["worker_provenance"].append("worker provenance 强度声明不诚实或字段漂移")\n\n'''
marker = '    try:\n        analysis_reporting.assert_projection(report, analysis)\n'
judge = replace_once(judge, marker, insert + marker, "judge worker check")
write("runtime/coverage_judge.py", judge)

# ---------- Agent contracts ----------
primary = read(".opencode/agents/pangea-test.md")
anchor = "## 独立审计与完成门禁\n"
policy = '''## Worker、阶段工件与审计 Provenance\n\n生命周期 Run 的每个 completed 分析 checkpoint 必须先通过 `stage-work-product-v2` 落盘 `internal/stages/<stage>.json`，并在 checkpoint 的 `artifact_bindings` 中绑定该文件当前 SHA-256。修改工件后旧 checkpoint 自动失效。\n\n每个 workflow plan 路由的 DFX 子 Agent 都必须通过 `stage-worker-receipt-v2` 形成固定 receipt；完整模块固定六个。receipt 记录 assigned/searched scope、contribution IDs、risk IDs、状态和剩余范围，并绑定 task contract、evidence provenance 和源码快照。analysis-model 必须消费 completed worker 的 contribution IDs。\n\n当前仓库不能认证真实客户端或子 Agent 身份，因此 worker 与 auditor 工件固定使用 `provenance_strength: repository_declared`、`identity_verified: false`，并保留限制说明。不得把不同的声明 invocation ID 说成平台认证。报告和 Judge 完成后，调用 `stage-auditor-receipt-v2` 绑定全部审计输入；没有当前 receipt 时 `apply-audit-v2` 和 `finalize-v2` 都会失败。\n\n'''
primary = replace_once(primary, anchor, policy + anchor, "primary provenance policy")
write(".opencode/agents/pangea-test.md", primary)

module = read(".opencode/commands/module-analysis.md")
needle = "深度门禁：完成分析阶段后，先调用"
replacement = "Worker 与阶段门禁：每个阶段先执行 `stage-work-product-v2` 并把返回 binding 写入 checkpoint；六个 DFX worker 分别执行 `stage-worker-receipt-v2`，缺任一 required receipt 或 contribution 未进入 analysis-model 都不得继续。\n\n深度门禁：完成分析阶段后，先调用"
module = replace_once(module, needle, replacement, "module worker command")
module = module.replace("并使用命令实际返回的固定模型路径和 SHA-256，将固定相对路径", "随后执行 `stage-auditor-receipt-v2 --producer-invocation-id <producer声明ID> --auditor-invocation-id <auditor声明ID>`；明确显示 `identity_verified: false`。再使用命令实际返回的固定模型路径和 SHA-256，将固定相对路径", 1)
write(".opencode/commands/module-analysis.md", module)

mr = read(".opencode/commands/mr-regression.md")
needle = "审计门禁：主 Agent 先调用"
replacement = "Worker 与阶段门禁：所有被 workflow plan 路由的 DFX worker 必须先通过 `stage-worker-receipt-v2`；每个 completed checkpoint 必须绑定 `stage-work-product-v2` 返回的固定阶段工件。\n\n审计门禁：主 Agent 先调用"
mr = replace_once(mr, needle, replacement, "MR worker command")
mr = mr.replace("并使用命令实际返回的固定模型路径和 SHA-256，将固定相对路径", "随后执行 `stage-auditor-receipt-v2`，记录不同的 producer/auditor 声明 ID 并明确 `identity_verified: false`；再使用命令实际返回的固定模型路径和 SHA-256，将固定相对路径", 1)
write(".opencode/commands/mr-regression.md", mr)

for worker in DFX_WORKERS:
    path = f".opencode/agents/{worker}.md"
    text = read(path)
    text += "\n\n完成时必须输出可提交给 `stage-worker-receipt-v2` 的 worker-result：精确 worker ID、invocation_id、assigned_scope、searched_scope、contribution_ids、risk_ids、status 与 remaining_scope。invocation_id 只是声明值，不得声称已被平台认证。\n"
    write(path, text)

auditor = read(".opencode/agents/auditor.md")
auditor += "\n\n主 Agent 必须提供当前 `internal/auditor-receipt.json`。该 receipt 只证明固定输入哈希和不同的声明 invocation ID；`identity_verified: false` 表示仓库无法认证真实 Agent 身份。不得把 repository_declared 描述为平台 attested。\n"
write(".opencode/agents/auditor.md", auditor)

# ---------- Tests ----------
test = r'''from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, runctl
from tests.test_analysis_depth_contract import AnalysisDepthContractTests, DFX
from tests.test_evidence_provenance import EvidenceProvenanceTests

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"
WORKERS = {
    "dfx-function-state": ["EP-1", "FLOW-1", "BR-1", "STATE-1", "ERR-1"],
    "dfx-resource-spec": ["RES-1"],
    "dfx-performance-pressure": ["CAND-1"],
    "dfx-concurrency-exception": ["CON-1"],
    "dfx-upgrade-compatibility": ["CAND-1"],
    "dfx-reliability-consistency": ["CAND-1"],
}


class WorkerAuditProvenanceTests(unittest.TestCase):
    def cli(self, root: Path, *args: str, expected: int = 0) -> dict:
        result = subprocess.run([sys.executable, str(RUNCTL), *args, "--root", str(root)], cwd=ROOT,
                                text=True, capture_output=True, check=False)
        if result.returncode != expected:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {"stderr": result.stderr}

    def prepare(self, root: Path, contract_id: str = "workers") -> Path:
        helper = EvidenceProvenanceTests()
        run_dir = helper.activate(root, contract_id=contract_id)
        helper.stage(root, run_dir, helper.payload(root, run_dir))
        return run_dir

    def stage_workers(self, root: Path, run_dir: Path, *, omit: str | None = None, unknown: bool = False) -> None:
        for worker, contributions in WORKERS.items():
            if worker == omit:
                continue
            payload = {"worker": worker, "invocation_id": f"invocation-{worker}",
                       "assigned_scope": [f"分析 {worker} 负责的 DFX 范围"],
                       "searched_scope": ["driver.c 及相关固定源码证据"],
                       "contribution_ids": (["UNKNOWN-ID"] if unknown and worker == "dfx-resource-spec" else contributions),
                       "risk_ids": [], "status": "completed", "remaining_scope": []}
            path = root / f"{worker}.json"; path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            self.cli(root, "stage-worker-receipt-v2", "--run-id", run_dir.name, "--file", str(path))

    def stage_artifacts_and_checkpoints(self, root: Path, run_dir: Path) -> None:
        for stage in ("code_map", "flow", "branches", "dfx_scan", "specialist", "sfmea", "test_design"):
            artifact = {"artifact_type": "stage_artifact", "schema_version": "1.0", "run_id": run_dir.name,
                        "stage": stage, "summary": f"{stage} 阶段已形成可复核结构化工件",
                        "evidence_ids": ["EV-1"], "item_ids": [stage.upper()], "open_items": []}
            source = root / f"stage-{stage}.json"; source.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
            binding = self.cli(root, "stage-work-product-v2", "--run-id", run_dir.name,
                               "--stage", stage, "--file", str(source))["artifact_binding"]
            if stage == "dfx_scan":
                facts = [{"dfx": item, "conclusion": f"{item}已形成具体结论", "evidence": "EV-1"} for item in DFX]
            else:
                facts = [{"summary": f"{stage} 已形成具体分析工件", "evidence": "EV-1"}]
            data_runtime.append_checkpoint(root, run_dir.name, {"stage": stage, "status": "completed",
                "facts": facts, "artifact_bindings": [binding], "open_items": [], "next_step": "继续"})

    def lifecycle_model(self, run_dir: Path) -> dict:
        model = AnalysisDepthContractTests.model(run_dir)
        model["evidence_consumption"][0]["source_ref"] = "EV-1"
        for collection in ("entrypoints", "flows", "branches", "states", "resources", "concurrency", "error_chains"):
            for item in model[collection]: item["source_evidence"] = ["EV-1"]
        return model

    def test_lifecycle_checkpoint_requires_current_stage_binding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "checkpoint")
            with self.assertRaises(data_runtime.DataRuntimeError) as context:
                data_runtime.append_checkpoint(root, run_dir.name, {"stage": "code_map", "status": "completed",
                    "facts": [{"summary": "代码地图已经完成具体入口分析", "evidence": "EV-1"}],
                    "open_items": [], "next_step": "继续"})
            self.assertIn("artifact_bindings", str(context.exception))

    def test_missing_worker_blocks_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "missing-worker")
            self.stage_workers(root, run_dir, omit="dfx-upgrade-compatibility")
            self.stage_artifacts_and_checkpoints(root, run_dir)
            model = self.lifecycle_model(run_dir); path = root / "model.json"
            path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("dfx-upgrade-compatibility", rejected["stderr"])

    def test_unknown_worker_contribution_blocks_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "unknown-worker")
            self.stage_workers(root, run_dir, unknown=True); self.stage_artifacts_and_checkpoints(root, run_dir)
            model = self.lifecycle_model(run_dir); path = root / "model.json"
            path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli(root, "stage-analysis-v2", "--run-id", run_dir.name, "--file", str(path), expected=2)
            self.assertIn("UNKNOWN-ID", rejected["stderr"])

    def test_worker_index_explicitly_does_not_claim_identity_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "identity")
            self.stage_workers(root, run_dir)
            index = json.loads((run_dir / "internal/worker-index.json").read_text(encoding="utf-8"))
            self.assertEqual("repository_declared", index["provenance_strength"])
            self.assertFalse(index["identity_verified"])
            for row in index["workers"]:
                receipt = json.loads((run_dir / row["receipt"]["path"]).read_text(encoding="utf-8"))
                self.assertFalse(receipt["identity_verified"])
                self.assertIsNone(receipt["identity_attestation"])

    def test_auditor_receipt_rejects_same_declared_invocation_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "audit-id")
            result = self.cli(root, "stage-auditor-receipt-v2", "--run-id", run_dir.name,
                              "--producer-invocation-id", "same-invocation", "--auditor-invocation-id", "same-invocation",
                              expected=2)
            self.assertIn("必须不同", result["stderr"])


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_worker_audit_provenance.py", test)

# Patch lifecycle helper so PR21 lifecycle analysis tests use bound checkpoints.
test_depth = read("tests/test_analysis_depth_contract.py")
old = '''    @staticmethod
    def complete_checkpoints(root: Path, run_id: str) -> None:
        for stage in ("code_map", "flow", "branches"):
'''
new = '''    @staticmethod
    def complete_checkpoints(root: Path, run_id: str) -> None:
        run_dir = data_runtime.ensure_layout(root) / "runs" / run_id
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        lifecycle = manifest.get("contract_record_file") == "internal/contract-record.json"
        def bindings(stage: str) -> list[dict[str, str]]:
            if not lifecycle:
                return []
            path = run_dir / "internal" / "stages" / f"{stage}.json"; path.parent.mkdir(parents=True, exist_ok=True)
            data_runtime.atomic_write_json(path, {"artifact_type": "stage_artifact", "schema_version": "1.0",
                "run_id": run_id, "stage": stage, "summary": f"{stage} 阶段已形成可复核结构化工件",
                "evidence_ids": ["EV-1"], "item_ids": [stage.upper()], "open_items": []})
            return [{"path": f"internal/stages/{stage}.json", "sha256": data_runtime.sha256_file(path)}]
        for stage in ("code_map", "flow", "branches"):
'''
test_depth = replace_once(test_depth, old, new, "checkpoint helper setup")
test_depth = test_depth.replace('"open_items": [], "next_step": "继续"})', '"artifact_bindings": bindings(stage), "open_items": [], "next_step": "继续"})', 3)
# dfx_scan line is different; patch explicitly.
test_depth = replace_once(test_depth,
    '            "open_items": [], "next_step": "继续"})\n        for stage in ("specialist", "sfmea", "test_design"):',
    '            "artifact_bindings": bindings("dfx_scan"), "open_items": [], "next_step": "继续"})\n        for stage in ("specialist", "sfmea", "test_design"):',
    "dfx checkpoint binding")
write("tests/test_analysis_depth_contract.py", test_depth)

# Agent structural test.
agent_test = read("tests/test_agent_v2.py")
marker = '\n    def test_primary_can_dispatch_only_internal_capabilities(self) -> None:\n'
insert = '''\n    def test_worker_checkpoint_and_auditor_provenance_are_hard_gates(self) -> None:\n        combined = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")\n        combined += "\\n" + (COMMANDS / "module-analysis.md").read_text(encoding="utf-8")\n        for term in ("stage-work-product-v2", "artifact_bindings", "stage-worker-receipt-v2",\n                     "stage-auditor-receipt-v2", "repository_declared", "identity_verified: false"):\n            self.assertIn(term, combined)\n\n'''
agent_test = replace_once(agent_test, marker, insert + marker, "agent provenance test")
write("tests/test_agent_v2.py", agent_test)
