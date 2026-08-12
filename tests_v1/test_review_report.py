from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from runtime import analysis, engine, model, report, review
from tests_v1.test_analysis import _native, _prepared


def _module_value(context: dict, *, drop_ids: list[str] | None = None) -> dict:
    dropped = drop_ids or []
    return {
        "type": "module_composition", "run_id": context["run_id"], "source_revision": context["source_revision"],
        "summary": ["模块入口语义已合并。"], "drop_ids": dropped, "relationship_updates": [], "field_updates": [],
        "decisions": [{"object_ids": dropped, "reason": "全局证据推翻该局部对象。"}] if dropped else [],
    }


def _dfx_value(context: dict, *, repeated: bool = False, risk_finding: bool = False) -> dict:
    behavior_id = next(row["id"] for row in context["semantics"]["behaviors"])
    evidence = [
        {**row, "fact": "模块行为由该源码范围直接支持。"}
        for row in context["semantics"]["behaviors"][0]["evidence"]
    ]
    assessments = []
    for index, dimension in enumerate(model.DFX_DIMENSIONS):
        findings = []
        if index == 0 and risk_finding:
            findings.append({
                "id": "DFX-RC01", "type": "risk", "title": "入口失败向调用者传播",
                "description": "非零退出状态会成为外部失败结果。", "related_ids": [behavior_id], "evidence": evidence,
                "risk_candidate": {
                    "condition": "入口返回非零", "propagation": "退出状态传递", "effect": "调用者判定失败",
                    "observation": "读取退出码", "recovery": "修正输入后重试", "confidence": "medium",
                    "missing_evidence": [],
                },
            })
        assessments.append({
            "dimension": dimension,
            "summary": "重复模板" if repeated else f"{dimension}：当前模块入口证据的模块级复核结果。",
            "findings": findings,
        })
    return {"type": "dfx_result", "run_id": context["run_id"], "semantic_revision": context["semantic_revision"], "assessments": assessments}


def _empty_risk(context: dict) -> dict:
    return {
        "type": "risk_result", "run_id": context["run_id"], "semantic_revision": context["semantic_revision"],
        "risk_decisions": [], "risks": [], "sfmea": [],
    }


def _adjudication(context: dict, *, status: str = "confirmed") -> dict:
    return {
        "type": "risk_adjudication_result", "run_id": context["run_id"],
        "semantic_revision": context["semantic_revision"],
        "decisions": [
            {"candidate_id": candidate_id, "status": status, "reason": "源码因果链足以完成候选裁决。"}
            for candidate_id in context["candidate_ids"]
        ],
    }


def _risk_synthesis(context: dict) -> dict:
    if not context["confirmed_candidates"]:
        return {
            "type": "risk_synthesis_result", "run_id": context["run_id"],
            "semantic_revision": context["semantic_revision"], "candidate_risk_mappings": [],
            "risks": [], "sfmea": [],
        }
    candidate = context["confirmed_candidates"][0]
    risk_id = "R-ENTRY-001"
    return {
        "type": "risk_synthesis_result", "run_id": context["run_id"],
        "semantic_revision": context["semantic_revision"],
        "candidate_risk_mappings": [
            {"candidate_id": row["candidate_id"], "risk_id": risk_id}
            for row in context["confirmed_candidates"]
        ],
        "risks": [{
            "id": risk_id, "title": candidate["title"], "dfx_dimensions": candidate["dfx_dimensions"] or ["功能与状态"],
            "severity": "Low", "confidence": candidate["confidence"], "condition": candidate["condition"],
            "propagation": candidate["propagation"], "effect": candidate["effect"],
            "observation": candidate["observation"], "recovery": candidate["recovery"],
            "testability": "blocked", "missing_evidence": ["需要可产生非零返回的关联实现或控制接口"],
            "related_ids": candidate["related_ids"], "evidence": candidate["evidence"],
        }],
        "sfmea": [{
            "id": "SFMEA-001", "failure_mode": "入口返回失败", "mechanism": "返回状态传播",
            "effect": "调用者判定启动失败", "severity": 3, "occurrence": 2, "detection": 2,
            "observations": ["进程退出码"], "recommendation": "补充可控制返回状态的集成环境",
            "risk_ids": [risk_id], "evidence": candidate["evidence"],
        }],
    }


