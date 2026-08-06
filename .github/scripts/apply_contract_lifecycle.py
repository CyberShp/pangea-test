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


preflight_schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PANGEA Preflight Receipt",
    "type": "object",
    "additionalProperties": True,
    "required": [
        "artifact_type", "schema_version", "created_at", "status", "project_root",
        "data_root", "repository_root", "known_repositories", "allowed_next_actions",
        "python_executable", "step_results", "step_errors",
    ],
    "properties": {
        "artifact_type": {"const": "preflight_receipt"},
        "schema_version": {"const": "1.0"},
        "created_at": {"type": "string", "minLength": 1},
        "status": {"enum": ["ready", "degraded"]},
        "project_root": {"type": "string", "minLength": 1},
        "data_root": {"type": "string", "minLength": 1},
        "repository_root": {"type": "string", "minLength": 1},
        "known_repositories": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "allowed_next_actions": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "python_executable": {"type": "string", "minLength": 1},
        "step_results": {"type": "object"},
        "step_errors": {"type": "object"},
    },
}
write("schemas/preflight-receipt.schema.json", json.dumps(preflight_schema, ensure_ascii=False, indent=2) + "\n")

contract_schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PANGEA Task Contract Record",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "artifact_type", "schema_version", "contract_id", "status", "confirmation_required",
        "confirmation_policy", "task_contract", "preflight", "created_at", "updated_at",
        "confirmation", "activation",
    ],
    "properties": {
        "artifact_type": {"const": "task_contract_record"},
        "schema_version": {"const": "1.0"},
        "contract_id": {"type": "string", "minLength": 1},
        "status": {"enum": ["draft", "confirmed", "activated", "cancelled"]},
        "confirmation_required": {"type": "boolean"},
        "confirmation_policy": {"enum": ["user_required", "auto_unambiguous"]},
        "task_contract": {"type": "object"},
        "preflight": {
            "type": "object", "additionalProperties": False,
            "required": ["path", "sha256", "created_at"],
            "properties": {
                "path": {"const": "session/preflight-receipt.json"},
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "created_at": {"type": "string", "minLength": 1},
            },
        },
        "created_at": {"type": "string", "minLength": 1},
        "updated_at": {"type": "string", "minLength": 1},
        "confirmation": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["source", "materials_status", "note", "confirmed_at"],
            "properties": {
                "source": {"enum": ["user_reply", "user_explicit_bypass", "auto_unambiguous"]},
                "materials_status": {"enum": ["provided", "confirmed_none", "unchanged"]},
                "note": {"type": ["string", "null"]},
                "confirmed_at": {"type": "string", "minLength": 1},
            },
        },
        "activation": {
            "type": ["object", "null"],
            "additionalProperties": False,
            "required": ["run_id", "activated_at"],
            "properties": {
                "run_id": {"type": "string", "minLength": 1},
                "activated_at": {"type": "string", "minLength": 1},
            },
        },
    },
}
write("schemas/contract-record.schema.json", json.dumps(contract_schema, ensure_ascii=False, indent=2) + "\n")

# Optional durable contract/session directories remain lazy.
data = read("runtime/data_runtime.py")
data = replace_once(
    data,
    'OPTIONAL_LAYOUT = ("library", "indexes", "reports", "tmp")\n',
    'OPTIONAL_LAYOUT = ("library", "indexes", "reports", "tmp", "contracts", "session")\n',
    "optional layout",
)
# Expose contract inventory without creating the optional directory.
needle = '''    return {
        "locations": {
            "documents_inbox": str(workspace / "inbox"),
            "document_library": str(workspace / "library"),
            "repositories": str(workspace / "repositories"),
            "indexes": str(workspace / "indexes"),
            "run_history": str(workspace / "runs"),
            "formal_reports": str(workspace / "reports"),
        },
        "formal_reports": formal_reports,
        "run_history": run_history,
        "legacy_reports": legacy_reports,
    }
'''
replacement = '''    contracts_root = workspace / "contracts"
    contracts: list[dict[str, Any]] = []
    if contracts_root.exists() or contracts_root.is_symlink():
        contracts_resolved = _require_managed_directory(contracts_root, workspace_resolved, "contracts 目录")
        for contract_dir in sorted(contracts_root.iterdir(), key=lambda item: item.name):
            if contract_dir.is_symlink() or not contract_dir.is_dir():
                raise DataRuntimeError(f"拒绝非目录任务契约项: {contract_dir}")
            resolved = _require_managed_directory(contract_dir, contracts_resolved, "任务契约目录")
            record_path = contract_dir / "contract.json"
            _require_regular_file(record_path, resolved, "任务契约记录")
            record = read_json(record_path)
            if not isinstance(record, dict):
                raise DataRuntimeError(f"任务契约记录无效: {contract_dir.name}")
            contracts.append({"contract_id": record.get("contract_id", contract_dir.name),
                              "status": record.get("status", "unknown"),
                              "target": record.get("task_contract", {}).get("target"),
                              "record": str(record_path), "activation": record.get("activation")})

    return {
        "locations": {
            "documents_inbox": str(workspace / "inbox"),
            "document_library": str(workspace / "library"),
            "repositories": str(workspace / "repositories"),
            "indexes": str(workspace / "indexes"),
            "run_history": str(workspace / "runs"),
            "formal_reports": str(workspace / "reports"),
            "task_contracts": str(workspace / "contracts"),
        },
        "formal_reports": formal_reports,
        "run_history": run_history,
        "legacy_reports": legacy_reports,
        "task_contracts": contracts,
    }
'''
data = replace_once(data, needle, replacement, "workspace contract inventory")
write("runtime/data_runtime.py", data)

