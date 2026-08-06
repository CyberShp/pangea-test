"""Deterministic projection of a validated analysis model into formal reports."""
from __future__ import annotations

import copy
import html
from typing import Any


DETAIL_KEYS = (
    "analysis_depth", "source_commits", "evidence_consumption", "entrypoints", "flows", "branches",
    "states", "resources", "concurrency", "error_chains", "model_applicability", "scenario_candidates",
    "sfmea", "test_scenarios", "test_flows", "test_cases", "traceability", "coverage_dispositions",
    "depth_limitations", "unresolved",
)


def _text(value: Any) -> str:
    if value is None:
        return "未说明"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, list):
        return "、".join(_text(item) for item in value) if value else "无"
    if isinstance(value, dict):
        return "；".join(f"{key}: {_text(item)}" for key, item in value.items()) if value else "无"
    return str(value)


def _source_evidence(item: dict[str, Any]) -> Any:
    return copy.deepcopy(item.get("source_evidence", item.get("source_refs", [])))


def projection(analysis: dict[str, Any]) -> dict[str, Any]:
    """Return every report field owned by the fixed analysis model."""
    code_map = []
    for item in analysis["entrypoints"]:
        code_map.append({
            "analysis_id": item["entrypoint_id"],
            "title": f"{item['entrypoint_id']} {item['title']}",
            "test_explanation": (
                f"外部触发：{_text(item['external_trigger'])}；运行时注册：{_text(item['registration'])}；"
                f"前置状态：{_text(item['preconditions'])}；关联流程：{_text(item['flow_ids'])}；"
                f"处置：{item['status']}（{_text(item['disposition_reason'])}）。"
            ),
            "source_evidence": _source_evidence(item),
        })

    flows = []
    for item in analysis["flows"]:
        flows.append({
            "analysis_id": item["flow_id"],
            "title": f"{item['flow_id']} {item['title']}",
            "test_explanation": (
                f"外部触发：{_text(item['external_trigger'])}；注册入口：{_text(item['registration'])}；"
                f"前置：{_text(item['preconditions'])}；黑盒控制：{_text(item['blackbox_controls'])}；"
                f"独立判据：{_text(item['oracles'])}；处置：{item['status']}（{_text(item['disposition_reason'])}）。"
            ),
            "steps": copy.deepcopy(item["normal_path"]),
            "source_evidence": _source_evidence(item),
            "developer_detail": copy.deepcopy(item),
        })

    branches = []
    for item in analysis["branches"]:
        branches.append({
            "analysis_id": item["branch_id"],
            "title": f"{item['branch_id']} {item['condition']}",
            "test_explanation": (
                f"所属流程：{item['flow_id']}；条件成立：{_text(item['true_path'])}；条件不成立：{_text(item['false_path'])}；"
                f"外部影响：{_text(item['external_effect'])}；构造：{_text(item['controllability'])}；"
                f"观测：{_text(item['observability'])}；处置：{item['status']}（{_text(item['disposition_reason'])}）。"
            ),
            "source_evidence": _source_evidence(item),
            "developer_detail": copy.deepcopy(item),
        })

    scenarios = []
    for item in analysis["test_scenarios"]:
        scenarios.append({
            "scenario_id": item["scenario_id"], "title": item["title"],
            "risk_ids": copy.deepcopy(item["risk_ids"]),
            "description": (
                f"来源候选：{_text(item['source_candidate_ids'])}；前置条件：{_text(item['preconditions'])}；"
                f"观测：{_text(item['observations'])}；清理：{_text(item['cleanup'])}。"
            ),
            "trigger": _text(item["trigger"]), "expected": _text(item["expected"]),
            "source_candidate_ids": copy.deepcopy(item["source_candidate_ids"]),
            "observations": copy.deepcopy(item["observations"]), "cleanup": copy.deepcopy(item["cleanup"]),
        })

    cases = []
    for item in analysis["test_cases"]:
        cases.append({
            "case_id": item["case_id"], "title": item["title"],
            "scenario_id": item["scenario_id"], "risk_ids": copy.deepcopy(item["risk_ids"]),
            "preconditions": copy.deepcopy(item["preconditions"]), "steps": copy.deepcopy(item["steps"]),
            "expected": copy.deepcopy(item["expected"]), "observation": copy.deepcopy(item["observation"]),
            "cleanup": copy.deepcopy(item["cleanup"]), "source_refs": copy.deepcopy(item["source_refs"]),
        })

    details = {key: copy.deepcopy(analysis[key]) for key in DETAIL_KEYS}
    return {"code_map": code_map, "flows": flows, "branches": branches,
            "scenarios": scenarios, "test_cases": cases, "analysis_details": details}


