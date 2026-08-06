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


judge_module = r'''"""Independent deterministic coverage judge for complete module analysis."""
from __future__ import annotations

from typing import Any

from runtime import analysis_reporting

CHECKS = (
    "model_integrity", "breadth_disposition", "scenario_derivation",
    "test_traceability", "report_projection",
)


def _ids(items: list[dict[str, Any]], field: str) -> set[str]:
    return {str(item[field]) for item in items if isinstance(item, dict) and item.get(field)}


def judge(analysis: dict[str, Any], report: dict[str, Any], ledger: dict[str, Any]) -> dict[str, Any]:
    findings: dict[str, list[str]] = {name: [] for name in CHECKS}
    entrypoints = _ids(analysis["entrypoints"], "entrypoint_id")
    flows = _ids(analysis["flows"], "flow_id")
    branches = _ids(analysis["branches"], "branch_id")
    states = _ids(analysis["states"], "state_id")
    resources = _ids(analysis["resources"], "resource_id")
    concurrency = _ids(analysis["concurrency"], "concurrency_id")
    errors = _ids(analysis["error_chains"], "chain_id")
    candidates = _ids(analysis["scenario_candidates"], "candidate_id")
    sfmea = _ids(analysis["sfmea"], "sfmea_id")
    scenarios = _ids(analysis["test_scenarios"], "scenario_id")
    test_flows = _ids(analysis["test_flows"], "test_flow_id")
    cases = _ids(analysis["test_cases"], "case_id")
    trace = _ids(analysis["traceability"], "trace_id")
    all_ids = entrypoints | flows | branches | states | resources | concurrency | errors | candidates | sfmea | scenarios | test_flows | cases | trace

    for item in analysis["entrypoints"]:
        unknown = set(map(str, item["flow_ids"])) - flows
        if unknown:
            findings["model_integrity"].append(f"入口 {item['entrypoint_id']} 引用未知 Flow: {sorted(unknown)}")
    for item in analysis["flows"]:
        if item["entrypoint_id"] not in entrypoints:
            findings["model_integrity"].append(f"Flow {item['flow_id']} 引用未知入口 {item['entrypoint_id']}")
    for item in analysis["traceability"]:
        unknown = (set(map(str, item["source_ids"])) | set(map(str, item["target_ids"]))) - all_ids
        if unknown:
            findings["model_integrity"].append(f"追溯 {item['trace_id']} 引用未知 ID: {sorted(unknown)}")

    dispositions = {str(item["item_id"]): item for item in analysis["coverage_dispositions"]}
    mandatory = entrypoints | flows | branches | states | resources | concurrency | errors | candidates
    for item_id in sorted(mandatory - set(dispositions)):
        findings["breadth_disposition"].append(f"缺少 Coverage disposition: {item_id}")
    cover_targets = scenarios | test_flows | cases
    for item_id, item in dispositions.items():
        covered_by = set(map(str, item.get("covered_by", [])))
        unknown = covered_by - cover_targets
        if unknown:
            findings["breadth_disposition"].append(f"{item_id} covered_by 引用未知测试工件: {sorted(unknown)}")
        if item["outcome"] in {"analyzed", "covered_by_other"} and not covered_by:
            findings["breadth_disposition"].append(f"{item_id} 已分析但未绑定场景、测试流程或用例")

    candidate_by_id = {str(item["candidate_id"]): item for item in analysis["scenario_candidates"]}
    scenario_candidate_refs = {str(ref) for item in analysis["test_scenarios"] for ref in item["source_candidate_ids"]}
    for candidate_id, item in candidate_by_id.items():
        targets = set(map(str, item["target_ids"]))
        unknown = targets - (scenarios | cases)
        if unknown:
            findings["scenario_derivation"].append(f"候选 {candidate_id} target_ids 未落到场景或用例: {sorted(unknown)}")
        if item["disposition"] == "retained" and candidate_id not in scenario_candidate_refs:
            findings["scenario_derivation"].append(f"保留候选 {candidate_id} 未被测试场景消费")
    for item in analysis["sfmea"]:
        unknown_scenarios = set(map(str, item["scenario_ids"])) - scenarios
        unknown_cases = set(map(str, item["test_case_ids"])) - cases
        if unknown_scenarios or unknown_cases:
            findings["scenario_derivation"].append(
                f"SFMEA {item['sfmea_id']} 引用未知场景/用例: {sorted(unknown_scenarios | unknown_cases)}"
            )

    flow_scenarios = {str(item["scenario_id"]) for item in analysis["test_flows"]}
    flow_cases = {str(case_id) for item in analysis["test_flows"] for case_id in item["test_case_ids"]}
    for scenario_id in sorted(scenarios - flow_scenarios):
        findings["test_traceability"].append(f"场景 {scenario_id} 没有黑盒测试流程")
    for case_id in sorted(cases - flow_cases):
        findings["test_traceability"].append(f"用例 {case_id} 没有被测试流程编排")
    risk_by_id = {str(item["risk_id"]): item for item in ledger.get("risks", []) if isinstance(item, dict)}
    executable_refs = {str(risk_id) for item in analysis["test_scenarios"] for risk_id in item["risk_ids"]}
    executable_refs |= {str(risk_id) for item in analysis["test_cases"] for risk_id in item["risk_ids"]}
    for risk_id, risk in risk_by_id.items():
        if risk.get("translation_status") != "Developer-confirm" and risk_id not in executable_refs:
            findings["test_traceability"].append(f"可执行风险 {risk_id} 未映射到场景或用例")
    for risk_id in sorted(executable_refs - set(risk_by_id)):
        findings["test_traceability"].append(f"测试工件引用风险账本外风险: {risk_id}")
    for item in analysis["test_flows"]:
        if not item.get("oracles"):
            findings["test_traceability"].append(f"测试流程 {item['test_flow_id']} 缺少独立 Oracle")
    for item in analysis["scenario_candidates"]:
        if not str(item.get("oracle", "")).strip():
            findings["test_traceability"].append(f"场景候选 {item['candidate_id']} 缺少独立 Oracle")

    unresolved_ids = {str(item.get("item_id")) for item in analysis.get("unresolved", []) if isinstance(item, dict)}
    for item in analysis["evidence_consumption"]:
        if item["status"] in {"blocked", "unreadable", "partially_parsed"} and item["evidence_id"] not in unresolved_ids:
            findings["model_integrity"].append(f"材料 {item['evidence_id']} 未完整消费但未进入 unresolved")

    try:
        analysis_reporting.assert_projection(report, analysis)
    except ValueError as exc:
        findings["report_projection"].append(str(exc))

    checks = {
        name: {"verdict": "PASS" if not items else "FAIL", "findings": items}
        for name, items in findings.items()
    }
    verdict = "PASS" if all(check["verdict"] == "PASS" for check in checks.values()) else "FAIL"
    return {"verdict": verdict, "checks": checks}
'''
write("runtime/coverage_judge.py", judge_module)