# Persist a preflight receipt on every resolved preflight.
preflight = read("tooling/pangea_cli/preflightctl.py")
preflight = preflight.replace("from runtime import workspace_runtime\n", "from runtime import data_runtime, workspace_runtime\n")
old = '''    result = workspace_runtime.run_preflight(
        explicit_root=args.root,
        start=Path(args.start) if args.start else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
'''
new = '''    result = workspace_runtime.run_preflight(
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
'''
preflight = replace_once(preflight, old, new, "preflight receipt")
write("tooling/pangea_cli/preflightctl.py", preflight)

runctl = read("runtime/runctl.py")
# Constants.
runctl = replace_once(
    runctl,
    'AUDITED_MODEL_RELATIVE = "internal/report-model.json"\n',
    'AUDITED_MODEL_RELATIVE = "internal/report-model.json"\n'
    'PREFLIGHT_RECEIPT_RELATIVE = "session/preflight-receipt.json"\n'
    'CONTRACT_RECORD_RELATIVE = "internal/contract-record.json"\n'
    'CONTRACT_CONFIRMATION_RELATIVE = "internal/contract-confirmation.json"\n'
    'PREFLIGHT_MAX_AGE_HOURS = 24\n',
    "lifecycle constants",
)

helpers = r'''

def _marked_project_root(root: Path) -> bool:
    from runtime import workspace_runtime
    return not workspace_runtime._marker_missing(root.resolve())


def _contract_storage(root: Path) -> Path:
    from runtime import data_runtime
    workspace = data_runtime.ensure_layout(root)
    return data_runtime._ensure_managed_directory(
        workspace / "contracts", workspace.resolve(strict=True), "contracts 目录"
    )


def _contract_record_path(root: Path, contract_id: str, *, create_dir: bool = False) -> Path:
    if not contract_id or Path(contract_id).name != contract_id or contract_id in {".", ".."}:
        raise RunCtlError("contract_id 非法")
    contracts = _contract_storage(root)
    directory = contracts / contract_id
    if create_dir:
        if directory.exists() or directory.is_symlink():
            raise RunCtlError(f"任务契约已存在: {contract_id}")
        directory.mkdir()
    elif directory.is_symlink() or not directory.is_dir():
        raise RunCtlError(f"任务契约不存在: {contract_id}")
    return directory / "contract.json"


def _load_contract_record(root: Path, contract_id: str) -> tuple[Path, dict[str, Any]]:
    path = _contract_record_path(root, contract_id)
    record = read_json(path)
    validate(record, "contract-record.schema.json")
    if record.get("contract_id") != contract_id:
        raise RunCtlError("任务契约 contract_id 与路径不一致")
    _assert_formal_task_contract(record.get("task_contract"))
    return path, record


def _preflight_binding(root: Path, repositories: list[str]) -> dict[str, str]:
    from runtime import data_runtime
    workspace = data_runtime.ensure_layout(root)
    path = workspace / PREFLIGHT_RECEIPT_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise RunCtlError("缺少 portable preflight receipt；请先执行 /initial 或 preflight")
    receipt = read_json(path)
    validate(receipt, "preflight-receipt.schema.json")
    if Path(receipt["project_root"]).resolve() != root.resolve():
        raise RunCtlError("preflight receipt 绑定的 project_root 与当前 root 不一致")
    if Path(receipt["repository_root"]).resolve() != (workspace / "repositories").resolve():
        raise RunCtlError("preflight receipt 的 repository_root 与当前工作区不一致")
    if "draft_contract" not in receipt.get("allowed_next_actions", []):
        raise RunCtlError("preflight 未允许进入任务契约阶段")
    missing = sorted(set(repositories) - set(receipt.get("known_repositories", [])))
    if missing:
        raise RunCtlError("preflight 未识别任务仓库: " + ", ".join(missing))
    try:
        created = datetime.fromisoformat(receipt["created_at"])
        now = datetime.now(created.tzinfo) if created.tzinfo else datetime.now()
    except (TypeError, ValueError) as exc:
        raise RunCtlError("preflight receipt created_at 无效") from exc
    if (now - created).total_seconds() > PREFLIGHT_MAX_AGE_HOURS * 3600:
        raise RunCtlError("preflight receipt 已过期，请重新执行 /initial")
    return {"path": PREFLIGHT_RECEIPT_RELATIVE, "sha256": _sha256_file(path),
            "created_at": receipt["created_at"]}


def _contract_from_args(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = load_scenario(args.scenario)
    mode = scenario["contract_mode"]
    depth = args.analysis_depth or scenario["default_depth"]
    if mode == "mr_regression" and depth != "focused":
        raise RunCtlError("MR 回归仅支持 focused 深度")
    if mode == "mr_regression" and not args.mr_url:
        raise RunCtlError("MR 回归必须提供 --mr-url")
    if mode == "module_analysis" and depth not in {"complete", "fast"}:
        raise RunCtlError("模块分析仅支持 complete 或 fast 深度")
    requested = args.repository or []
    if not requested:
        raise RunCtlError("至少提供一个 --repository")
    repositories = _registered_repositories(root, requested)
    repository_commits = _repository_commits(root, args.repository_commit or [], repositories, mode)
    contract = {
        "schema_version": "1.0", "mode": mode, "goal": args.goal or scenario["display_name"],
        "target": args.target, "repositories": repositories, "analysis_depth": depth,
        "mr_url": args.mr_url if mode == "mr_regression" else None,
        "version": args.version, "topology": args.topology,
        "test_focus": args.test_focus or [], "input_refs": args.input_ref or [],
        "excluded_scope": args.exclude or [], "tool_gaps": args.tool_gap or [],
        "known_gaps": args.known_gap or [], "created_by": args.created_by,
        "signals": args.signal or [], "resource_emphasis": bool(args.resource_emphasis),
    }
    if repository_commits is not None:
        contract["repository_commits"] = repository_commits
    _assert_formal_task_contract(contract)
    return scenario, contract


def _assert_run_contract_lifecycle(run_dir: Path) -> dict[str, Any] | None:
    from runtime import data_runtime
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    record_file = manifest.get("contract_record_file") if isinstance(manifest, dict) else None
    confirmation_file = manifest.get("contract_confirmation_file") if isinstance(manifest, dict) else None
    if record_file is None and confirmation_file is None:
        return None
    if record_file != CONTRACT_RECORD_RELATIVE or confirmation_file != CONTRACT_CONFIRMATION_RELATIVE:
        raise RunCtlError("Run 任务契约生命周期文件路径无效")
    record = read_json(run_dir / record_file)
    validate(record, "contract-record.schema.json")
    if record.get("status") != "activated" or record.get("activation", {}).get("run_id") != run_dir.name:
        raise RunCtlError("Run 未绑定已激活任务契约")
    confirmation = read_json(run_dir / confirmation_file)
    if confirmation != record.get("confirmation") or not isinstance(confirmation, dict):
        raise RunCtlError("Run 任务契约确认记录缺失或不一致")
    canonical = data_runtime.read_json(run_dir / "internal" / "task-contract.json")
    if record.get("task_contract") != canonical:
        raise RunCtlError("已激活任务契约与 Run canonical task contract 不一致")
    return record


def draft_contract_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, workspace_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    workspace_runtime.validate_project_root(root)
    scenario, contract = _contract_from_args(args, root)
    binding = _preflight_binding(root, contract["repositories"])
    contract_id = args.contract_id or f"{args.scenario}-{slug(args.target)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = _contract_record_path(root, contract_id, create_dir=True)
    required = contract["mode"] == "module_analysis" and contract["analysis_depth"] == "complete"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        "artifact_type": "task_contract_record", "schema_version": "1.0",
        "contract_id": contract_id, "status": "draft", "confirmation_required": required,
        "confirmation_policy": "user_required" if required else "auto_unambiguous",
        "task_contract": contract, "preflight": binding, "created_at": now, "updated_at": now,
        "confirmation": None, "activation": None,
    }
    validate(record, "contract-record.schema.json")
    data_runtime.atomic_write_json(path, record)
    print(json.dumps({"contract_id": contract_id, "status": "draft", "record": str(path),
                      "task_contract": contract, "confirmation_required": required,
                      "required_user_action": "请确认分析范围并说明是否还有补充材料" if required else None,
                      "next_step": "confirm-contract-v2"}, ensure_ascii=False))


def confirm_contract_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, workspace_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    workspace_runtime.validate_project_root(root)
    path, record = _load_contract_record(root, args.contract_id)
    if record["status"] != "draft":
        raise RunCtlError("只有 draft 任务契约可以确认")
    if record["confirmation_required"] and args.source not in {"user_reply", "user_explicit_bypass"}:
        raise RunCtlError("完整型模块分析必须由用户回复或用户明确免确认，禁止自动确认")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    confirmation = {"source": args.source, "materials_status": args.materials_status,
                    "note": args.note, "confirmed_at": now}
    record.update({"status": "confirmed", "confirmation": confirmation, "updated_at": now})
    validate(record, "contract-record.schema.json")
    data_runtime.atomic_write_json(path, record)
    print(json.dumps({"contract_id": args.contract_id, "status": "confirmed",
                      "confirmation": confirmation, "next_step": "activate-contract-v2"}, ensure_ascii=False))


def activate_contract_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, workspace_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    workspace_runtime.validate_project_root(root)
    path, record = _load_contract_record(root, args.contract_id)
    if record["status"] != "confirmed" or not isinstance(record.get("confirmation"), dict):
        raise RunCtlError("任务契约尚未确认，禁止创建 Run 或源码快照")
    current_binding = _preflight_binding(root, record["task_contract"]["repositories"])
    if current_binding != record["preflight"]:
        raise RunCtlError("preflight receipt 在契约确认前后发生变化，请重新生成任务契约")
    contract = record["task_contract"]
    scenario_name = "mr-regression" if contract["mode"] == "mr_regression" else "module-analysis"
    namespace = argparse.Namespace(
        root=str(root), scenario=scenario_name, target=contract["target"], repository=contract["repositories"],
        repository_commit=[f"{name}={value}" for name, value in contract.get("repository_commits", {}).items()],
        run_id=args.run_id, mr_url=contract.get("mr_url"), goal=contract.get("goal"),
        analysis_depth=contract.get("analysis_depth"), version=contract.get("version"), topology=contract.get("topology"),
        test_focus=contract.get("test_focus"), input_ref=contract.get("input_refs"), exclude=contract.get("excluded_scope"),
        tool_gap=contract.get("tool_gaps"), known_gap=contract.get("known_gaps"), signal=contract.get("signals"),
        resource_emphasis=contract.get("resource_emphasis", False), created_by=contract.get("created_by"),
        max_audit_rounds=args.max_audit_rounds, _canonical_contract=contract, _return_payload=True,
    )
    payload = create_v2_run(namespace)
    run_dir = Path(payload["run_dir"])
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    record.update({"status": "activated", "updated_at": now,
                   "activation": {"run_id": payload["run_id"], "activated_at": now}})
    validate(record, "contract-record.schema.json")
    data_runtime.atomic_write_json(path, record)
    data_runtime.atomic_write_json(run_dir / CONTRACT_RECORD_RELATIVE, record)
    data_runtime.atomic_write_json(run_dir / CONTRACT_CONFIRMATION_RELATIVE, record["confirmation"])
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest["contract_record_file"] = CONTRACT_RECORD_RELATIVE
    manifest["contract_confirmation_file"] = CONTRACT_CONFIRMATION_RELATIVE
    validate(manifest, "session-manifest.schema.json")
    data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    print(json.dumps({**payload, "contract_id": args.contract_id, "contract_status": "activated",
                      "contract_record": str(run_dir / CONTRACT_RECORD_RELATIVE)}, ensure_ascii=False))
'''
runctl = replace_once(
    runctl,
    '\ndef _assert_report_contract_and_sections(run_dir: Path, model: Any) -> dict[str, Any]:\n',
    helpers + '\n\ndef _assert_report_contract_and_sections(run_dir: Path, model: Any) -> dict[str, Any]:\n',
    "contract lifecycle helpers",
)

