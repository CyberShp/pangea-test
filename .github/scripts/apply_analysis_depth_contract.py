from __future__ import annotations

import json
import re
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


schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PANGEA Complete Analysis Model",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "artifact_type", "schema_version", "run_id", "analysis_depth", "source_commits",
        "evidence_consumption", "entrypoints", "flows", "branches", "states", "resources",
        "concurrency", "error_chains", "model_applicability", "scenario_candidates", "sfmea",
        "test_scenarios", "test_flows", "test_cases", "traceability", "coverage_dispositions",
        "depth_limitations", "unresolved",
    ],
    "properties": {
        "artifact_type": {"const": "analysis_model"},
        "schema_version": {"const": "1.0"},
        "run_id": {"type": "string", "minLength": 1},
        "analysis_depth": {"enum": ["complete", "fast"]},
        "source_commits": {
            "type": "object", "minProperties": 1,
            "additionalProperties": {"type": "string", "pattern": "^[a-f0-9]{40}$"},
        },
        "evidence_consumption": {"type": "array", "items": {"type": "object", "minProperties": 1}},
        "entrypoints": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "flows": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "branches": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "states": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "resources": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "concurrency": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "error_chains": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "model_applicability": {"type": "array", "minItems": 6, "items": {"type": "object", "minProperties": 1}},
        "scenario_candidates": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "sfmea": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "test_scenarios": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "test_flows": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "test_cases": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "traceability": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "coverage_dispositions": {"type": "array", "minItems": 1, "items": {"type": "object", "minProperties": 1}},
        "depth_limitations": {"type": "array", "items": {"type": "string", "minLength": 8}},
        "unresolved": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["item_id", "reason", "impact", "next_action"],
                "properties": {
                    "item_id": {"type": "string", "minLength": 1},
                    "reason": {"type": "string", "minLength": 8},
                    "impact": {"type": "string", "minLength": 8},
                    "next_action": {"type": "string", "minLength": 8},
                },
            },
        },
    },
}
write("schemas/analysis-model.schema.json", json.dumps(schema, ensure_ascii=False, indent=2) + "\n")

runctl = read("runtime/runctl.py")
runctl = replace_once(
    runctl,
    '_GENERIC_FACT_STAGES = {"code_map", "flow", "branches", "impact_chain", "dfx_route", "risk_ledger", "specialist", "sfmea", "test_design"}\n',
    '_GENERIC_FACT_STAGES = {"code_map", "flow", "branches", "impact_chain", "dfx_route", "risk_ledger", "specialist", "sfmea", "test_design"}\n'
    'ANALYSIS_MODEL_RELATIVE = "internal/analysis-model.json"\n'
    'ANALYSIS_OUTCOMES = {"analyzed", "covered_by_other", "not_applicable", "blocked", "need_verify", "truncated"}\n',
    "analysis constants",
)