def apply_projection(report: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(report)
    result.update(projection(analysis))
    return result


def assert_projection(report: dict[str, Any], analysis: dict[str, Any]) -> None:
    expected = projection(analysis)
    mismatches = [key for key, value in expected.items() if report.get(key) != value]
    if mismatches:
        raise ValueError("报告没有完整消费固定分析模型: " + ", ".join(mismatches))


def validate_details(details: Any) -> dict[str, Any]:
    if not isinstance(details, dict):
        raise ValueError("analysis_details 必须是对象")
    missing = [key for key in DETAIL_KEYS if key not in details]
    if missing:
        raise ValueError("analysis_details 缺少字段: " + ", ".join(missing))
    for key in DETAIL_KEYS:
        if key in {"analysis_depth", "source_commits"}:
            continue
        if not isinstance(details[key], list):
            raise ValueError(f"analysis_details.{key} 必须是数组")
    return details


def _md_value(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_md_value(item) for item in value) if value else "无"
    if isinstance(value, dict):
        return "；".join(f"{key}={_md_value(item)}" for key, item in value.items()) if value else "无"
    return str(value) if value not in (None, "") else "未说明"


def _md_records(title: str, records: list[dict[str, Any]], id_fields: tuple[str, ...]) -> list[str]:
    out = [title, ""]
    if not records:
        return out + ["无。", ""]
    for index, item in enumerate(records, 1):
        identity = next((str(item[field]) for field in id_fields if item.get(field)), str(index))
        label = str(item.get("title", item.get("condition", item.get("failure_mode", identity))))
        out += [f"### {identity} {label}", ""]
        for key, value in item.items():
            if key in id_fields or key == "title":
                continue
            out.append(f"- **{key}**：{_md_value(value)}")
        out.append("")
    return out


def markdown_sections(details: dict[str, Any], start: int = 11) -> str:
    details = validate_details(details)
    n = start
    out: list[str] = []
    out += _md_records(f"## {n}. 输入材料消费与入口广度盘点", details["evidence_consumption"], ("evidence_id",)); n += 1
    out += _md_records(f"## {n}. 开发实现讲解与完整 Flow Card", details["flows"], ("flow_id",)); n += 1
    out += [f"## {n}. 状态、资源与并发模型", ""]
    out += _md_records("### 状态模型", details["states"], ("state_id",))
    out += _md_records("### 资源生命周期", details["resources"], ("resource_id",))
    out += _md_records("### 并发模型", details["concurrency"], ("concurrency_id",)); n += 1
    out += [f"## {n}. 分支处置与错误传播链", ""]
    out += _md_records("### 分支处置", details["branches"], ("branch_id",))
    out += _md_records("### 错误传播链", details["error_chains"], ("chain_id",)); n += 1
    out += [f"## {n}. 场景推导与 SFMEA", ""]
    out += _md_records("### 场景候选", details["scenario_candidates"], ("candidate_id",))
    out += _md_records("### SFMEA", details["sfmea"], ("sfmea_id",)); n += 1
    out += _md_records(f"## {n}. 黑盒测试流程", details["test_flows"], ("test_flow_id",)); n += 1
    out += [f"## {n}. 追溯矩阵与 Coverage disposition", ""]
    out += _md_records("### 追溯矩阵", details["traceability"], ("trace_id",))
    out += _md_records("### Coverage disposition", details["coverage_dispositions"], ("item_id",)); n += 1
    out += [f"## {n}. 分析深度边界与未闭环项", "",
            f"- **分析深度**：{_md_value(details['analysis_depth'])}",
            f"- **源码版本**：{_md_value(details['source_commits'])}",
            f"- **深度限制**：{_md_value(details['depth_limitations'])}",
            f"- **未闭环项**：{_md_value(details['unresolved'])}", ""]
    return "\n".join(out)


def _html_records(title: str, records: list[dict[str, Any]], id_fields: tuple[str, ...]) -> str:
    cards: list[str] = []
    for index, item in enumerate(records, 1):
        identity = next((str(item[field]) for field in id_fields if item.get(field)), str(index))
        label = str(item.get("title", item.get("condition", item.get("failure_mode", identity))))
        rows = "".join(
            f"<tr><th>{html.escape(str(key))}</th><td>{html.escape(_text(value))}</td></tr>"
            for key, value in item.items() if key not in id_fields and key != "title"
        )
        cards.append(f'<article id="analysis-{html.escape(identity)}"><h3>{html.escape(identity)} {html.escape(label)}</h3><table>{rows}</table></article>')
    return f"<h3>{html.escape(title)}</h3>" + ("".join(cards) or "<p>无。</p>")


def html_sections(details: dict[str, Any], start: int = 11) -> str:
    details = validate_details(details)
    n = start
    sections: list[str] = []
    sections.append(f'<section><h2>{n}. 输入材料消费与入口广度盘点</h2>{_html_records("输入材料消费", details["evidence_consumption"], ("evidence_id",))}{_html_records("入口清单", details["entrypoints"], ("entrypoint_id",))}{_html_records("DFX 适用性", details["model_applicability"], ("dfx",))}</section>'); n += 1
    sections.append(f'<section><h2>{n}. 开发实现讲解与完整 Flow Card</h2>{_html_records("Flow Card", details["flows"], ("flow_id",))}</section>'); n += 1
    sections.append(f'<section><h2>{n}. 状态、资源与并发模型</h2>{_html_records("状态模型", details["states"], ("state_id",))}{_html_records("资源生命周期", details["resources"], ("resource_id",))}{_html_records("并发模型", details["concurrency"], ("concurrency_id",))}</section>'); n += 1
    sections.append(f'<section><h2>{n}. 分支处置与错误传播链</h2>{_html_records("分支处置", details["branches"], ("branch_id",))}{_html_records("错误传播链", details["error_chains"], ("chain_id",))}</section>'); n += 1
    sections.append(f'<section><h2>{n}. 场景推导与 SFMEA</h2>{_html_records("场景候选", details["scenario_candidates"], ("candidate_id",))}{_html_records("SFMEA", details["sfmea"], ("sfmea_id",))}</section>'); n += 1
    sections.append(f'<section><h2>{n}. 黑盒测试流程</h2>{_html_records("测试流程", details["test_flows"], ("test_flow_id",))}</section>'); n += 1
    sections.append(f'<section><h2>{n}. 追溯矩阵与 Coverage disposition</h2>{_html_records("追溯矩阵", details["traceability"], ("trace_id",))}{_html_records("Coverage disposition", details["coverage_dispositions"], ("item_id",))}</section>'); n += 1
    boundary = "".join(f"<li><b>{html.escape(label)}</b>：{html.escape(_text(value))}</li>" for label, value in (
        ("分析深度", details["analysis_depth"]), ("源码版本", details["source_commits"]),
        ("深度限制", details["depth_limitations"]), ("未闭环项", details["unresolved"])))
    sections.append(f'<section><h2>{n}. 分析深度边界与未闭环项</h2><ul>{boundary}</ul></section>')
    return "".join(sections)