# Every activated Run verifies lifecycle while legacy historical/test roots remain readable.
runctl = replace_once(
    runctl,
    '    contract = data_runtime.read_json(run_dir / "internal" / "task-contract.json")\n    plan = data_runtime.read_json(run_dir / "internal" / "workflow-plan.json", {})\n',
    '    contract = data_runtime.read_json(run_dir / "internal" / "task-contract.json")\n'
    '    _assert_run_contract_lifecycle(run_dir)\n'
    '    plan = data_runtime.read_json(run_dir / "internal" / "workflow-plan.json", {})\n',
    "lifecycle plan gate",
)

# Replace create_v2_run implementation with canonical-contract support and marked-root gate.
start = runctl.index("def create_v2_run(args: argparse.Namespace) -> None:\n")
end = runctl.index("\n\ndef _specialist_skip_permitted", start)
new_create = r'''def create_v2_run(args: argparse.Namespace) -> dict[str, Any] | None:
    """Create a v2 Run; marked project roots require an activated contract."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    canonical = getattr(args, "_canonical_contract", None)
    if canonical is None and _marked_project_root(root):
        raise RunCtlError(
            "正式项目根禁止直接 create-v2；请依次使用 draft-contract-v2、confirm-contract-v2、activate-contract-v2"
        )
    if canonical is None:
        scenario, contract = _contract_from_args(args, root)
    else:
        contract = _assert_formal_task_contract(canonical)
        scenario_name = "mr-regression" if contract["mode"] == "mr_regression" else "module-analysis"
        scenario = load_scenario(scenario_name)
        registered = _registered_repositories(root, contract["repositories"])
        if registered != contract["repositories"]:
            raise RunCtlError("激活时仓库登记集合与任务契约不一致")
        expected = contract.get("repository_commits")
        if not isinstance(expected, dict) or set(expected) != set(registered):
            raise RunCtlError("激活任务契约缺少完整 repository_commits")
    mode = contract["mode"]
    repositories = contract["repositories"]
    repository_commits = contract.get("repository_commits")
    run_id = args.run_id or f"{scenario_name if canonical is not None else args.scenario}-{slug(contract['target'])}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    created = data_runtime.create_run(root, run_id, contract, args.max_audit_rounds)
    plan = v2_plan(contract)
    run_dir = Path(created["run_dir"])
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest["audit"]["rework"] = None
    validate(manifest, "session-manifest.schema.json")
    data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    atomic_write(run_dir / "internal" / "workflow-plan.json", plan)
    source_snapshots: dict[str, Any] | None = None
    state_message = "已建立任务契约，准备共享代码地图"
    if mode == "module_analysis":
        from runtime import repository_runtime
        specs = [{"repository": repository, "ref": repository_commits[repository], "snapshot_id": repository}
                 for repository in repositories]
        source_snapshots = repository_runtime.create_snapshots(root, run_id, specs)
        atomic_write(run_dir / "internal" / "source-snapshots.json", source_snapshots)
        state_message = ("已绑定仓库 commit；部分只读快照不可用，按覆盖缺口继续"
                         if source_snapshots["coverage_gaps"] else
                         "已绑定仓库 commit 并创建只读源码快照，准备共享代码地图")
    data_runtime.set_run_state(root, run_id, "mapping", state_message)
    payload = {"run_id": run_id, "run_dir": str(run_dir), "contract": contract,
               "plan": plan, "source_snapshots": source_snapshots,
               "validation_backend": validate(contract, "task-contract.schema.json")}
    if getattr(args, "_return_payload", False):
        return payload
    print(json.dumps(payload, ensure_ascii=False))
    return None
'''
runctl = runctl[:start] + new_create + runctl[end:]