analysis_helpers = r'''

_ANALYSIS_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "evidence_consumption": ("evidence_id", "source_ref", "status", "parser", "consumed_ranges", "conclusions", "used_by", "unread_ranges", "limitations"),
    "entrypoints": ("entrypoint_id", "title", "external_trigger", "registration", "preconditions", "flow_ids", "status", "disposition_reason", "source_evidence"),
    "flows": ("flow_id", "title", "priority", "external_trigger", "entrypoint_id", "registration", "preconditions", "normal_path", "decisions", "abnormal_paths", "state_changes", "resource_lifecycle", "timeout_retry_recovery", "concurrency", "error_propagation", "latent_or_secondary_failures", "blackbox_controls", "oracles", "source_evidence", "status", "disposition_reason"),
    "branches": ("branch_id", "flow_id", "condition", "true_path", "false_path", "external_effect", "controllability", "observability", "source_evidence", "status", "disposition_reason"),
    "states": ("state_id", "title", "initial_state", "transitions", "illegal_transitions", "external_controls", "observables", "source_evidence", "status", "disposition_reason"),
    "resources": ("resource_id", "title", "acquire", "owner", "release", "abnormal_cleanup", "invariant", "limits", "recovery", "source_evidence", "status", "disposition_reason"),
    "concurrency": ("concurrency_id", "title", "actors", "shared_state", "ordering", "race_windows", "cancellation", "recovery", "source_evidence", "status", "disposition_reason"),
    "error_chains": ("chain_id", "title", "trigger", "propagation", "masking", "terminal_effect", "recovery", "source_evidence", "status", "disposition_reason"),
    "model_applicability": ("dfx", "applicable", "reason", "evidence"),
    "scenario_candidates": ("candidate_id", "title", "drivers", "source_refs", "failure_mechanism", "external_construction", "injection", "oracle", "disposition", "target_ids"),
    "sfmea": ("sfmea_id", "title", "source_refs", "failure_mode", "cause", "local_effect", "external_effect", "detection", "recovery", "severity", "scenario_ids", "test_case_ids"),
    "test_scenarios": ("scenario_id", "title", "source_candidate_ids", "risk_ids", "preconditions", "trigger", "expected", "observations", "cleanup"),
    "test_flows": ("test_flow_id", "title", "scenario_id", "steps", "oracles", "cleanup", "test_case_ids"),
    "test_cases": ("case_id", "title", "scenario_id", "risk_ids", "preconditions", "steps", "expected", "observation", "cleanup", "source_refs"),
    "traceability": ("trace_id", "source_ids", "target_ids", "rationale"),
    "coverage_dispositions": ("item_type", "item_id", "outcome", "evidence", "covered_by", "missing_work"),
}
_ANALYSIS_ID_FIELDS = {
    "evidence_consumption": "evidence_id", "entrypoints": "entrypoint_id", "flows": "flow_id",
    "branches": "branch_id", "states": "state_id", "resources": "resource_id",
    "concurrency": "concurrency_id", "error_chains": "chain_id", "scenario_candidates": "candidate_id",
    "sfmea": "sfmea_id", "test_scenarios": "scenario_id", "test_flows": "test_flow_id",
    "test_cases": "case_id", "traceability": "trace_id",
}
_ANALYSIS_LIST_FIELDS = {
    "consumed_ranges", "conclusions", "used_by", "unread_ranges", "limitations", "flow_ids", "source_evidence",
    "normal_path", "decisions", "abnormal_paths", "state_changes", "resource_lifecycle", "timeout_retry_recovery",
    "concurrency", "error_propagation", "latent_or_secondary_failures", "blackbox_controls", "oracles", "transitions",
    "illegal_transitions", "external_controls", "observables", "limits", "actors", "shared_state", "ordering",
    "race_windows", "cancellation", "propagation", "drivers", "source_refs", "target_ids", "scenario_ids",
    "test_case_ids", "source_candidate_ids", "risk_ids", "observations", "steps", "covered_by", "source_ids",
}


def _analysis_model_path(run_dir: Path) -> Path:
    internal = (run_dir / "internal").resolve()
    path = run_dir / ANALYSIS_MODEL_RELATIVE
    if path.is_symlink() or path.resolve().parent != internal:
        raise RunCtlError("分析模型不得通过符号链接指向 Run 外部")
    return path.resolve()


def _require_analysis_text(value: Any, label: str, minimum: int = 4) -> None:
    if not _meaningful_text(value, minimum):
        raise RunCtlError(f"分析模型字段缺少具体内容: {label}")


def _require_analysis_list(value: Any, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise RunCtlError(f"分析模型字段必须是{'可空' if allow_empty else '非空'}数组: {label}")
    for index, item in enumerate(value, 1):
        if isinstance(item, str):
            _require_analysis_text(item, f"{label}[{index}]", 2)
        elif not isinstance(item, dict) or not item:
            raise RunCtlError(f"分析模型数组项无效: {label}[{index}]")


def _analysis_ids(model: dict[str, Any]) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = {}
    for collection, field in _ANALYSIS_ID_FIELDS.items():
        values: set[str] = set()
        for index, item in enumerate(model[collection], 1):
            value = item.get(field) if isinstance(item, dict) else None
            _require_analysis_text(value, f"{collection}[{index}].{field}", 2)
            if value in values:
                raise RunCtlError(f"分析模型 ID 重复: {value}")
            values.add(value)
        ids[collection] = values
    return ids


def _validate_analysis_model(model: Any, contract: dict[str, Any], run_id: str) -> dict[str, Any]:
    if not isinstance(model, dict):
        raise RunCtlError("分析模型必须是 JSON 对象")
    validate(model, "analysis-model.schema.json")
    if model.get("run_id") != run_id:
        raise RunCtlError("分析模型 run_id 与当前 Run 不一致")
    if model.get("analysis_depth") != contract.get("analysis_depth"):
        raise RunCtlError("分析模型 analysis_depth 与任务契约不一致")
    if model.get("source_commits") != contract.get("repository_commits"):
        raise RunCtlError("分析模型 source_commits 与任务契约 repository_commits 不一致")

    for collection, required in _ANALYSIS_COLLECTIONS.items():
        items = model.get(collection)
        if not isinstance(items, list) or not items:
            raise RunCtlError(f"完整分析缺少非空工件集合: {collection}")
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise RunCtlError(f"分析模型项必须是对象: {collection}[{index}]")
            missing = [field for field in required if field not in item]
            if missing:
                raise RunCtlError(f"{collection}[{index}] 缺少字段: {', '.join(missing)}")
            for field in required:
                value = item[field]
                label = f"{collection}[{index}].{field}"
                if field in _ANALYSIS_LIST_FIELDS:
                    _require_analysis_list(value, label, allow_empty=field in {"unread_ranges", "limitations", "covered_by", "missing_work", "test_case_ids"})
                elif field == "applicable":
                    if not isinstance(value, bool):
                        raise RunCtlError(f"分析模型字段必须是布尔值: {label}")
                elif field == "status":
                    if value not in ANALYSIS_OUTCOMES:
                        raise RunCtlError(f"分析模型 disposition 非法: {label}={value}")
                elif field == "outcome":
                    if value not in ANALYSIS_OUTCOMES:
                        raise RunCtlError(f"Coverage outcome 非法: {label}={value}")
                elif field == "disposition":
                    if value not in {"retained", "merged", "untestable", "out_of_scope", "blocked"}:
                        raise RunCtlError(f"场景候选 disposition 非法: {label}={value}")
                elif field == "severity":
                    if value not in {"Low", "Medium", "High", "Critical"}:
                        raise RunCtlError(f"SFMEA 严重度非法: {label}={value}")
                else:
                    _require_analysis_text(value, label, 2)

    ids = _analysis_ids(model)
    dfx = [item.get("dfx") for item in model["model_applicability"]]
    if len(dfx) != len(DFX_AGENTS) or set(dfx) != set(DFX_AGENTS) or len(dfx) != len(set(dfx)):
        raise RunCtlError("model_applicability 必须恰好覆盖六个 canonical DFX")

    entrypoints, flows = ids["entrypoints"], ids["flows"]
    branches, states, resources = ids["branches"], ids["states"], ids["resources"]
    concurrency, chains = ids["concurrency"], ids["error_chains"]
    scenarios, cases = ids["test_scenarios"], ids["test_cases"]
    candidates = ids["scenario_candidates"]
    for item in model["entrypoints"]:
        unknown = set(item["flow_ids"]) - flows
        if unknown:
            raise RunCtlError(f"入口引用未知 flow: {sorted(unknown)}")
    for item in model["flows"]:
        if item["entrypoint_id"] not in entrypoints:
            raise RunCtlError(f"Flow 引用未知 entrypoint: {item['entrypoint_id']}")
        reference_fields = {
            "decisions": branches, "state_changes": states, "resource_lifecycle": resources,
            "concurrency": concurrency, "error_propagation": chains,
        }
        for field, known in reference_fields.items():
            unknown = set(item[field]) - known
            if unknown:
                raise RunCtlError(f"Flow {item['flow_id']} 的 {field} 引用未知 ID: {sorted(unknown)}")
    for item in model["branches"]:
        if item["flow_id"] not in flows:
            raise RunCtlError(f"Branch 引用未知 flow: {item['flow_id']}")
    for item in model["test_scenarios"]:
        unknown = set(item["source_candidate_ids"]) - candidates
        if unknown:
            raise RunCtlError(f"测试场景引用未知 candidate: {sorted(unknown)}")
    for item in model["test_flows"]:
        if item["scenario_id"] not in scenarios:
            raise RunCtlError(f"测试流程引用未知 scenario: {item['scenario_id']}")
        unknown = set(item["test_case_ids"]) - cases
        if unknown:
            raise RunCtlError(f"测试流程引用未知 case: {sorted(unknown)}")
    for item in model["test_cases"]:
        if item["scenario_id"] not in scenarios:
            raise RunCtlError(f"测试用例引用未知 scenario: {item['scenario_id']}")

    all_ids = set().union(*ids.values())
    for item in model["coverage_dispositions"]:
        if item["item_id"] not in all_ids:
            raise RunCtlError(f"Coverage disposition 引用未知分析项: {item['item_id']}")
    covered_items = {item["item_id"] for item in model["coverage_dispositions"]}
    mandatory = entrypoints | flows | branches | states | resources | concurrency | chains | candidates
    missing_dispositions = sorted(mandatory - covered_items)
    if missing_dispositions:
        raise RunCtlError("完整分析存在未处置项: " + ", ".join(missing_dispositions))

    incomplete = {
        item.get(field) for collection, field in _ANALYSIS_ID_FIELDS.items()
        for item in model[collection]
        if item.get("status") in {"blocked", "need_verify", "truncated"}
    }
    incomplete |= {item["item_id"] for item in model["coverage_dispositions"]
                   if item["outcome"] in {"blocked", "need_verify", "truncated"}}
    unresolved_ids = {item.get("item_id") for item in model.get("unresolved", []) if isinstance(item, dict)}
    if incomplete - unresolved_ids:
        raise RunCtlError("blocked/need_verify/truncated 项必须逐项进入 unresolved: " + ", ".join(sorted(incomplete - unresolved_ids)))

    if contract.get("analysis_depth") == "complete":
        truncated = [item["item_id"] for item in model["coverage_dispositions"] if item["outcome"] == "truncated"]
        if truncated:
            raise RunCtlError("complete 模式不得以 truncated 通过门禁: " + ", ".join(truncated))
    elif not model.get("depth_limitations"):
        raise RunCtlError("fast 模式必须明确 depth_limitations，禁止伪装成完整型")
    return model


def _analysis_model_binding(run_dir: Path, contract: dict[str, Any], *, required: bool) -> dict[str, str] | None:
    path = _analysis_model_path(run_dir)
    if not path.is_file():
        if required:
            raise RunCtlError(f"完整型模块分析缺少固定分析模型: {ANALYSIS_MODEL_RELATIVE}")
        return None
    model = _validate_analysis_model(read_json(path), contract, run_dir.name)
    del model
    return {"path": ANALYSIS_MODEL_RELATIVE, "sha256": _sha256_file(path)}


def _requires_complete_analysis_model(contract: dict[str, Any]) -> bool:
    return contract.get("mode") == "module_analysis" and contract.get("analysis_depth") == "complete"
'''
runctl = replace_once(
    runctl,
    '\ndef _assert_report_contract_and_sections(run_dir: Path, model: Any) -> dict[str, Any]:\n',
    analysis_helpers + '\n\ndef _assert_report_contract_and_sections(run_dir: Path, model: Any) -> dict[str, Any]:\n',
    "analysis helpers",
)

