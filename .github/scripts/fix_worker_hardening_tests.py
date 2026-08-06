from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / "tests/test_worker_audit_provenance.py"
text = path.read_text(encoding="utf-8")
replacements = [
    (
        "        self.stage_workers(root, run_dir); self.stage_artifacts_and_checkpoints(root, run_dir)\n",
        "        self.stage_artifacts_and_checkpoints(root, run_dir); self.stage_workers(root, run_dir)\n",
        1,
    ),
    (
        "            self.stage_workers(root, run_dir, omit=\"dfx-upgrade-compatibility\")\n            self.stage_artifacts_and_checkpoints(root, run_dir)\n",
        "            self.stage_artifacts_and_checkpoints(root, run_dir)\n            self.stage_workers(root, run_dir, omit=\"dfx-upgrade-compatibility\")\n",
        1,
    ),
    (
        "            self.stage_workers(root, run_dir, unknown=True); self.stage_artifacts_and_checkpoints(root, run_dir)\n",
        "            self.stage_artifacts_and_checkpoints(root, run_dir); self.stage_workers(root, run_dir, unknown=True)\n",
        1,
    ),
    (
        "            self.stage_workers(root, run_dir)\n            index = json.loads((run_dir / \"internal/worker-index.json\").read_text(encoding=\"utf-8\"))\n",
        "            self.stage_artifacts_and_checkpoints(root, run_dir); self.stage_workers(root, run_dir)\n            index = json.loads((run_dir / \"internal/worker-index.json\").read_text(encoding=\"utf-8\"))\n",
        1,
    ),
]
for old, new, expected in replacements:
    if text.count(old) != expected:
        raise SystemExit(f"worker order fixture count={text.count(old)} expected={expected}: {old[:60]}")
    text = text.replace(old, new, expected)
path.write_text(text, encoding="utf-8")
