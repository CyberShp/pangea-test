from __future__ import annotations

from pathlib import Path

root = Path(__file__).resolve().parents[2]

# Validate all downstream paths before publishing the new checkpoint, then remove them after manifest publication.
path = root / "runtime/data_runtime.py"
text = path.read_text(encoding="utf-8")
start = text.index("def _invalidate_lifecycle_after_checkpoint(run_dir: Path, manifest: dict[str, Any], stage: str) -> None:\n")
end = text.index("\ndef append_checkpoint(root: Path, run_id: str, checkpoint: dict[str, Any]) -> dict[str, Any]:\n", start)
replacement = '''def _lifecycle_downstream_paths(run_dir: Path, manifest: dict[str, Any], stage: str) -> list[Path]:
    if manifest.get("contract_record_file") != "internal/contract-record.json" or stage in {"report", "rework"}:
        return []
    candidates = [
        run_dir / "internal/analysis-model.json", run_dir / "internal/report-model.json",
        run_dir / "internal/coverage-judge.json", run_dir / "internal/auditor-receipt.json",
    ]
    if stage in {"code_map", "flow", "branches", "dfx_scan", "impact_chain", "mr_baseline", "dfx_route"}:
        candidates.append(run_dir / "internal/worker-index.json")
    existing: list[Path] = []
    for path in candidates:
        if path.is_symlink():
            raise DataRuntimeError(f"拒绝删除符号链接下游工件: {path}")
        if not path.exists():
            continue
        if not path.is_file() or path.resolve().parent != (run_dir / "internal").resolve():
            raise DataRuntimeError(f"拒绝删除异常下游工件: {path}")
        existing.append(path)
    return existing


'''
text = text[:start] + replacement + text[end + 1:]
old = '''    validate_runtime_record(checkpoint, "stage-checkpoint.schema.json")
    _verify_checkpoint_artifacts(run_dir, manifest, checkpoint)
    _write_json_exclusive(checkpoint_dir / f"{number:03d}-{checkpoint['stage']}.json", checkpoint)
'''
new = '''    validate_runtime_record(checkpoint, "stage-checkpoint.schema.json")
    _verify_checkpoint_artifacts(run_dir, manifest, checkpoint)
    downstream = _lifecycle_downstream_paths(run_dir, manifest, checkpoint["stage"])
    _write_json_exclusive(checkpoint_dir / f"{number:03d}-{checkpoint['stage']}.json", checkpoint)
'''
if text.count(old) != 1:
    raise SystemExit(f"checkpoint prevalidation count={text.count(old)}")
text = text.replace(old, new, 1)
old = '''    atomic_write_json(run_dir / "manifest.json", manifest)
    _invalidate_lifecycle_after_checkpoint(run_dir, manifest, checkpoint["stage"])
    return checkpoint
'''
new = '''    atomic_write_json(run_dir / "manifest.json", manifest)
    for path in downstream:
        path.unlink()
    return checkpoint
'''
if text.count(old) != 1:
    raise SystemExit(f"checkpoint delete count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

# Evidence and MR-diff changes invalidate all dependent provenance indexes immediately.
path = root / "runtime/runctl.py"
text = path.read_text(encoding="utf-8")
old = '''    _invalidate_fixed_artifact(_evidence_provenance_path(run_dir))
    _invalidate_fixed_artifact(_analysis_model_path(run_dir))
    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
'''
new = '''    _invalidate_fixed_artifact(_evidence_provenance_path(run_dir))
    _invalidate_fixed_artifact(_worker_index_path(run_dir))
    _invalidate_fixed_artifact(_analysis_model_path(run_dir))
    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
    _invalidate_fixed_artifact(_auditor_receipt_path(run_dir))
'''
if text.count(old) != 1:
    raise SystemExit(f"MR diff invalidation count={text.count(old)}")
text = text.replace(old, new, 1)
old = '''    _invalidate_fixed_artifact(_analysis_model_path(run_dir))
    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
    data_runtime.atomic_write_json(target, normalized)
'''
new = '''    _invalidate_fixed_artifact(_worker_index_path(run_dir))
    _invalidate_fixed_artifact(_analysis_model_path(run_dir))
    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
    _invalidate_fixed_artifact(_auditor_receipt_path(run_dir))
    data_runtime.atomic_write_json(target, normalized)
'''
if text.count(old) != 1:
    raise SystemExit(f"evidence invalidation count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

# Regression: a second content-addressed artifact for the same stage does not break the first checkpoint.
path = root / "tests/test_worker_audit_provenance.py"
text = path.read_text(encoding="utf-8")
marker = '\n    def test_missing_worker_blocks_analysis(self) -> None:\n'
insert = '''
    def test_repeated_stage_artifacts_preserve_historical_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); run_dir = self.prepare(root, "append-only-stage")
            bindings = []
            for suffix in ("首次入口盘点", "补充入口盘点"):
                artifact = {"artifact_type": "stage_artifact", "schema_version": "1.0", "run_id": run_dir.name,
                            "stage": "code_map", "summary": f"代码地图{suffix}已经形成可复核结构化工件",
                            "evidence_ids": ["EV-1"], "item_ids": [f"EP-{len(bindings) + 1}"], "open_items": []}
                source = root / f"code-map-{len(bindings) + 1}.json"
                source.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
                binding = self.cli(root, "stage-work-product-v2", "--run-id", run_dir.name,
                                   "--stage", "code_map", "--file", str(source))["artifact_binding"]
                data_runtime.append_checkpoint(root, run_dir.name, {"stage": "code_map", "status": "completed",
                    "facts": [{"summary": suffix + "已完成", "evidence": "EV-1"}],
                    "artifact_bindings": [binding], "open_items": [], "next_step": "继续"})
                bindings.append(binding)
            self.assertNotEqual(bindings[0]["path"], bindings[1]["path"])
            self.assertTrue((run_dir / bindings[0]["path"]).is_file())
            resumed = self.cli(root, "resume-v2", "--run-id", run_dir.name)
            self.assertEqual("flow", resumed["next_stage"])

'''
if text.count(marker) != 1:
    raise SystemExit(f"append-only test marker count={text.count(marker)}")
path.write_text(text.replace(marker, "\n" + insert + marker, 1), encoding="utf-8")
