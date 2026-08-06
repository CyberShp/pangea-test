from __future__ import annotations

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


analysis_reporting = r'''"""Deterministic projection of a validated analysis model into formal reports."""
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
'''
write("runtime/analysis_reporting.py", analysis_reporting)

runctl = read("runtime/runctl.py")
old = '''    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))
    if binding is not None and model.get("analysis_artifact") != binding:
        raise RunCtlError("report-model 未精确绑定当前固定分析模型")
    return model
'''
new = '''    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))
    if binding is not None:
        if model.get("analysis_artifact") != binding:
            raise RunCtlError("report-model 未精确绑定当前固定分析模型")
        from runtime import analysis_reporting
        try:
            analysis_reporting.assert_projection(model, data_runtime.read_json(_analysis_model_path(run_dir)))
        except ValueError as exc:
            raise RunCtlError(str(exc)) from exc
    return model
'''
runctl = replace_once(runctl, old, new, "verify report projection")
old = '''    if not isinstance(model, dict):
        raise RunCtlError("报告模型必须是 JSON 对象")
    if analysis_binding is not None:
        model["analysis_artifact"] = analysis_binding
    model = _assert_report_contract_and_sections(run_dir, model)
'''
new = '''    if not isinstance(model, dict):
        raise RunCtlError("报告模型必须是 JSON 对象")
    if analysis_binding is not None:
        from runtime import analysis_reporting
        model = analysis_reporting.apply_projection(model, data_runtime.read_json(_analysis_model_path(run_dir)))
        model["analysis_artifact"] = analysis_binding
    model = _assert_report_contract_and_sections(run_dir, model)
'''
runctl = replace_once(runctl, old, new, "apply report projection")
write("runtime/runctl.py", runctl)

reporting = read("runtime/reporting.py")
old = '''def validate_model(model: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_model(model)
    _test_text(normalized["title"], "报告标题")
'''
new = '''def validate_model(model: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_model(model)
    if normalized.get("analysis_artifact") is not None:
        from runtime import analysis_reporting
        try:
            analysis_reporting.validate_details(normalized.get("analysis_details"))
        except ValueError as exc:
            raise ReportError(str(exc)) from exc
    _test_text(normalized["title"], "报告标题")
'''
reporting = replace_once(reporting, old, new, "validate analysis details")
old = '''    out += [f"## 10. {SECTION_TITLES[9]}", "", "### 未闭环项", _bullet_text(model["unresolved"]), "", "### 下一步建议", _bullet_text(model["next_steps"]), ""]
    return "\\n".join(out)
'''
new = '''    out += [f"## 10. {SECTION_TITLES[9]}", "", "### 未闭环项", _bullet_text(model["unresolved"]), "", "### 下一步建议", _bullet_text(model["next_steps"]), ""]
    if model.get("analysis_details") is not None:
        from runtime import analysis_reporting
        out += [analysis_reporting.markdown_sections(model["analysis_details"], 11)]
    return "\\n".join(out)
'''
reporting = replace_once(reporting, old, new, "markdown complete sections")
old = '''        _html_next(model),
    ]
    return f\'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(str(model['title']))}</title>
'''
new = '''        _html_next(model),
    ]
    if model.get("analysis_details") is not None:
        from runtime import analysis_reporting
        sections.append(analysis_reporting.html_sections(model["analysis_details"], 11))
    return f\'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(str(model['title']))}</title>
'''
reporting = replace_once(reporting, old, new, "html complete sections")
write("runtime/reporting.py", reporting)

agent = read(".opencode/agents/pangea-test.md")
old = '完成全部分析阶段后，完整型模块分析必须先调用 `runctl stage-analysis-v2`，由运行时校验并写入 `pangea-data/runs/<run-id>/internal/analysis-model.json`。随后调用 `runctl stage-report-v2`；运行时会把报告模型绑定到该分析模型的 SHA-256。没有有效分析模型时不得进入审计。只能使用命令返回的固定路径和哈希；不得用聊天总结或阶段套话代替分析工件。'
new = old + ' 对完整型模块分析，`stage-report-v2` 会忽略草稿中手工编写的代码地图、流程、分支、场景和用例，改由固定分析模型确定性投影，并把全部开发 Flow Card、状态/资源/并发、错误传播、场景推导、SFMEA、测试流程、追溯和 Coverage disposition 写入正式报告。不得在投影后手工删减。'
agent = replace_once(agent, old, new, "agent deterministic report")
write(".opencode/agents/pangea-test.md", agent)

module = read(".opencode/commands/module-analysis.md")
needle = '然后进入审计门禁：主 Agent 调用 `python3 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <完整报告模型JSON>`，'
replacement = '然后进入审计门禁：主 Agent 调用 `python3 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <报告外壳JSON>`；完整型的代码地图、Flow、分支、场景、用例和全部深度章节由运行时从固定分析模型确定性覆盖生成，Agent 不得手工压缩或删减。'
module = replace_once(module, needle, replacement, "module deterministic report")
write(".opencode/commands/module-analysis.md", module)

skill = read(".opencode/skills/report-contract/SKILL.md")
skill += '''\n\n## 完整型分析报告投影\n\n完整型模块分析的正式报告不是主 Agent 手工摘要。`stage-report-v2` 必须从固定 `internal/analysis-model.json` 确定性投影代码地图、完整 Flow Card、分支、状态、资源、并发、错误传播、场景候选、SFMEA、黑盒测试流程、用例、追溯和 Coverage disposition。报告模型保存 `analysis_artifact` 路径与 SHA-256，并保存与固定分析模型逐字段一致的 `analysis_details`；任何删减或篡改都会使审计和完成失败。\n'''
write(".opencode/skills/report-contract/SKILL.md", skill)