runctl = replace_once(
    runctl,
    '    if reported != canonical:\n        raise RunCtlError("report-model task_contract 与 Run internal/task-contract.json canonical 内容不一致")\n    required_sections = ("code_map", "flows", "branches", "risks")\n',
    '    if reported != canonical:\n        raise RunCtlError("report-model task_contract 与 Run internal/task-contract.json canonical 内容不一致")\n'
    '    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))\n'
    '    if binding is not None and model.get("analysis_artifact") != binding:\n'
    '        raise RunCtlError("report-model 未精确绑定当前固定分析模型")\n'
    '    required_sections = ("code_map", "flows", "branches", "risks")\n',
    "report analysis binding",
)

stage_analysis = r'''

def stage_analysis_v2(args: argparse.Namespace) -> None:
    """Validate and atomically stage the complete source-driven analysis model."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入分析模型")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    if contract.get("mode") != "module_analysis":
        raise RunCtlError("stage-analysis-v2 当前仅用于模块分析")
    plan = _load_v2_workflow_plan(run_dir)
    _assert_analysis_stages_complete(run_dir, plan)
    if args.json is not None:
        try:
            model = json.loads(args.json)
        except json.JSONDecodeError as exc:
            raise RunCtlError(f"--json 分析模型无效: {exc}") from exc
    else:
        source = Path(args.file).expanduser()
        if source.is_symlink() or not source.is_file():
            raise RunCtlError(f"分析模型输入必须是普通文件: {source}")
        model = read_json(source.resolve())
    normalized = _validate_analysis_model(model, contract, args.run_id)
    target = _analysis_model_path(run_dir)
    data_runtime.atomic_write_json(target, normalized)
    digest = _sha256_file(target)
    data_runtime.set_run_state(root, args.run_id, "reviewing", "完整分析模型已落盘，准备生成报告模型")
    print(json.dumps({"run_id": args.run_id, "analysis_model": str(target),
                      "analysis_artifact": ANALYSIS_MODEL_RELATIVE, "sha256": digest,
                      "next_step": "stage-report-v2"}, ensure_ascii=False))
'''
runctl = replace_once(
    runctl,
    '\ndef stage_report_v2(args: argparse.Namespace) -> None:\n',
    stage_analysis + '\n\ndef stage_report_v2(args: argparse.Namespace) -> None:\n',
    "stage analysis command",
)