def _blocked_risk(context: dict) -> dict:
    candidate = context["candidates"][0]
    risk_id = "R-ENTRY-001"
    return {
        "type": "risk_result", "run_id": context["run_id"], "semantic_revision": context["semantic_revision"],
        "risk_decisions": [{"candidate_id": candidate["candidate_id"], "status": "confirmed", "reason": "因果链有源码证据。", "risk_id": risk_id}],
        "risks": [{
            "id": risk_id, "title": candidate["title"], "dfx_dimensions": ["功能与状态"], "severity": "Low",
            "confidence": candidate["confidence"], "condition": candidate["condition"], "propagation": candidate["propagation"],
            "effect": candidate["effect"], "observation": candidate["observation"], "recovery": candidate["recovery"],
            "testability": "blocked", "missing_evidence": ["需要可产生非零返回的关联实现或控制接口"],
            "related_ids": candidate["related_ids"], "evidence": candidate["evidence"],
        }],
        "sfmea": [{
            "id": "SFMEA-001", "failure_mode": "入口返回失败", "mechanism": "返回状态传播",
            "effect": "调用者判定启动失败", "severity": 3, "occurrence": 2, "detection": 2,
            "observations": ["进程退出码"], "recommendation": "补充可控制返回状态的集成环境",
            "risk_ids": [risk_id], "evidence": candidate["evidence"],
        }],
    }


def _empty_test(context: dict) -> dict:
    if context.get("batch_kind") == "behavior_only":
        behavior = context["semantics"]["behaviors"][0]
        prefix = context["output_id_prefix"]
        scenario_id = prefix + "SC-001"
        return {
            "type": "test_design_result", "run_id": context["run_id"],
            "semantic_revision": context["semantic_revision"],
            "scenarios": [{
                "id": scenario_id, "title": "验证冻结行为", "risk_ids": [],
                "semantic_ids": [behavior["id"]], "parameters": {"mode": ["default"]},
                "preconditions": ["目标构建产物可用"], "action": "执行该行为的主路径",
                "expected": "观察到冻结语义描述的结果", "observation": "记录输出和退出状态",
                "evidence": behavior["evidence"],
            }],
            "test_cases": [{
                "id": prefix + "TC-001", "title": "冻结行为主路径", "scenario_id": scenario_id,
                "risk_ids": [], "semantic_ids": [behavior["id"]], "parameters": {"mode": "default"},
                "preconditions": ["目标构建产物可用"], "steps": ["执行主路径"],
                "expected": ["行为结果符合冻结语义"], "observations": ["记录输出和退出状态"],
                "cleanup": ["清理本次测试产生的临时状态"],
            }],
        }
    return {
        "type": "test_design_result", "run_id": context["run_id"],
        "semantic_revision": context["semantic_revision"], "scenarios": [], "test_cases": [],
    }


def _composed(root: Path, *, candidate: bool = False, repeated_dfx: bool = False, dfx_risk: bool = False) -> dict:
    _context, fragment = _prepared(root, with_candidate=candidate)
    analysis.analyze(root, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(fragment), ""))
    return review.compose(
        root, "r1",
        runner=lambda _r, context, **k: subprocess.CompletedProcess([], 0, _native(_module_value(context)), ""),
        dfx_runner=lambda _r, context, **k: subprocess.CompletedProcess([], 0, _native(_dfx_value(context, repeated=repeated_dfx, risk_finding=dfx_risk)), ""),
        adjudication_runner=lambda _r, context, **k: subprocess.CompletedProcess([], 0, _native(_adjudication(context)), ""),
        risk_runner=lambda _r, context, **k: subprocess.CompletedProcess([], 0, _native(_risk_synthesis(context)), ""),
        design_runner=lambda _r, context, **k: subprocess.CompletedProcess([], 0, _native(_empty_test(context)), ""),
    )


def _review_value(context: dict, verdict: str = "pass") -> dict:
    return {
        "type": "review_result", "run_id": context["run_id"], "analysis_revision": context["analysis_revision"],
        "verdict": verdict, "metrics": context["expected_metrics"],
        "summary": "证据、模块语义、DFX、风险判定与测试边界一致。", "findings": [],
    }


def test_pipeline_runs_module_then_one_dfx_then_design(tmp_path: Path) -> None:
    value = _composed(tmp_path)
    assert value["dfx"][0]["findings"] == []
    assert len(value["scenarios"]) == 1 and len(value["test_cases"]) == 1
    receipts = sorted((tmp_path / "pangea-data/runs/r1/executions").glob("*-attempt-*.json"))
    assert [path.name for path in receipts] == [
        "S001-attempt-001.json", "compose-attempt-001.json", "dfx-attempt-001.json",
        "risk-synthesis-attempt-001.json", "test-design-001-attempt-001.json",
    ]
    assert engine.load_run(tmp_path, "r1")["state"] == "composed"