# Parser commands.
parser_anchor = '    resume2 = sub.add_parser("resume-v2", help="读取 pangea-data Run 的续跑计划")\n'
contract_parsers = '''    draft2 = sub.add_parser("draft-contract-v2", help="生成待确认的正式任务契约")
    draft2.add_argument("--scenario", choices=["mr-regression", "module-analysis"], required=True)
    draft2.add_argument("--target", required=True)
    draft2.add_argument("--repository", action="append", required=True)
    draft2.add_argument("--repository-commit", action="append")
    draft2.add_argument("--contract-id")
    draft2.add_argument("--root")
    draft2.add_argument("--mr-url")
    draft2.add_argument("--goal")
    draft2.add_argument("--analysis-depth")
    draft2.add_argument("--version")
    draft2.add_argument("--topology")
    draft2.add_argument("--test-focus", action="append")
    draft2.add_argument("--input-ref", action="append")
    draft2.add_argument("--exclude", action="append")
    draft2.add_argument("--tool-gap", action="append")
    draft2.add_argument("--known-gap", action="append")
    draft2.add_argument("--signal", action="append")
    draft2.add_argument("--resource-emphasis", action="store_true")
    draft2.add_argument("--created-by", default="pangea-test")
    draft2.set_defaults(func=draft_contract_v2)
    confirm2 = sub.add_parser("confirm-contract-v2", help="持久化任务契约确认")
    confirm2.add_argument("--contract-id", required=True)
    confirm2.add_argument("--source", required=True,
                          choices=["user_reply", "user_explicit_bypass", "auto_unambiguous"])
    confirm2.add_argument("--materials-status", required=True,
                          choices=["provided", "confirmed_none", "unchanged"])
    confirm2.add_argument("--note")
    confirm2.add_argument("--root")
    confirm2.set_defaults(func=confirm_contract_v2)
    activate2 = sub.add_parser("activate-contract-v2", help="从已确认契约创建 Run 与只读快照")
    activate2.add_argument("--contract-id", required=True)
    activate2.add_argument("--run-id")
    activate2.add_argument("--root")
    activate2.add_argument("--max-audit-rounds", type=int, default=2)
    activate2.set_defaults(func=activate_contract_v2)
'''
runctl = replace_once(runctl, parser_anchor, contract_parsers + parser_anchor, "contract parsers")
write("runtime/runctl.py", runctl)