schema = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "PANGEA Coverage Judge",
    "type": "object", "additionalProperties": False,
    "required": ["artifact_type", "schema_version", "run_id", "checked_at", "analysis_artifact",
                 "report_artifact", "risk_ledger_artifact", "verdict", "checks"],
    "properties": {
        "artifact_type": {"const": "coverage_judge"}, "schema_version": {"const": "1.0"},
        "run_id": {"type": "string", "minLength": 1}, "checked_at": {"type": "string", "minLength": 1},
        "verdict": {"enum": ["PASS", "FAIL"]},
        "analysis_artifact": {"$ref": "#/$defs/binding"}, "report_artifact": {"$ref": "#/$defs/binding"},
        "risk_ledger_artifact": {"$ref": "#/$defs/binding"},
        "checks": {
            "type": "object", "additionalProperties": False,
            "required": ["model_integrity", "breadth_disposition", "scenario_derivation", "test_traceability", "report_projection"],
            "properties": {name: {"$ref": "#/$defs/check"} for name in (
                "model_integrity", "breadth_disposition", "scenario_derivation", "test_traceability", "report_projection")},
        },
    },
    "$defs": {
        "binding": {"type": "object", "additionalProperties": False, "required": ["path", "sha256"],
                    "properties": {"path": {"type": "string", "minLength": 1},
                                   "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}},
        "check": {"type": "object", "additionalProperties": False, "required": ["verdict", "findings"],
                  "properties": {"verdict": {"enum": ["PASS", "FAIL"]},
                                 "findings": {"type": "array", "items": {"type": "string", "minLength": 1}}}},
    },
}
write("schemas/coverage-judge.schema.json", json.dumps(schema, ensure_ascii=False, indent=2) + "\n")

runctl = read("runtime/runctl.py")
runctl = replace_once(
    runctl,
    'ANALYSIS_MODEL_RELATIVE = "internal/analysis-model.json"\n',
    'ANALYSIS_MODEL_RELATIVE = "internal/analysis-model.json"\nCOVERAGE_JUDGE_RELATIVE = "internal/coverage-judge.json"\n',
    "judge constant",
)
helpers = r'''

def _coverage_judge_path(run_dir: Path) -> Path:
    internal = (run_dir / "internal").resolve()
    path = run_dir / COVERAGE_JUDGE_RELATIVE
    if path.is_symlink() or path.resolve().parent != internal:
        raise RunCtlError("Coverage Judge 工件不得通过符号链接指向 Run 外部")
    return path.resolve()


def _binding(path: Path, relative: str) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise RunCtlError(f"Judge 绑定工件不存在或不是普通文件: {relative}")
    return {"path": relative, "sha256": _sha256_file(path)}


def _judge_required(contract: dict[str, Any]) -> bool:
    return contract.get("mode") == "module_analysis" and contract.get("analysis_depth") == "complete"


def _run_coverage_judge(run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    from runtime import coverage_judge, data_runtime

    analysis_path = _analysis_model_path(run_dir)
    report_path = _fixed_audit_model(run_dir)
    ledger_path = run_dir / "internal" / "risk-ledger.json"
    analysis = _validate_analysis_model(data_runtime.read_json(analysis_path), contract, run_dir.name)
    report = _assert_report_contract_and_sections(run_dir, data_runtime.read_json(report_path))
    ledger = data_runtime.read_json(ledger_path)
    validate(ledger, "risk-ledger.schema.json")
    judged = coverage_judge.judge(analysis, report, ledger)
    payload = {
        "artifact_type": "coverage_judge", "schema_version": "1.0", "run_id": run_dir.name,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "analysis_artifact": _binding(analysis_path, ANALYSIS_MODEL_RELATIVE),
        "report_artifact": _binding(report_path, AUDITED_MODEL_RELATIVE),
        "risk_ledger_artifact": _binding(ledger_path, "internal/risk-ledger.json"),
        "verdict": judged["verdict"], "checks": judged["checks"],
    }
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
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RunCtlError(f"Coverage Judge 的 {key} 已过期，必须重新执行")
    if payload.get("verdict") != "PASS":
        raise RunCtlError("独立 Coverage Judge 未通过，禁止提交 auditor 或完成 Run")
    return {"path": COVERAGE_JUDGE_RELATIVE, "sha256": _sha256_file(path), "verdict": "PASS"}


def _invalidate_fixed_artifact(path: Path) -> None:
    if path.is_symlink():
        raise RunCtlError(f"拒绝删除符号链接工件: {path}")
    if path.exists():
        if not path.is_file():
            raise RunCtlError(f"固定工件不是普通文件: {path}")
        path.unlink()
'''
runctl = replace_once(runctl, '\ndef stage_analysis_v2(args: argparse.Namespace) -> None:\n', helpers + '\n\ndef stage_analysis_v2(args: argparse.Namespace) -> None:\n', "judge helpers")

runctl = replace_once(
    runctl,
    '    target = _analysis_model_path(run_dir)\n    data_runtime.atomic_write_json(target, normalized)\n',
    '    target = _analysis_model_path(run_dir)\n'
    '    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))\n'
    '    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))\n'
    '    data_runtime.atomic_write_json(target, normalized)\n',
    "analysis invalidation",
)
runctl = replace_once(
    runctl,
    '    target = _fixed_audit_model(run_dir)\n    data_runtime.atomic_write_json(target, model)\n    digest = _sha256_file(target)\n'
    '    data_runtime.set_run_state(root, args.run_id, "reviewing", "报告模型已实际落盘，等待独立审计")\n'
    '    print(json.dumps({"run_id": args.run_id, "report_model": str(target),\n'
    '                      "audited_artifact": AUDITED_MODEL_RELATIVE, "sha256": digest,\n'
    '                      "next_step": "audit"}, ensure_ascii=False))\n',
    '    target = _fixed_audit_model(run_dir)\n'
    '    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))\n'
    '    data_runtime.atomic_write_json(target, model)\n'
    '    digest = _sha256_file(target)\n'
    '    judge = _run_coverage_judge(run_dir, contract) if _judge_required(contract) else None\n'
    '    if judge is not None and judge["verdict"] != "PASS":\n'
    '        failed = [name for name, check in judge["checks"].items() if check["verdict"] != "PASS"]\n'
    '        raise RunCtlError("独立 Coverage Judge 未通过: " + ", ".join(failed))\n'
    '    data_runtime.set_run_state(root, args.run_id, "reviewing", "报告模型和独立覆盖审查已落盘，等待 auditor")\n'
    '    print(json.dumps({"run_id": args.run_id, "report_model": str(target),\n'
    '                      "audited_artifact": AUDITED_MODEL_RELATIVE, "sha256": digest,\n'
    '                      "coverage_judge": str(_coverage_judge_path(run_dir)) if judge is not None else None,\n'
    '                      "next_step": "audit"}, ensure_ascii=False))\n',
    "automatic judge",
)

judge_command = r'''

def judge_analysis_v2(args: argparse.Namespace) -> None:
    """Re-run the independent deterministic judge from fixed Run artifacts."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可重新执行 Coverage Judge")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    if not _judge_required(contract):
        raise RunCtlError("judge-analysis-v2 仅用于完整型模块分析")
    payload = _run_coverage_judge(run_dir, contract)
    data_runtime.set_run_state(root, args.run_id, "reviewing", f"独立 Coverage Judge：{payload['verdict']}")
    if payload["verdict"] != "PASS":
        failed = [name for name, check in payload["checks"].items() if check["verdict"] != "PASS"]
        raise RunCtlError("独立 Coverage Judge 未通过: " + ", ".join(failed))
    print(json.dumps({"run_id": args.run_id, "verdict": "PASS", "judge": str(_coverage_judge_path(run_dir)),
                      "analysis_artifact": payload["analysis_artifact"], "report_artifact": payload["report_artifact"]},
                     ensure_ascii=False))
'''
runctl = replace_once(runctl, '\ndef apply_audit_v2(args: argparse.Namespace) -> None:\n', judge_command + '\n\ndef apply_audit_v2(args: argparse.Namespace) -> None:\n', "judge command")
runctl = replace_once(
    runctl,
    '    report_model = _assert_report_contract_and_sections(run_dir, read_json(Path(audited_model["path"])))\n'
    '    _assert_report_gap_binding(report_model, snapshot_gaps)\n',
    '    report_model = _assert_report_contract_and_sections(run_dir, read_json(Path(audited_model["path"])))\n'
    '    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))\n'
    '    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))\n'
    '    _assert_report_gap_binding(report_model, snapshot_gaps)\n',
    "audit requires judge",
)
runctl = replace_once(
    runctl,
    '    snapshot_gaps = _assert_mr_snapshot_binding(root, run_dir)\n'
    '    if manifest.get("audit", {}).get("status") != "PASS":\n',
    '    snapshot_gaps = _assert_mr_snapshot_binding(root, run_dir)\n'
    '    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))\n'
    '    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))\n'
    '    if manifest.get("audit", {}).get("status") != "PASS":\n',
    "finalize requires judge",
)
runctl = replace_once(
    runctl,
    '    analysis2 = sub.add_parser("stage-analysis-v2", help="校验并实际落盘完整分析模型")\n',
    '    judge2 = sub.add_parser("judge-analysis-v2", help="独立核对完整分析、测试追溯与报告投影")\n'
    '    judge2.add_argument("--run-id", required=True)\n'
    '    judge2.add_argument("--root")\n'
    '    judge2.set_defaults(func=judge_analysis_v2)\n'
    '    analysis2 = sub.add_parser("stage-analysis-v2", help="校验并实际落盘完整分析模型")\n',
    "judge parser",
)
write("runtime/runctl.py", runctl)

agent = read(".opencode/agents/pangea-test.md")
old = '完成全部分析阶段后，完整型模块分析必须先调用 `runctl stage-analysis-v2`，由运行时校验并写入 `pangea-data/runs/<run-id>/internal/analysis-model.json`。随后调用 `runctl stage-report-v2`；运行时会把报告模型绑定到该分析模型的 SHA-256。没有有效分析模型时不得进入审计。只能使用命令返回的固定路径和哈希；不得用聊天总结或阶段套话代替分析工件。 对完整型模块分析，`stage-report-v2` 会忽略草稿中手工编写的代码地图、流程、分支、场景和用例，改由固定分析模型确定性投影，并把全部开发 Flow Card、状态/资源/并发、错误传播、场景推导、SFMEA、测试流程、追溯和 Coverage disposition 写入正式报告。不得在投影后手工删减。'
new = old + ' `stage-report-v2` 随后必须运行独立 Coverage Judge，并写入 `internal/coverage-judge.json`。Judge 独立比较入口、Flow、模型、场景候选、SFMEA、测试流程、用例、风险和报告投影；只有 Judge PASS 才能把报告交给 auditor。Producer 的“已完成”文字不得作为 Judge 证据。'
agent = replace_once(agent, old, new, "agent judge contract")
write(".opencode/agents/pangea-test.md", agent)

module = read(".opencode/commands/module-analysis.md")
needle = '完整型的代码地图、Flow、分支、场景、用例和全部深度章节由运行时从固定分析模型确定性覆盖生成，Agent 不得手工压缩或删减。'
module = replace_once(module, needle, needle + ' `stage-report-v2` 会自动执行独立 Coverage Judge；也可用 `python3 runtime/runctl.py judge-analysis-v2 --run-id <Run ID>` 重跑。Judge 非 PASS 时禁止调用 auditor。', "module judge")
write(".opencode/commands/module-analysis.md", module)

auditor = read(".opencode/agents/auditor.md")
auditor = auditor.replace(
    '输入为任务契约、固定分析模型、风险卡、代码证据、报告模型，以及两个固定工件的绑定。',
    '输入为任务契约、固定分析模型、独立 Coverage Judge 工件、风险卡、代码证据、报告模型，以及固定工件绑定。Coverage Judge 必须先 PASS，但你仍需独立审阅内容，不能照抄 Judge 结论。',
)
write(".opencode/agents/auditor.md", auditor)

skill = read(".opencode/skills/analysis-depth-contract/SKILL.md")
skill += '''\n\n## 独立 Coverage Judge\n\n固定分析模型和确定性报告投影完成后，运行时必须生成 `internal/coverage-judge.json`。Judge 独立核对入口与 Flow、全部 disposition、场景候选、SFMEA、测试流程、测试用例、风险映射、Oracle、追溯和报告投影。Judge 工件绑定 analysis-model、report-model 和 risk-ledger 的 SHA-256；任何一个变化都会使 Judge 过期。只有 Judge `PASS` 才允许提交 auditor 或完成 Run。\n'''
write(".opencode/skills/analysis-depth-contract/SKILL.md", skill)

test = r'''from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_analysis_report_projection import AnalysisReportProjectionTests

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"


class CoverageJudgeTests(unittest.TestCase):
    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, text=True, capture_output=True, check=False)

    def cli(self, *args: str) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    def stage_report(self, root: Path) -> tuple[Path, dict]:
        helper = AnalysisReportProjectionTests()
        helper.cli_result = self.cli_result
        helper.cli = self.cli
        run_dir, risk = helper.prepare(root)
        contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
        draft = {"title": "独立覆盖审查报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                 "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
        path = root / "draft.json"; path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
        staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
        return run_dir, staged

    def test_stage_report_writes_passed_independent_judge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, staged = self.stage_report(root)
            judge_path = run_dir / "internal/coverage-judge.json"
            judge = json.loads(judge_path.read_text(encoding="utf-8"))
            self.assertEqual("PASS", judge["verdict"])
            self.assertTrue(all(check["verdict"] == "PASS" for check in judge["checks"].values()))
            self.assertEqual(hashlib.sha256((run_dir / "internal/analysis-model.json").read_bytes()).hexdigest(),
                             judge["analysis_artifact"]["sha256"])
            self.assertEqual(str(judge_path), staged["coverage_judge"])

    def test_unknown_coverage_target_blocks_report_before_auditor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            helper = AnalysisReportProjectionTests(); helper.cli_result = self.cli_result; helper.cli = self.cli
            run_dir, risk = helper.prepare(root)
            analysis_path = run_dir / "internal/analysis-model.json"
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            analysis["coverage_dispositions"][0]["covered_by"] = ["TC-UNKNOWN"]
            source = root / "bad-analysis.json"; source.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
            self.cli("stage-analysis-v2", "--root", str(root), "--run-id", "depth", "--file", str(source))
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            draft_path = root / "draft.json"; draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(draft_path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("Coverage Judge 未通过", rejected.stderr)
            judge = json.loads((run_dir / "internal/coverage-judge.json").read_text(encoding="utf-8"))
            self.assertEqual("FAIL", judge["verdict"])
            self.assertTrue(judge["checks"]["breadth_disposition"]["findings"])

    def test_report_change_expires_judge_even_when_auditor_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, _ = self.stage_report(root)
            report_path = run_dir / "internal/report-model.json"
            report = json.loads(report_path.read_text(encoding="utf-8")); report["summary"] = "Judge 之后被修改"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0", "audited_artifact": "internal/report-model.json",
                       "audited_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(), "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            path = root / "audit.json"; path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("apply-audit-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("已过期", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_coverage_judge.py", test)
