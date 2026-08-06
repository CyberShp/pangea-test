from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / ".github/scripts/apply_worker_audit_provenance.py"
text = path.read_text(encoding="utf-8")
start = text.index('# Patch lifecycle helper so PR21 lifecycle analysis tests use bound checkpoints.')
end = text.index('\n# Agent structural test.', start)
replacement = r'''# Patch lifecycle helper so PR21 lifecycle analysis tests use bound checkpoints.
test_depth = read("tests/test_analysis_depth_contract.py")
function_start = test_depth.index("    @staticmethod\n    def complete_checkpoints(root: Path, run_id: str) -> None:\n")
function_end = test_depth.index("\n    @staticmethod\n    def model", function_start)
new_function = '''    @staticmethod
    def complete_checkpoints(root: Path, run_id: str) -> None:
        run_dir = data_runtime.ensure_layout(root) / "runs" / run_id
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        lifecycle = manifest.get("contract_record_file") == "internal/contract-record.json"

        def bindings(stage: str) -> list[dict[str, str]]:
            if not lifecycle:
                return []
            artifact = run_dir / "internal" / "stages" / f"{stage}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            data_runtime.atomic_write_json(artifact, {
                "artifact_type": "stage_artifact", "schema_version": "1.0", "run_id": run_id,
                "stage": stage, "summary": f"{stage} 阶段已形成可复核结构化工件",
                "evidence_ids": ["EV-1"], "item_ids": [stage.upper()], "open_items": [],
            })
            return [{"path": f"internal/stages/{stage}.json", "sha256": data_runtime.sha256_file(artifact)}]

        for stage in ("code_map", "flow", "branches"):
            data_runtime.append_checkpoint(root, run_id, {"stage": stage, "status": "completed",
                "facts": [{"summary": f"{stage} 已建立具体实现模型", "evidence": f"driver.c: {stage} evidence"}],
                "artifact_bindings": bindings(stage), "open_items": [], "next_step": "继续"})
        data_runtime.append_checkpoint(root, run_id, {"stage": "dfx_scan", "status": "completed",
            "facts": [{"dfx": item, "conclusion": f"{item}已形成具体结论", "evidence": f"driver.c: {item}"} for item in DFX],
            "artifact_bindings": bindings("dfx_scan"), "open_items": [], "next_step": "继续"})
        for stage in ("specialist", "sfmea", "test_design"):
            data_runtime.append_checkpoint(root, run_id, {"stage": stage, "status": "completed",
                "facts": [{"summary": f"{stage} 已形成具体分析工件", "evidence": f"internal/{stage}.json"}],
                "artifact_bindings": bindings(stage), "open_items": [], "next_step": "继续"})
'''
test_depth = test_depth[:function_start] + new_function + test_depth[function_end:]
write("tests/test_analysis_depth_contract.py", test_depth)
'''
path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