# Manifest allows lifecycle bindings.
manifest_path = ROOT / "schemas/session-manifest.schema.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["properties"]["contract_record_file"] = {"type": ["string", "null"]}
manifest["properties"]["contract_confirmation_file"] = {"type": ["string", "null"]}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Commands use lifecycle, never direct create-v2.
module = read(".opencode/commands/module-analysis.md")
old_start = "确认任务契约后，使用真实入口创建 Run：`<preflight.python_executable> runtime/runctl.py create-v2"
if old_start not in module:
    raise RuntimeError("module create-v2 paragraph missing")
paragraph_start = module.index(old_start)
paragraph_end = module.index("\n\n深度门禁：", paragraph_start)
module_lifecycle = '''先生成任务契约草稿：

```text
<preflight.python_executable> runtime/runctl.py draft-contract-v2 --scenario module-analysis --target <模块> --repository <已登记仓名> --analysis-depth <complete|fast>
```

必须把命令返回的完整任务契约矩阵展示给用户，包含目标、仓库与 commit、输入材料、排除范围、深度和已知缺口。`complete` 必须询问“是否有其他材料需要补充？”并等待用户回复；用户已在同一请求中明确要求按当前资料直接开始时，可记录 `user_explicit_bypass`，但仍须展示契约。确认后执行：

```text
<preflight.python_executable> runtime/runctl.py confirm-contract-v2 --contract-id <ID> --source <user_reply|user_explicit_bypass> --materials-status <provided|confirmed_none|unchanged>
<preflight.python_executable> runtime/runctl.py activate-contract-v2 --contract-id <ID> --run-id <Run-ID>
```

`fast` 在任务无歧义时可在展示契约后使用 `auto_unambiguous` 确认。禁止直接调用 `create-v2`；未确认契约时不得创建 Run、快照、checkpoint 或调用任何代码/DFX 子 Agent。'''
module = module[:paragraph_start] + module_lifecycle + module[paragraph_end:]
write(".opencode/commands/module-analysis.md", module)