runctl = replace_once(
    runctl,
    '    plan = _load_v2_workflow_plan(run_dir)\n    _assert_analysis_stages_complete(run_dir, plan)\n    if args.json is not None:\n',
    '    plan = _load_v2_workflow_plan(run_dir)\n'
    '    _assert_analysis_stages_complete(run_dir, plan)\n'
    '    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))\n'
    '    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))\n'
    '    if args.json is not None:\n',
    "stage report load analysis",
)
runctl = replace_once(
    runctl,
    '    if not isinstance(model, dict):\n        raise RunCtlError("报告模型必须是 JSON 对象")\n    model = _assert_report_contract_and_sections(run_dir, model)\n',
    '    if not isinstance(model, dict):\n        raise RunCtlError("报告模型必须是 JSON 对象")\n'
    '    if analysis_binding is not None:\n        model["analysis_artifact"] = analysis_binding\n'
    '    model = _assert_report_contract_and_sections(run_dir, model)\n',
    "inject analysis binding",
)

runctl = replace_once(
    runctl,
    '    stage2 = sub.add_parser("stage-report-v2", help="校验并实际落盘固定报告模型")\n',
    '    analysis2 = sub.add_parser("stage-analysis-v2", help="校验并实际落盘完整分析模型")\n'
    '    analysis2.add_argument("--run-id", required=True)\n'
    '    analysis_input = analysis2.add_mutually_exclusive_group(required=True)\n'
    '    analysis_input.add_argument("--file")\n'
    '    analysis_input.add_argument("--json")\n'
    '    analysis2.add_argument("--root")\n'
    '    analysis2.set_defaults(func=stage_analysis_v2)\n'
    '    stage2 = sub.add_parser("stage-report-v2", help="校验并实际落盘固定报告模型")\n',
    "parser stage analysis",
)
write("runtime/runctl.py", runctl)