# Tests
report_test = r'''from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime
from tests.test_analysis_depth_contract import AnalysisDepthContractTests, DFX

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"


class AnalysisReportProjectionTests(unittest.TestCase):
    def cli_result(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(RUNCTL), *args], cwd=ROOT, text=True, capture_output=True, check=False)

    def cli(self, *args: str) -> dict:
        result = self.cli_result(*args)
        self.assertEqual(0, result.returncode, result.stderr or result.stdout)
        return json.loads(result.stdout)

    @staticmethod
    def risk() -> dict:
        return {"artifact_type": "risk_card", "schema_version": "1.0", "risk_id": "R-1",
                "title": "错误后状态残留", "dfx": ["功能与状态"], "severity": "High", "confidence": "high",
                "trigger": "先发送非法请求", "propagation": "错误路径未恢复状态", "external_impact": "后续正常请求失败",
                "observation": "返回码、日志和后续业务", "recovery": "修正请求后业务应恢复",
                "translation_status": "Blackbox-ready", "test_explanation": "验证错误不影响后续正常业务。",
                "instrumentation_request": None, "evidence": [{"path": "driver.c", "line": 1, "fact": "error path"}],
                "status": "open"}

    def prepare(self, root: Path) -> tuple[Path, dict]:
        AnalysisDepthContractTests.repository(root)
        created = self.cli("create-v2", "--root", str(root), "--scenario", "module-analysis", "--target", "driver",
                           "--repository", "driver", "--run-id", "depth", "--analysis-depth", "complete")
        run_dir = Path(created["run_dir"])
        AnalysisDepthContractTests.complete_checkpoints(root, "depth")
        risk = self.risk(); data_runtime.upsert_risk(root, "depth", risk)
        analysis = AnalysisDepthContractTests.model(run_dir)
        path = root / "analysis.json"; path.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        self.cli("stage-analysis-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
        return run_dir, risk

    def test_stage_report_overwrites_shallow_sections_from_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, risk = self.prepare(root)
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "深度报告", "summary": "固定分析模型生成。", "task_contract": contract,
                     "code_map": [{"title": "浅摘要", "test_explanation": "只有一句。", "source_evidence": "x"}],
                     "flows": [{"title": "浅流程", "test_explanation": "只有一句。", "steps": ["请求"], "source_evidence": "x"}],
                     "branches": [{"title": "浅分支", "test_explanation": "只有一句。", "source_evidence": "x"}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            draft_path = root / "draft.json"; draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(draft_path))
            model = json.loads(Path(staged["report_model"]).read_text(encoding="utf-8"))
            self.assertEqual("EP-1", model["code_map"][0]["analysis_id"])
            self.assertEqual("FLOW-1", model["flows"][0]["analysis_id"])
            self.assertIn("resource_lifecycle", model["flows"][0]["developer_detail"])
            self.assertEqual("SC-1", model["scenarios"][0]["scenario_id"])
            self.assertEqual("TF-1", model["analysis_details"]["test_flows"][0]["test_flow_id"])
            analysis_path = run_dir / "internal/analysis-model.json"
            self.assertEqual({"path": "internal/analysis-model.json", "sha256": hashlib.sha256(analysis_path.read_bytes()).hexdigest()},
                             model["analysis_artifact"])

    def test_render_contains_every_deep_analysis_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, risk = self.prepare(root)
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "深度报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            path = root / "draft.json"; path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
            model_path = Path(staged["report_model"])
            # Rendering is exercised directly before audit; finalization has the same renderer.
            from runtime import reporting
            md, page = reporting.render(json.loads(model_path.read_text(encoding="utf-8")))
            for token in ("FLOW-1", "STATE-1", "RES-1", "CON-1", "ERR-1", "CAND-1", "SF-1", "TF-1", "TR-1"):
                self.assertIn(token, md)
                self.assertIn(token, page)
            for title in ("开发实现讲解与完整 Flow Card", "状态、资源与并发模型", "场景推导与 SFMEA",
                          "黑盒测试流程", "追溯矩阵与 Coverage disposition"):
                self.assertIn(title, md)
                self.assertIn(title, page)

    def test_tampered_projection_is_rejected_even_with_matching_report_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir, risk = self.prepare(root)
            contract = json.loads((run_dir / "internal/task-contract.json").read_text(encoding="utf-8"))
            draft = {"title": "深度报告", "task_contract": contract, "code_map": [{}], "flows": [{}], "branches": [{}],
                     "risks": [risk], "scenarios": [], "test_cases": [], "unresolved": [], "next_steps": []}
            path = root / "draft.json"; path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            staged = self.cli("stage-report-v2", "--root", str(root), "--run-id", "depth", "--file", str(path))
            model_path = Path(staged["report_model"])
            model = json.loads(model_path.read_text(encoding="utf-8")); model["analysis_details"]["flows"] = []
            model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
            digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
            opinion = {"artifact_type": "audit_opinion", "schema_version": "2.0", "audited_artifact": "internal/report-model.json",
                       "audited_sha256": digest, "verdict": "PASS", "required_actions": [],
                       "checks": {name: {"verdict": "PASS", "violations": [], "gaps": []}
                                  for name in ("traceability", "blackbox_executability", "coverage", "format_compliance")}}
            opinion_path = root / "audit.json"; opinion_path.write_text(json.dumps(opinion, ensure_ascii=False), encoding="utf-8")
            rejected = self.cli_result("apply-audit-v2", "--root", str(root), "--run-id", "depth", "--file", str(opinion_path))
            self.assertEqual(2, rejected.returncode)
            self.assertIn("完整消费固定分析模型", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
'''
write("tests/test_analysis_report_projection.py", report_test)