mr = read(".opencode/commands/mr-regression.md")
old_start = "确认任务契约后，使用真实入口创建 Run：`<preflight.python_executable> runtime/runctl.py create-v2"
paragraph_start = mr.index(old_start)
paragraph_end = mr.index("\n\n审计门禁：", paragraph_start)
mr_lifecycle = '''先生成并展示任务契约草稿：

```text
<preflight.python_executable> runtime/runctl.py draft-contract-v2 --scenario mr-regression --target <模块> --repository <仓名> --repository-commit <仓名>=<40位SHA> --mr-url <MR> --analysis-depth focused
```

若 MR、commit、仓库和目标范围无歧义，可在展示契约后使用 `auto_unambiguous` 确认；存在原问题背景、关联仓、版本或范围歧义时必须等待用户确认：

```text
<preflight.python_executable> runtime/runctl.py confirm-contract-v2 --contract-id <ID> --source <auto_unambiguous|user_reply> --materials-status <provided|confirmed_none|unchanged>
<preflight.python_executable> runtime/runctl.py activate-contract-v2 --contract-id <ID> --run-id <Run-ID>
```

禁止直接调用 `create-v2`。未激活任务契约前不得开始 MR 影响链分析或创建快照。'''
mr = mr[:paragraph_start] + mr_lifecycle + mr[paragraph_end:]
write(".opencode/commands/mr-regression.md", mr)

