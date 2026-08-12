from pathlib import Path

import pytest

from runtime import engine, model


def test_run_uses_single_v2_state_source(tmp_path: Path) -> None:
    run = engine.create_run(tmp_path, "r1", repository="spdk", scope="app/iscsi_tgt", target="iscsi_tgt")
    assert run["format_version"] == "2" and run["state"] == "draft"
    assert run["source"] == {"files": [], "source_map": None, "methods": []}
    assert engine.transition(tmp_path, "r1", "draft", "confirmed")["state"] == "confirmed"
    with pytest.raises(engine.RunError, match="illegal"):
        engine.transition(tmp_path, "r1", "confirmed", "analyzing")


def test_public_contracts_encode_new_stage_boundaries() -> None:
    contract = model.slice_result_contract("S001", ["c-cpp-analysis"])
    assert contract["type"] == "semantic_fragment"
    assert "risk_candidates" in contract["object_keys"]
    assert not {"dfx", "risks", "scenarios", "test_cases"}.intersection(contract["object_keys"])
    assert model.DFX_CONTRACT["finding_types"] == ["strength", "risk", "limitation", "gap", "recommendation"]
    assert model.DFX_CONTRACT["field_types"]["finding.risk_candidate.missing_evidence"].startswith("list[string]")
    assert "Critical" in model.RISK_CONTRACT["field_types"]["risk.severity"]
    assert "可靠性与一致性" in model.RISK_CONTRACT["field_types"]["risk.dfx_dimensions"]
    assert model.TESTABILITY == ("blackbox_ready", "graybox_ready", "blocked")