# Update primary workflow contract.
agent = read(".opencode/agents/pangea-test.md")
agent = replace_once(
    agent,
    '- 子 Agent 只能返回结构化风险卡，不能直接写报告或用例集。主 Agent 负责风险去重、跨维度合并、严重度和可信度、内部 SFMEA、黑盒转译与报告。',
    '- 子 Agent 不得只返回风险卡。每次深挖必须同时返回其负责范围的结构化模型贡献（Flow/Branch/State/Resource/Concurrency/Error Chain/Scenario Candidate/Coverage disposition）和风险卡；主 Agent 负责合并为固定 `internal/analysis-model.json`。缺少模型贡献时不得把该 DFX 维度标为完成。',
    "primary subagent contract",
)
needle = '3. 资源与规格必须先轻量扫描；命中申请、释放、计数、队列、连接、缓存、内存池等信号，或用户明确强调时，进入资源规格、泄漏、过载回落和长稳专项深挖。\n'
replacement = needle + '''4. `complete` 与 `fast` 必须由工件区分，不能只改任务标签。完整型在审计前必须生成并通过 `stage-analysis-v2`：输入材料消费、入口清单、完整 Flow Card、分支/状态/资源/并发/错误传播、六维适用性、场景候选、SFMEA、测试场景、测试流程、测试用例、追溯和 Coverage disposition。每个 P0/P1 Flow 必须回答外部触发、入口注册、前置状态、主路径、判断分支、状态变化、资源所有权、超时重试恢复、并发窗口、错误传播、潜伏故障、黑盒控制/Oracle 与源码证据。`fast` 必须填写 `depth_limitations`，不得以完整型口径交付。\n'''
agent = replace_once(agent, needle, replacement, "complete depth contract")
agent = replace_once(
    agent,
    '完成分析阶段和报告模型后，必须调用 `runctl stage-report-v2`，由确定性运行时校验并实际写入唯一允许被审的固定文件 `pangea-data/runs/<run-id>/internal/report-model.json`。只能使用命令返回的 SHA-256 和 `audited_artifact` 交给只读 `auditor`；不得只在对话中总结报告、声称已写入，或让 auditor 计算、猜测和替换哈希。',
    '完成全部分析阶段后，完整型模块分析必须先调用 `runctl stage-analysis-v2`，由运行时校验并写入 `pangea-data/runs/<run-id>/internal/analysis-model.json`。随后调用 `runctl stage-report-v2`；运行时会把报告模型绑定到该分析模型的 SHA-256。没有有效分析模型时不得进入审计。只能使用命令返回的固定路径和哈希；不得用聊天总结或阶段套话代替分析工件。',
    "audit analysis gate",
)
write(".opencode/agents/pangea-test.md", agent)

module_cmd = read(".opencode/commands/module-analysis.md")
module_cmd = replace_once(
    module_cmd,
    '审计门禁：主 Agent 先调用 `python3 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <完整报告模型JSON>`，',
    '深度门禁：完成分析阶段后，先调用 `python3 runtime/runctl.py stage-analysis-v2 --run-id <Run ID> --file <完整分析模型JSON>`。完整分析模型必须覆盖输入消费、入口、Flow Card、分支、状态、资源、并发、错误传播、六维适用性、场景候选、SFMEA、测试流程、用例、追溯与 Coverage disposition。命令失败时不得继续。然后进入审计门禁：主 Agent 调用 `python3 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <完整报告模型JSON>`，',
    "module command gate",
)
write(".opencode/commands/module-analysis.md", module_cmd)

code_excavator = read(".opencode/agents/code-excavator.md")
code_excavator = replace_once(
    code_excavator,
    '输入必须指定目标、源码范围和需要确认的事实。输出只包含：文件与行号、相关符号、调用或状态关系、直接可见事实、尚未证实的推断及其验证建议。先说明这段代码对外部行为意味着什么，再给出源码细节。',
    '输入必须指定目标、源码范围、Pass/Flow ID 和需要确认的事实。输出必须包含：精确文件与行号、相关符号、注册和可达性、外部触发、前置状态、按时序展开的主路径、影响外部行为的判断、状态变化、资源申请/归还、超时重试恢复、并发窗口、错误传播与终点、黑盒控制/Oracle、直接事实、待验证项及 Coverage disposition。先给开发实现讲解，再给结构化模型贡献；不得只回传函数列表或一张风险卡。',
    "excavator contract",
)
write(".opencode/agents/code-excavator.md", code_excavator)