resume = read(".opencode/commands/resume-run.md")
resume += '''\n\n恢复 Run 时必须读取 manifest 中的 `contract_record_file` 和 `contract_confirmation_file`。存在生命周期文件时，两者必须有效且契约状态为 activated；缺失确认不得继续。历史 Run 未包含这两个字段时按 legacy 只读兼容，不反向伪造确认记录。\n'''
write(".opencode/commands/resume-run.md", resume)

initial = read(".opencode/commands/initial.md")
initial += '''\n\npreflight 成功或可继续的降级状态必须实际写入 `pangea-data/session/preflight-receipt.json`。后续任务契约只能绑定该文件的真实 SHA-256；receipt 不存在、过期、根目录不一致或未识别目标仓库时不得生成任务契约。`workspace_inventory.task_contracts` 用于展示 draft、confirmed 和 activated 契约。\n'''
write(".opencode/commands/initial.md", initial)

agent = read(".opencode/agents/pangea-test.md")
needle = "对 `/mr-regression` 和 `/module-analysis`，先生成简短任务契约，写清：模式、目标模块、仓库与版本、MR 或范围、组网、测试重点、输入材料、排除范围、分析深度和已知缺口。信息足够就直接开始；只有关键歧义、输入冲突或无法访问必要仓库时才提问。\n"
replacement = '''对 `/mr-regression` 和 `/module-analysis`，任务契约是运行时状态机而不是聊天格式。必须依次执行 `draft-contract-v2`、展示 canonical 契约、`confirm-contract-v2`、`activate-contract-v2`；禁止直接调用 `create-v2`。契约写清模式、目标模块、仓库与 commit、MR 或范围、组网、测试重点、输入材料、排除范围、分析深度和已知缺口。

完整型模块分析固定 `confirmation_required: true`：必须询问用户是否还有补充材料并等待回复；只有用户在当前请求中已明确要求“按当前资料直接开始/无需再次确认”时，才可使用 `user_explicit_bypass`，但仍须展示契约。MR 和 fast 在信息无歧义时可展示后使用 `auto_unambiguous`。任务契约未 activated 时，不得读取源码开展业务分析、调用 MR/代码/DFX 子 Agent、创建快照或写 checkpoint。
'''
agent = replace_once(agent, needle, replacement, "primary contract policy")
write(".opencode/agents/pangea-test.md", agent)

readme = read("README.md")
anchor = "## MR 回归测试建议\n"
contract_docs = '''## 任务契约确认门禁

正式任务不再直接创建 Run。portable preflight 会写入固定 receipt；随后任务按以下状态推进：

```text
draft-contract-v2 -> confirmed-contract-v2 -> activate-contract-v2 -> Run/只读快照
```

完整型模块分析必须先展示契约并确认是否还有补充材料。MR 和 fast 在信息完整无歧义时可以展示后自动确认。项目根上的直接 `create-v2` 会被拒绝；历史 Run 不会被重写。

'''
readme = replace_once(readme, anchor, contract_docs + anchor, "README contract lifecycle")
write("README.md", readme)