def test_global_role_prompt_requires_strict_json_and_dfx_array_type() -> None:
    context = {"run_id": "r1", "contract": model.DFX_CONTRACT}
    prompt = review._role_prompt(context, "dfx-analyst")
    assert "parses once as JSON" in prompt
    assert "missing_evidence is always a JSON array" in prompt
    risk_prompt = review._role_prompt({"run_id": "r1", "candidate_ids": ["C1"], "contract": model.RISK_ADJUDICATION_CONTRACT}, "risk-adjudicator")
    assert "exactly one decision for every ID" in risk_prompt
    design_prompt = review._role_prompt({"run_id": "r1", "output_id_prefix": "TD001-", "contract": model.TEST_DESIGN_CONTRACT}, "test-designer")
    assert "output_id_prefix" in design_prompt and "line_start, line_end and fact" in design_prompt
    assert "behavior_only" in design_prompt


def test_dfx_supports_zero_to_many_findings_and_exact_dimensions(tmp_path: Path) -> None:
    _context, fragment = _prepared(tmp_path)
    analysis.analyze(tmp_path, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(fragment), ""))
    captured = {}
    def dfx_runner(_r, context, **kwargs):
        value = _dfx_value(context, risk_finding=True)
        value["assessments"][0]["findings"].append({
            "id": "DFX-S01", "type": "strength", "title": "退出结果清晰", "description": "返回值直接可观测。",
            "related_ids": [context["semantic_ids"][0]], "evidence": [
                {**row, "fact": "该源码范围支持退出结果。"}
                for row in context["semantics"]["behaviors"][0]["evidence"]
            ],
            "risk_candidate": None,
        })
        captured["value"] = value
        return subprocess.CompletedProcess([], 0, _native(value), "")
    review.compose(
        tmp_path, "r1",
        runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_module_value(c)), ""),
        dfx_runner=dfx_runner,
        adjudication_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_adjudication(c, status="dismissed")), ""),
        risk_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_risk_synthesis(c)), ""),
        design_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_empty_test(c)), ""),
    )
    assert len(captured["value"]["assessments"][0]["findings"]) == 2
    assert all(not row["findings"] for row in captured["value"]["assessments"][1:])


def test_confirmed_blocked_risk_survives_without_test(tmp_path: Path) -> None:
    value = _composed(tmp_path, candidate=True)
    assert value["risks"][0]["testability"] == "blocked"
    assert all(value["risks"][0]["id"] not in row["risk_ids"] for row in value["test_cases"])
    assert value["sfmea"][0]["risk_ids"] == [value["risks"][0]["id"]]
    assert review.structural_findings(value) == []


def test_testable_risk_requires_derived_case_but_blocked_does_not(tmp_path: Path) -> None:
    value = _composed(tmp_path, candidate=True)
    test_context = engine.read_json(tmp_path / "pangea-data/runs/r1/test-design-context.json")
    risk = copy.deepcopy(test_context["risk_result"])
    context = {
        **test_context,
        "candidates": [{"candidate_id": row["candidate_id"]} for row in risk["risk_decisions"]],
    }
    design = {"type": "design_result", **{k: v for k, v in risk.items() if k != "type"}, "scenarios": [], "test_cases": []}
    design["risks"][0]["testability"] = "blackbox_ready"
    design["risks"][0]["missing_evidence"] = []
    with pytest.raises(model.ContractError, match="testable risks"):
        model.validate_design(design, context)
    assert model.validate_risk_result(risk, context)["risks"]


def test_composer_may_drop_disproved_behavior_with_decision(tmp_path: Path) -> None:
    context, fragment = _prepared(tmp_path)
    candidate = {"summary": [fragment["summary"]], "applied_skills": [row["name"] for row in fragment["applied_skills"]]}
    candidate.update({family: copy.deepcopy(fragment[family]) for family in model.SEMANTIC_FAMILIES})
    composition_context = {
        "run_id": "r1", "source_revision": context["revision"], "candidate": candidate,
        "sources": context["sources"], "contract": model.MODULE_CONTRACT,
    }
    behavior_id = fragment["behaviors"][0]["id"]
    value = _module_value(composition_context, drop_ids=[behavior_id])
    assert model.validate_module_composition(value, composition_context)["drop_ids"] == [behavior_id]


def test_causal_duplicate_candidates_share_adjudication_batch() -> None:
    base = {"condition": "A", "propagation": "B", "effect": "C", "related_ids": []}
    candidates = [
        {"candidate_id": "X", **base}, {"candidate_id": "Y", **base},
        {"candidate_id": "Z", "condition": "D", "propagation": "E", "effect": "F", "related_ids": []},
        {"candidate_id": "W", "condition": "G", "propagation": "H", "effect": "I", "related_ids": ["Z"]},
    ]
    batches = review._causal_candidate_batches(candidates)
    assert any({row["candidate_id"] for row in batch}.issuperset({"X", "Y"}) for batch in batches)
    assert any({row["candidate_id"] for row in batch}.issuperset({"Z", "W"}) for batch in batches)


