from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from runtime import analysis, engine, model, source


def _native(value: dict, *, reason: str = "stop", text: str | None = None, tool: str | None = None) -> str:
    events = [{"type": "step_start", "sessionID": "ses-test", "part": {"type": "step-start"}}]
    if tool:
        events.append({"type": "tool_use", "sessionID": "ses-test", "part": {"tool": tool}})
    events += [
        {"type": "text", "sessionID": "ses-test", "part": {"text": json.dumps(value, ensure_ascii=False) if text is None else text}},
        {"type": "step_finish", "sessionID": "ses-test", "part": {
            "reason": reason,
            "tokens": {"input": 100, "output": 500, "reasoning": 50, "cache": {"read": 10, "write": 0}},
        }},
    ]
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in events) + "\n"


def _prepared(root: Path, *, with_candidate: bool = False) -> tuple[dict, dict]:
    repo = root / "pangea-data/repositories/spdk/app/iscsi_tgt"
    repo.mkdir(parents=True)
    (repo / "iscsi_tgt.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
    engine.create_run(root, "r1", repository="spdk", scope="app/iscsi_tgt", target="iscsi_tgt")
    engine.transition(root, "r1", "draft", "confirmed")
    source.prepare(root, "r1")
    plan = source.plan(root, "r1")
    context = engine.read_json(root / "pangea-data/runs/r1/contexts/S001.json")
    evidence = [{"path": "app/iscsi_tgt/iscsi_tgt.c", "line_start": 1, "line_end": 1, "fact": "main 直接返回零"}]
    behavior_id, flow_id = "S001-B01", "S001-F01"
    candidate = [{
        "id": "S001-RC01", "title": "退出结果可能被调用者解释为失败",
        "condition": "入口返回非零", "propagation": "退出值传给调用者", "effect": "调用者判定启动失败",
        "observation": "读取进程退出码", "recovery": "修正触发条件后重新运行", "confidence": "low",
        "related_ids": [behavior_id], "evidence": evidence,
    }] if with_candidate else []
    result = {
        "type": "semantic_fragment", "run_id": "r1", "revision": context["revision"], "slice_id": "S001",
        "summary": "程序入口直接返回零。",
        "applied_skills": [{"name": item["name"], "reason": item["reason"]} for item in context["methods"]],
        "behaviors": [{
            "id": behavior_id, "title": "程序入口", "category": "entry", "description": "main 直接返回零。",
            "inputs": ["无参数"], "outputs": ["退出码零"], "related_ids": [flow_id], "evidence": evidence,
        }],
        "flows": [{
            "id": flow_id, "title": "正常退出流程", "trigger": "进程启动", "steps": ["进入 main", "返回零"],
            "result": "进程以零退出", "related_ids": [behavior_id], "evidence": evidence,
        }],
        "branches": [], "states": [], "resources": [], "concurrency": [], "error_chains": [],
        "risk_candidates": candidate, "gaps": [],
    }
    assert plan["slices"][0]["method_names"]
    return context, result


def test_stop_single_fragment_is_staged(tmp_path: Path) -> None:
    _context, result = _prepared(tmp_path)
    analysis.analyze(tmp_path, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(result), ""))
    staged = engine.read_json(tmp_path / "pangea-data/runs/r1/results/S001.json")
    assert staged["type"] == "semantic_fragment"
    assert "dfx" not in staged and "test_cases" not in staged
    receipt = engine.read_json(tmp_path / "pangea-data/runs/r1/executions/S001-attempt-001.json")
    assert receipt["finish_reasons"] == ["stop"] and receipt["tokens"]["output"] == 500


@pytest.mark.parametrize("reason", ["length", "max_tokens", "tool-calls"])
def test_non_stop_finish_is_failure(tmp_path: Path, reason: str) -> None:
    _context, result = _prepared(tmp_path)
    with pytest.raises(analysis.AnalysisError, match="finish reason is not stop"):
        analysis.analyze(tmp_path, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(result, reason=reason), ""))
    assert engine.load_run(tmp_path, "r1")["state"] == "failed"


def test_extra_json_empty_output_and_tool_use_are_rejected(tmp_path: Path) -> None:
    _context, result = _prepared(tmp_path)
    with pytest.raises(analysis.AnalysisError, match="data after"):
        analysis.analyze(tmp_path, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(result, text=json.dumps(result) + "{}"), ""))
    for suffix, text, tool, message in (("empty", "", None, "empty final text"), ("tool", None, "bash", "called tools")):
        root = tmp_path / suffix
        _context, result = _prepared(root)
        with pytest.raises(analysis.AnalysisError, match=message):
            analysis.analyze(root, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(result, text=text, tool=tool), ""))


def test_json_duplicate_keys_are_rejected_with_role_name() -> None:
    with pytest.raises(analysis.AnalysisError, match="test-designer final JSON contains duplicate key: id"):
        analysis._one_json_object('{"id":"one","id":"two"}', role="test-designer")


def test_out_of_slice_evidence_and_unavailable_skill_are_rejected(tmp_path: Path) -> None:
    context, result = _prepared(tmp_path)
    broken = copy.deepcopy(result)
    broken["behaviors"][0]["evidence"][0]["line_end"] = 2
    with pytest.raises(model.ContractError, match="outside supplied"):
        model.validate_slice_result(broken, context)
    broken = copy.deepcopy(result)
    broken["applied_skills"].append({"name": "invented", "reason": "不存在"})
    with pytest.raises(model.ContractError, match="unavailable"):
        model.validate_slice_result(broken, context)


def test_slice_contract_forbids_dfx_tests_and_accepts_risk_candidate_without_case(tmp_path: Path) -> None:
    context, result = _prepared(tmp_path, with_candidate=True)
    assert "dfx" not in context["contract"]["object_keys"]
    assert "test_cases" not in context["contract"]["object_keys"]
    assert model.validate_slice_result(result, context)["risk_candidates"]
    with_extra = {**result, "dfx": []}
    with pytest.raises(model.ContractError, match="keys"):
        model.validate_slice_result(with_extra, context)
    build_fragment = copy.deepcopy(result)
    build_fragment["behaviors"][0]["category"] = "build"
    assert model.validate_slice_result(build_fragment, context)["behaviors"][0]["category"] == "build"
    assert "do not assert undocumented external semantics" in analysis.ROLE_PROMPTS["analysis-worker"]


def test_isolated_roles_disable_tools() -> None:
    for role in ("analysis-worker", "composer", "dfx-analyst", "risk-adjudicator", "risk-analyst", "test-designer", "reviewer"):
        environment = analysis.isolated_opencode_environment(role)
        config = json.loads(environment["OPENCODE_CONFIG_CONTENT"])["agent"][role]
        assert config["tools"] and not any(config["tools"].values())
        assert set(json.loads(environment["OPENCODE_PERMISSION"]).values()) == {"deny"}


def test_retry_appends_receipt(tmp_path: Path) -> None:
    _context, result = _prepared(tmp_path)
    with pytest.raises(analysis.AnalysisError):
        analysis.analyze(tmp_path, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(result, reason="length"), ""))
    engine.retry(tmp_path, "r1")
    analysis.analyze(tmp_path, "r1", runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(result), ""))
    receipts = sorted((tmp_path / "pangea-data/runs/r1/executions").glob("S001-attempt-*.json"))
    assert [path.name for path in receipts] == ["S001-attempt-001.json", "S001-attempt-002.json"]
