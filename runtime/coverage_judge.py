"""Independent deterministic coverage judge for complete module analysis."""
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