def test_template_dfx_fails_deterministic_review(tmp_path: Path) -> None:
    value = _composed(tmp_path, repeated_dfx=True)
    findings = review.structural_findings(value)
    assert any(row["code"] == "template-dfx-summary" for row in findings)


def test_deterministic_review_flags_platform_and_spdk_test_prerequisites(tmp_path: Path) -> None:
    value = _composed(tmp_path)
    value["test_cases"] = [{
        "id": "TC-P", "risk_ids": [], "preconditions": ["可执行文件已构建"],
        "steps": ["执行 LD_PRELOAD=x.so MEMZONE_DUMP=1 ./app", "readlink /proc/1/fd/1"],
        "observations": ["记录输出"],
    }]
    codes = {row["code"] for row in review.structural_findings(value)}
    assert {
        "test-proc-platform-prerequisite", "test-ld-preload-platform-prerequisite",
        "test-spdk-readiness-prerequisite",
    }.issubset(codes)


def test_test_context_uses_compact_direct_semantic_projection(tmp_path: Path) -> None:
    value = _composed(tmp_path, candidate=True)
    context = engine.read_json(tmp_path / "pangea-data/runs/r1/test-design-context.json")
    rows = [row for family in context["semantics"].values() for row in family]
    assert rows and all(set(row) == {"id", "title", "facts", "evidence"} for row in rows)
    assert {row["id"] for row in rows}.issuperset({row["id"] for row in value["behaviors"]})


def test_composer_context_uses_compact_semantic_projection(tmp_path: Path) -> None:
    _composed(tmp_path, candidate=True)
    context = engine.read_json(tmp_path / "pangea-data/runs/r1/module-context.json")
    rows = [row for family in model.SEMANTIC_FAMILIES for row in context["candidate"][family]]
    assert rows and all(set(row) == {"id", "title", "facts", "related_ids", "evidence"} for row in rows)
    assert all(set(item) == {"path", "line_start", "line_end"} for row in rows for item in row["evidence"])


def test_composer_cannot_drop_required_semantics_as_downstream_concern(tmp_path: Path) -> None:
    context, fragment = _prepared(tmp_path, with_candidate=True)
    candidate_id = fragment["risk_candidates"][0]["id"]
    context = {
        "run_id": "r1", "source_revision": 1,
        "candidate": review._composition_candidate(review._combined_candidate([fragment])),
    }
    value = _module_value(context, drop_ids=[candidate_id])
    value["decisions"][0]["reason"] = "该候选应交给下游阶段，因此不纳入最终模块。"
    with pytest.raises(model.ContractError, match="downstream concern"):
        model.validate_module_composition(value, context)


def test_dfx_context_projects_every_semantic_object(tmp_path: Path) -> None:
    value = _composed(tmp_path, candidate=True)
    context = engine.read_json(tmp_path / "pangea-data/runs/r1/dfx-context.json")
    rows = [row for family in model.SEMANTIC_FAMILIES for row in context["semantics"][family]]
    assert len(rows) == sum(len(value[family]) for family in model.SEMANTIC_FAMILIES)
    assert all(set(row) == {"id", "title", "facts", "related_ids", "evidence"} for row in rows)
    assert all(set(item) == {"path", "line_start", "line_end", "fact"} for row in rows for item in row["evidence"])


def test_pass_review_publishes_deep_markdown_and_interactive_html(tmp_path: Path) -> None:
    value = _composed(tmp_path, candidate=True)
    reviewed = review.review(
        tmp_path, "r1",
        runner=lambda _r, context, **k: subprocess.CompletedProcess([], 0, _native(_review_value(context)), ""),
    )
    assert reviewed["verdict"] == "pass"
    paths = report.publish(tmp_path, "r1")
    markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
    html = Path(paths["html"]).read_text(encoding="utf-8")
    for heading in ("Flow", "Branch", "State", "Resource", "Concurrency", "Error Chain", "六维 DFX", "Canonical Risk", "SFMEA"):
        assert heading in markdown
    assert "testability=blocked" not in markdown
    assert "`blocked`" in markdown and "缺失证据" in markdown
    assert "confirmed=1" in markdown and "dismissed=0" in markdown
    assert 'id="q"' in html and 'id="kind"' in html and 'id="testability"' in html
    assert engine.load_run(tmp_path, "r1")["state"] == "completed"
    assert report.publish(tmp_path, "r1") == paths
    second = review.review(
        tmp_path, "r1",
        runner=lambda _r, context, **k: subprocess.CompletedProcess([], 0, _native(_review_value(context)), ""),
    )
    assert second["metrics"] == reviewed["metrics"] and engine.load_run(tmp_path, "r1")["state"] == "reviewing"
    report.publish(tmp_path, "r1")
    assert engine.load_run(tmp_path, "r1")["state"] == "completed"