# Tests.
test = r'''from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, runctl

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"


class ContractLifecycleTests(unittest.TestCase):
    @staticmethod
    def marked_root(root: Path) -> None:
        (root / ".opencode").mkdir(parents=True)
        (root / "runtime").mkdir(); (root / "runtime/runctl.py").write_text("# marker\n")
        (root / "tooling/pangea_cli").mkdir(parents=True)
        (root / "tooling/pangea_cli/__main__.py").write_text("# marker\n")
        (root / "registry").mkdir(); (root / "registry/scenarios.json").write_text("{}\n")

    @staticmethod
    def repository(root: Path) -> None:
        repo = data_runtime.ensure_layout(root) / "repositories/driver"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        (repo / "driver.c").write_text("int entry(void) { return 0; }\n")
        subprocess.run(["git", "-C", str(repo), "add", "driver.c"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid",
                        "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "initial"], check=True)

    @staticmethod
    def receipt(root: Path) -> None:
        workspace = data_runtime.ensure_layout(root)
        session = data_runtime._ensure_managed_directory(workspace / "session", workspace.resolve(), "session")
        payload = {
            "artifact_type": "preflight_receipt", "schema_version": "1.0",
            "created_at": data_runtime.utc_now(), "status": "ready",
            "project_root": str(root.resolve()), "data_root": str(workspace),
            "repository_root": str(workspace / "repositories"), "known_repositories": ["driver"],
            "allowed_next_actions": ["draft_contract"], "python_executable": sys.executable,
            "step_results": {}, "step_errors": {},
        }
        data_runtime.atomic_write_json(session / "preflight-receipt.json", payload)

    @staticmethod
    def cli(root: Path, *args: str, expected: int = 0) -> dict:
        result = subprocess.run([sys.executable, str(RUNCTL), *args, "--root", str(root)],
                                cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != expected:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {"stderr": result.stderr}

    def prepare(self, root: Path) -> None:
        self.marked_root(root); self.repository(root); self.receipt(root)

    def test_complete_contract_requires_user_confirmation_before_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                             "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "chap-contract")
            self.assertTrue(draft["confirmation_required"])
            self.assertFalse((root / "pangea-data/runs/chap-run").exists())
            rejected = self.cli(root, "confirm-contract-v2", "--contract-id", "chap-contract",
                                "--source", "auto_unambiguous", "--materials-status", "confirmed_none", expected=2)
            self.assertIn("禁止自动确认", rejected["stderr"])
            self.cli(root, "confirm-contract-v2", "--contract-id", "chap-contract",
                     "--source", "user_reply", "--materials-status", "confirmed_none")
            activated = self.cli(root, "activate-contract-v2", "--contract-id", "chap-contract", "--run-id", "chap-run")
            run_dir = Path(activated["run_dir"])
            self.assertTrue((run_dir / "internal/contract-record.json").is_file())
            self.assertTrue((run_dir / "internal/contract-confirmation.json").is_file())
            record = json.loads((run_dir / "internal/contract-record.json").read_text())
            self.assertEqual("activated", record["status"])
            self.assertEqual("chap-run", record["activation"]["run_id"])

    def test_direct_create_is_rejected_on_marked_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            rejected = self.cli(root, "create-v2", "--scenario", "module-analysis", "--target", "chap",
                                "--repository", "driver", "--analysis-depth", "complete", expected=2)
            self.assertIn("禁止直接 create-v2", rejected["stderr"])
            self.assertEqual([], list((root / "pangea-data/runs").iterdir()))

    def test_fast_contract_may_use_auto_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                             "--repository", "driver", "--analysis-depth", "fast", "--contract-id", "fast-contract")
            self.assertFalse(draft["confirmation_required"])
            self.cli(root, "confirm-contract-v2", "--contract-id", "fast-contract",
                     "--source", "auto_unambiguous", "--materials-status", "unchanged")
            activated = self.cli(root, "activate-contract-v2", "--contract-id", "fast-contract", "--run-id", "fast-run")
            self.assertEqual("activated", activated["contract_status"])

    def test_changed_preflight_receipt_invalidates_confirmed_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                     "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "changed")
            self.cli(root, "confirm-contract-v2", "--contract-id", "changed",
                     "--source", "user_reply", "--materials-status", "confirmed_none")
            receipt = root / "pangea-data/session/preflight-receipt.json"
            payload = json.loads(receipt.read_text()); payload["step_errors"] = {"index": {"message": "changed"}}
            data_runtime.atomic_write_json(receipt, payload)
            rejected = self.cli(root, "activate-contract-v2", "--contract-id", "changed", "--run-id", "bad", expected=2)
            self.assertIn("发生变化", rejected["stderr"])


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_contract_lifecycle.py", test)

# Structural tests ensure formal Agent paths cannot bypass lifecycle.
agent_test = read("tests/test_agent_v2.py")
marker = '\n    def test_primary_can_dispatch_only_internal_capabilities(self) -> None:\n'
insert = '''\n    def test_formal_analysis_commands_require_contract_lifecycle(self) -> None:\n        module = (COMMANDS / "module-analysis.md").read_text(encoding="utf-8")\n        mr = (COMMANDS / "mr-regression.md").read_text(encoding="utf-8")\n        for text in (module, mr):\n            for command in ("draft-contract-v2", "confirm-contract-v2", "activate-contract-v2"):\n                self.assertIn(command, text)\n            self.assertNotIn("runctl.py create-v2", text)\n        self.assertIn("confirmation_required: true", (AGENTS / "pangea-test.md").read_text(encoding="utf-8"))\n\n'''
agent_test = replace_once(agent_test, marker, insert + marker, "contract lifecycle agent test")
write("tests/test_agent_v2.py", agent_test)
