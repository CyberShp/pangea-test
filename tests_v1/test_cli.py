from __future__ import annotations

import subprocess
from pathlib import Path

from runtime import engine, runctl
from tests_v1.test_analysis import _native, _prepared
from tests_v1.test_review_report import _adjudication, _dfx_value, _empty_test, _module_value, _review_value, _risk_synthesis


def test_full_pipeline_uses_all_isolated_stage_receipts(tmp_path: Path) -> None:
    _context, fragment = _prepared(tmp_path)
    result = runctl.run_pipeline(
        tmp_path, "r1", model_name="test/model", timeout_seconds=10,
        analysis_runner=lambda *a, **k: subprocess.CompletedProcess([], 0, _native(fragment), ""),
        composition_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_module_value(c)), ""),
        dfx_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_dfx_value(c)), ""),
        adjudication_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_adjudication(c)), ""),
        risk_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_risk_synthesis(c)), ""),
        design_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_empty_test(c)), ""),
        review_runner=lambda _r, c, **k: subprocess.CompletedProcess([], 0, _native(_review_value(c)), ""),
    )
    assert result["status"] == "completed"
    run = engine.load_run(tmp_path, "r1")
    assert run["state"] == "completed" and Path(result["reports"]["markdown"]).is_file()
    names = {path.name for path in (tmp_path / "pangea-data/runs/r1/executions").glob("*.json")}
    assert {"S001-attempt-001.json", "compose-attempt-001.json", "dfx-attempt-001.json", "risk-synthesis-attempt-001.json", "test-design-001-attempt-001.json", "review-attempt-001.json"}.issubset(names)


def test_opencode_commands_are_single_runtime_calls() -> None:
    root = Path(__file__).resolve().parents[1]
    agent = (root / ".opencode/agents/pangea-test.md").read_text(encoding="utf-8")
    assert "read: false" in agent
    for name in ("analyze", "resume", "retry", "status"):
        text = (root / f".opencode/commands/{name}.md").read_text(encoding="utf-8")
        assert f"python3 runtime/runctl.py {name} $ARGUMENTS" in text
        assert "不得执行任何前置探测" in text