for path in sorted((ROOT / ".opencode/agents").glob("dfx-*.md")):
    text = path.read_text(encoding="utf-8")
    if "只输出 `风险卡`" in text:
        text = text.replace(
            "只输出 `风险卡`，遵循 `skills/risk-card/SKILL.md`。",
            "输出两部分：①本维度的结构化分析模型贡献和逐项 disposition；②符合 `skills/risk-card/SKILL.md` 的风险卡。不得把完整 Flow/State/Resource/Error Chain 压缩成风险卡后丢弃。",
        )
        path.write_text(text, encoding="utf-8")

# Auditor must judge the analysis chain, not merely the final formatting.
auditor = read(".opencode/agents/auditor.md")
auditor = auditor.replace(
    '输入为任务契约、风险卡、代码证据、报告草稿，以及主 Agent 已写入 Run 的报告模型绑定。',
    '输入为任务契约、固定分析模型、风险卡、代码证据、报告模型，以及两个固定工件的绑定。',
)
auditor = auditor.replace(
    '5. 报告是否覆盖任务契约所承诺的 DFX 维度、MR 四项基础覆盖或模块完整/快速深度边界。',
    '5. 独立比较入口清单与 Flow Card、Flow 与 Branch/State/Resource/Concurrency/Error Chain、场景候选与 SFMEA/测试流程/用例、Coverage disposition 与未闭环项；不得以 Producer 的“已完成”文字作为证据。\n6. 报告是否精确绑定固定分析模型，并完整消费其开发讲解、状态资源模型、错误传播、场景推导、SFMEA、测试流程和覆盖结论。',
)
write(".opencode/agents/auditor.md", auditor)

skill = '''---
name: analysis-depth-contract
description: PANGEA-TEST 完整型源码分析模型与 Coverage disposition 契约
---

# 完整型分析模型

`complete` 模块分析必须在报告前写入 `pangea-data/runs/<run-id>/internal/analysis-model.json`，并通过：

```text
python runtime/runctl.py stage-analysis-v2 --run-id <run-id> --file <analysis-model.json>
```

固定模型必须包含输入材料消费、入口、完整 Flow Card、分支、状态、资源、并发、错误传播、六维适用性、场景候选、SFMEA、测试场景、测试流程、用例、追溯、Coverage disposition、深度限制和未闭环项。

每个入口、Flow、Branch、State、Resource、Concurrency、Error Chain 和 Scenario Candidate 都必须有 disposition。允许 `analyzed`、`covered_by_other`、`not_applicable`、`blocked`、`need_verify`、`truncated`；完整型不得以 `truncated` 通过。所有 `blocked`/`need_verify`/`truncated` 项必须逐项进入 `unresolved`，写明原因、影响和最小下一步。

风险卡只是风险视图，不能替代开发实现模型。报告模型必须绑定固定分析模型的路径和 SHA-256，不能手工省略分析链。
'''
write(".opencode/skills/analysis-depth-contract/SKILL.md", skill)

