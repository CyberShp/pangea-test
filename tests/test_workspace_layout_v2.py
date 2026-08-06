from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, library_runtime

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceLayoutV2Tests(unittest.TestCase):
    def test_initial_layout_has_only_durable_entry_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = data_runtime.ensure_layout(root)
            self.assertEqual({"inbox", "repositories", "runs"}, {item.name for item in workspace.iterdir()})
            prepared = data_runtime.session_prepare(root)
            self.assertFalse((workspace / "library").exists())
            self.assertFalse((workspace / "indexes").exists())
            self.assertFalse((workspace / "reports").exists())
            self.assertFalse((workspace / "tmp").exists())
            self.assertIn("formal_reports", prepared["workspace_inventory"]["locations"])

    def test_run_directories_are_lazy_and_inventory_separates_categories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = {"schema_version": "1.0", "mode": "module_analysis", "goal": "分析连接恢复",
                        "target": "iscsi", "repositories": ["driver"], "analysis_depth": "fast",
                        "created_by": "pangea-test"}
            created = data_runtime.create_run(root, "run-one", contract)
            run_dir = Path(created["run_dir"])
            self.assertEqual({"internal", "manifest.json"}, {item.name for item in run_dir.iterdir()})
            data_runtime.append_checkpoint(root, "run-one", {
                "stage": "code_map", "status": "completed",
                "facts": [{"summary": "已定位连接处理入口与状态边界", "evidence": "snapshot/iscsi.c:42"}],
                "open_items": [], "next_step": "继续流程分析",
            })
            self.assertTrue((run_dir / "checkpoints").is_dir())
            inventory = data_runtime.workspace_inventory(root)
            history = inventory["run_history"][0]
            self.assertEqual("run-one", history["run_id"])
            self.assertIn(str(run_dir / "internal"), history["intermediate_dirs"])
            self.assertEqual([], inventory["formal_reports"])

    def test_legacy_roots_are_detected_but_not_moved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "outputs" / "old-report.md"
            legacy.parent.mkdir()
            legacy.write_text("old", encoding="utf-8")
            result = library_runtime.legacy_migration_gaps(root)
            self.assertEqual("outputs", result["legacy_migration_gaps"][0]["legacy_root"])
            self.assertTrue(legacy.exists())
            self.assertIn("pangea-data/reports", result["suggested_destinations"]["outputs"])

    def test_repository_has_no_retired_root_placeholders(self) -> None:
        for name in ("source", "inputs", "workspace", "outputs", "projects", "runs"):
            self.assertFalse((ROOT / name).exists(), name)
        self.assertFalse((ROOT / "core" / "modules").exists())
        self.assertFalse((ROOT / "core" / "protocols").exists())


if __name__ == "__main__":
    unittest.main()


class ReportLifecycleLayoutTests(unittest.TestCase):
    def test_stage_command_is_exposed_and_formal_reports_are_outside_runs(self) -> None:
        help_result = subprocess.run(
            [str(Path(__import__("sys").executable)), str(ROOT / "runtime" / "runctl.py"), "--help"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, help_result.returncode, help_result.stderr)
        self.assertIn("stage-report-v2", help_result.stdout)
        primary = (ROOT / ".opencode" / "agents" / "pangea-test.md").read_text(encoding="utf-8")
        self.assertIn("pangea-data/reports/<run-id>/report.md", primary)
        self.assertIn("聊天中的报告摘要不是正式交付", primary)
