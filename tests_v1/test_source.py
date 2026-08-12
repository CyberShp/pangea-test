from pathlib import Path

from runtime import engine, methods, source


def _confirmed(root: Path) -> None:
    engine.create_run(root, "r1", repository="spdk", scope="app/iscsi_tgt", target="iscsi_tgt")
    engine.transition(root, "r1", "draft", "confirmed")


def test_plan_covers_source_lines_and_injects_evidence_gated_methods(tmp_path: Path) -> None:
    target = tmp_path / "pangea-data/repositories/spdk/app/iscsi_tgt"
    target.mkdir(parents=True)
    (target / "iscsi_tgt.c").write_text("int main(void) { if (1) return 0; return 1; }\n", encoding="utf-8")
    _confirmed(tmp_path)
    source_map = source.prepare(tmp_path, "r1")
    plan = source.plan(tmp_path, "r1")
    assert source_map["files"][0]["inventory"]
    covered = set()
    for item in plan["slices"]:
        covered.update(range(item["sources"][0]["line_start"], item["sources"][0]["line_end"] + 1))
        context = engine.read_json(tmp_path / f"pangea-data/runs/r1/contexts/{item['slice_id']}.json")
        names = {row["name"] for row in context["methods"]}
        assert {"c-cpp-analysis", "analysis-depth", "storage-spdk", "storage-iscsi"}.issubset(names)
        iscsi = next(row for row in context["methods"] if row["name"] == "storage-iscsi")
        assert "iSCSI cross-file checklist" not in iscsi["instructions"]
        assert context["slice"]["obligation_ids"]
        assert "dfx" not in context["contract"]["object_keys"]
    assert covered == {1}
    assert "storage-iscsi" in engine.load_run(tmp_path, "r1")["source"]["methods"]


def test_deep_domain_checklist_requires_implementation_signal(tmp_path: Path) -> None:
    selected = methods.select_slice_methods(
        tmp_path,
        {"repository": "spdk", "scope": "lib/iscsi", "target": "iSCSI"},
        [{"path": "lib/iscsi/login.c", "lines": [{"line": 1, "text": "iscsi_login session PDU CHAP"}]}],
    )
    iscsi = next(row for row in selected if row["name"] == "storage-iscsi")
    assert "iSCSI cross-file checklist" in iscsi["instructions"]


def test_generation_budget_splits_without_cutting_function_inventory(tmp_path: Path) -> None:
    target = tmp_path / "pangea-data/repositories/spdk/app/iscsi_tgt"
    target.mkdir(parents=True)
    text = "\n".join(f"int f{i}(int x) {{ if (x) return {i}; return 0; }}" for i in range(12)) + "\n"
    (target / "iscsi_tgt.c").write_text(text, encoding="utf-8")
    _confirmed(tmp_path)
    source.prepare(tmp_path, "r1")
    plan = source.plan(tmp_path, "r1", max_generation_tokens=3000)
    assert len(plan["slices"]) > 1
    assert all(row["estimated_generation_tokens"] <= 3000 or len(row["sources"]) == 1 for row in plan["slices"])


def test_default_generation_budget_is_below_provider_total_generation_cap() -> None:
    assert source.DEFAULT_MAX_GENERATION_TOKENS == 12_000