# Regression tests focused on the new gate.
test = r'''from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"
DFX = ("功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性")


class AnalysisDepthContractTests(unittest.TestCase):
    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, text=True,
                              capture_output=True, check=False)

    def cli(self, *args: str) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    @staticmethod
    def repository(root: Path) -> None:
        repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        (repo / "driver.c").write_text("int entry(void) { return 0; }\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "driver.c"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid",
                        "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "initial"], check=True)

    @staticmethod
    def complete_checkpoints(root: Path, run_id: str) -> None:
        for stage in ("code_map", "flow", "branches"):
            data_runtime.append_checkpoint(root, run_id, {"stage": stage, "status": "completed",
                "facts": [{"summary": f"{stage} 已建立具体实现模型", "evidence": f"driver.c: {stage} evidence"}],
                "open_items": [], "next_step": "继续"})
        data_runtime.append_checkpoint(root, run_id, {"stage": "dfx_scan", "status": "completed",
            "facts": [{"dfx": item, "conclusion": f"{item}已形成具体结论", "evidence": f"driver.c: {item}"} for item in DFX],
            "open_items": [], "next_step": "继续"})
        for stage in ("specialist", "sfmea", "test_design"):
            data_runtime.append_checkpoint(root, run_id, {"stage": stage, "status": "completed",
                "facts": [{"summary": f"{stage} 已形成具体分析工件", "evidence": f"internal/{stage}.json"}],
                "open_items": [], "next_step": "继续"})

    @staticmethod
    def model(run_dir: Path, depth: str = "complete") -> dict:
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        evidence = [{"path": "driver.c", "line": 1, "fact": "entry is externally registered"}]
        disposition = {"status": "analyzed", "disposition_reason": "已读取直接源码并完成外部行为分析"}
        return {
            "artifact_type": "analysis_model", "schema_version": "1.0", "run_id": run_dir.name,
            "analysis_depth": depth, "source_commits": contract["repository_commits"],
            "evidence_consumption": [{"evidence_id": "E-1", "source_ref": "driver.c", "status": "parsed",
                "parser": "source reader", "consumed_ranges": ["driver.c:1"], "conclusions": ["入口可达"],
                "used_by": ["EP-1", "FLOW-1"], "unread_ranges": [], "limitations": []}],
            "entrypoints": [{"entrypoint_id": "EP-1", "title": "外部入口", "external_trigger": "发送业务请求",
                "registration": "启动时登记入口", "preconditions": "模块已初始化", "flow_ids": ["FLOW-1"],
                "source_evidence": evidence, **disposition}],
            "flows": [{"flow_id": "FLOW-1", "title": "业务主流程", "priority": "P0",
                "external_trigger": "发送业务请求", "entrypoint_id": "EP-1", "registration": "启动时登记",
                "preconditions": "模块正常运行", "normal_path": ["接收请求", "校验状态", "返回结果"],
                "decisions": ["BR-1"], "abnormal_paths": ["非法请求返回错误"], "state_changes": ["STATE-1"],
                "resource_lifecycle": ["RES-1"], "timeout_retry_recovery": ["超时后返回并允许重试"],
                "concurrency": ["CON-1"], "error_propagation": ["ERR-1"],
                "latent_or_secondary_failures": ["连续失败可能造成状态残留"],
                "blackbox_controls": ["构造非法请求和超时"], "oracles": ["返回码、日志和后续业务恢复"],
                "source_evidence": evidence, **disposition}],
            "branches": [{"branch_id": "BR-1", "flow_id": "FLOW-1", "condition": "请求字段是否合法",
                "true_path": "继续处理", "false_path": "返回错误", "external_effect": "请求成功或明确失败",
                "controllability": "可修改请求字段", "observability": "返回码和日志", "source_evidence": evidence, **disposition}],
            "states": [{"state_id": "STATE-1", "title": "运行状态", "initial_state": "READY",
                "transitions": ["READY->BUSY->READY"], "illegal_transitions": ["ERROR->BUSY"],
                "external_controls": ["发送请求或触发恢复"], "observables": ["业务结果和状态日志"],
                "source_evidence": evidence, **disposition}],
            "resources": [{"resource_id": "RES-1", "title": "请求额度", "acquire": "接收请求时占用",
                "owner": "当前请求", "release": "请求完成时归还", "abnormal_cleanup": "异常出口统一归还",
                "invariant": "占用数不超过上限且完成后回落", "limits": ["N-1", "N", "N+1"],
                "recovery": "压力解除后自动恢复", "source_evidence": evidence, **disposition}],
            "concurrency": [{"concurrency_id": "CON-1", "title": "请求与恢复并发", "actors": ["请求线程", "恢复线程"],
                "shared_state": ["运行状态", "请求额度"], "ordering": ["状态检查先于资源占用"],
                "race_windows": ["恢复与新请求同时发生"], "cancellation": ["取消后释放额度"],
                "recovery": "并发结束后状态和额度恢复", "source_evidence": evidence, **disposition}],
            "error_chains": [{"chain_id": "ERR-1", "title": "非法请求传播", "trigger": "字段非法",
                "propagation": ["校验失败", "错误返回", "记录日志"], "masking": "不得转换为成功",
                "terminal_effect": "当前请求失败但后续业务可继续", "recovery": "修正请求后重试",
                "source_evidence": evidence, **disposition}],
            "model_applicability": [{"dfx": item, "applicable": True, "reason": f"{item}与该流程相关",
                "evidence": f"driver.c: {item}"} for item in DFX],
            "scenario_candidates": [{"candidate_id": "CAND-1", "title": "非法字段后恢复", "drivers": ["分支", "状态", "异常传播"],
                "source_refs": ["BR-1", "STATE-1", "ERR-1"], "failure_mechanism": "错误路径可能残留状态",
                "external_construction": "发送非法请求后立即发送正常请求", "injection": "无需内部注入",
                "oracle": "非法请求失败且正常请求成功", "disposition": "retained", "target_ids": ["SC-1", "TC-1"]}],
            "sfmea": [{"sfmea_id": "SF-1", "title": "错误后状态残留", "source_refs": ["ERR-1", "STATE-1"],
                "failure_mode": "非法请求后状态未恢复", "cause": "异常出口遗漏恢复", "local_effect": "状态保持异常",
                "external_effect": "后续正常请求失败", "detection": "返回码、日志、后续业务", "recovery": "重新发起恢复或重连",
                "severity": "High", "scenario_ids": ["SC-1"], "test_case_ids": ["TC-1"]}],
            "test_scenarios": [{"scenario_id": "SC-1", "title": "非法请求后正常业务恢复", "source_candidate_ids": ["CAND-1"],
                "risk_ids": ["R-1"], "preconditions": "模块正常运行", "trigger": "先非法后正常请求",
                "expected": "非法请求失败且正常请求成功", "observations": ["返回码", "日志", "业务状态"],
                "cleanup": "结束请求并确认资源释放"}],
            "test_flows": [{"test_flow_id": "TF-1", "title": "错误恢复测试流程", "scenario_id": "SC-1",
                "steps": ["建立正常基线", "发送非法请求", "发送正常请求", "检查资源和状态"],
                "oracles": ["错误可见且后续业务成功"], "cleanup": "释放会话", "test_case_ids": ["TC-1"]}],
            "test_cases": [{"case_id": "TC-1", "title": "非法字段恢复", "scenario_id": "SC-1", "risk_ids": ["R-1"],
                "preconditions": "模块正常运行", "steps": ["发送非法请求", "随后发送正常请求"],
                "expected": "非法请求失败且正常请求成功", "observation": "返回码、日志、资源计数",
                "cleanup": "释放会话", "source_refs": ["BR-1", "ERR-1"]}],
            "traceability": [{"trace_id": "TR-1", "source_ids": ["BR-1", "ERR-1", "CAND-1"],
                "target_ids": ["SF-1", "SC-1", "TF-1", "TC-1"], "rationale": "分支和错误传播推导测试"}],
            "coverage_dispositions": [
                {"item_type": kind, "item_id": item_id, "outcome": "analyzed", "evidence": "已形成直接源码分析工件",
                 "covered_by": ["TC-1"], "missing_work": []}
                for kind, item_id in (("entrypoint", "EP-1"), ("flow", "FLOW-1"), ("branch", "BR-1"),
                                      ("state", "STATE-1"), ("resource", "RES-1"), ("concurrency", "CON-1"),
                                      ("error_chain", "ERR-1"), ("candidate", "CAND-1"))
            ],
            "depth_limitations": [] if depth == "complete" else ["快速模式只展开一个 P0 流程，其他 P1 流程待补充"],
            "unresolved": [],
        }

    def test_complete_run_requires_staged_analysis_model_before_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "driver",
                               "--repository", "driver", "--run-id", "depth", "--analysis-depth", "complete")
            run_dir = Path(created["run_dir"]); self.complete_checkpoints(root, "depth")
            report = {"title": "报告", "task_contract": json.loads((run_dir / "internal/task-contract.json").read_text()),
                      "code_map": [{"title": "入口", "test_explanation": "外部请求进入模块", "source_evidence": "driver.c:1"}],
                      "flows": [{"title": "流程", "test_explanation": "请求进入后返回结果", "steps": ["发送请求", "观察结果"], "source_evidence": "driver.c:1"}],
                      "branches": [{"title": "分支", "test_explanation": "非法输入返回错误", "source_evidence": "driver.c:1"}],
                      "risks": [], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            report_path = root / "report.json"; report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("stage-report-v2", "--root", tmp, "--run-id", "depth", "--file", str(report_path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("analysis-model.json", rejected.stderr)

            model_path = root / "analysis.json"; model_path.write_text(json.dumps(self.model(run_dir), ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-analysis-v2", "--root", tmp, "--run-id", "depth", "--file", str(model_path))
            self.assertEqual("internal/analysis-model.json", staged["analysis_artifact"])
            self.assertEqual(hashlib.sha256((run_dir / "internal/analysis-model.json").read_bytes()).hexdigest(), staged["sha256"])

    def test_shallow_flow_card_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "driver",
                               "--repository", "driver", "--run-id", "shallow", "--analysis-depth", "complete")
            run_dir = Path(created["run_dir"]); self.complete_checkpoints(root, "shallow")
            model = self.model(run_dir); model["flows"][0].pop("resource_lifecycle")
            path = root / "shallow.json"; path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            result = self.cli_result("stage-analysis-v2", "--root", tmp, "--run-id", "shallow", "--file", str(path))
            self.assertEqual(2, result.returncode)
            self.assertIn("resource_lifecycle", result.stderr)

    def test_fast_model_requires_explicit_depth_limitations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.repository(root)
            created = self.cli("create-v2", "--root", tmp, "--scenario", "module-analysis", "--target", "driver",
                               "--repository", "driver", "--run-id", "fast", "--analysis-depth", "fast")
            run_dir = Path(created["run_dir"]); self.complete_checkpoints(root, "fast")
            model = self.model(run_dir, "fast"); model["depth_limitations"] = []
            path = root / "fast.json"; path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            result = self.cli_result("stage-analysis-v2", "--root", tmp, "--run-id", "fast", "--file", str(path))
            self.assertEqual(2, result.returncode)
            self.assertIn("depth_limitations", result.stderr)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_analysis_depth_contract.py", test)

# Ensure repository docs mention the gate.
requirements = read("docs/requirements.md")
requirements += '''\n\n**R16 完整型分析深度。** `module-analysis --analysis-depth complete` 必须在报告审计前通过 `stage-analysis-v2`，生成固定 `internal/analysis-model.json`。模型必须逐项覆盖入口、完整 Flow Card、分支、状态、资源、并发、错误传播、场景候选、SFMEA、测试流程、用例、追溯和 Coverage disposition。风险卡、阶段摘要或六句 DFX 结论不能替代分析模型。`fast` 必须记录明确 `depth_limitations`。\n'''
write("docs/requirements.md", requirements)
