from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:200]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "runtime/data_runtime.py",
    '''LAYOUT = ("inbox", "repositories", "runs")
REQUIRED_RUN_LAYOUT = ("internal",)
''',
    '''LAYOUT = ("inbox", "repositories", "runs")
OPTIONAL_LAYOUT = ("library", "indexes", "reports", "tmp")
REQUIRED_RUN_LAYOUT = ("internal",)
''',
)
replace_once(
    "runtime/data_runtime.py",
    '''    for relative in LAYOUT:
        directory = workspace / relative
        _ensure_managed_directory(directory, workspace_resolved, f"受管目录 {relative}")
    return workspace
''',
    '''    for relative in LAYOUT:
        directory = workspace / relative
        _ensure_managed_directory(directory, workspace_resolved, f"受管目录 {relative}")
    for relative in OPTIONAL_LAYOUT:
        directory = workspace / relative
        if directory.exists() or directory.is_symlink():
            _require_managed_directory(directory, workspace_resolved, f"受管目录 {relative}")
    return workspace
''',
)

replace_once(
    "tests/test_repository_runtime.py",
    '''        run_one_tmp.rmdir()
        run_one_tmp.symlink_to(run_two_tmp, target_is_directory=True)
''',
    '''        if run_one_tmp.exists():
            run_one_tmp.rmdir()
        run_one_tmp.symlink_to(run_two_tmp, target_is_directory=True)
''',
)

replace_once(
    "tests/test_workflows_v2.py",
    '''            final_dir = Path(created["run_dir"]) / "final"
            external = root / "external-final"
            external.mkdir()
            marker = external / "report.md"
            marker.write_text("outside\\n", encoding="utf-8")
            final_dir.rmdir()
            final_dir.symlink_to(external, target_is_directory=True)
            rejected_final_link = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
            self.assertEqual(2, rejected_final_link.returncode)
            self.assertIn("固定目录 final", rejected_final_link.stderr)
            self.assertEqual("outside\\n", marker.read_text(encoding="utf-8"))
            final_dir.unlink()
            final_dir.mkdir()
            final = self.cli("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
''',
    '''            reports_root = root / "pangea-data" / "reports"
            reports_root.mkdir()
            report_dir = reports_root / "module-fast"
            external = root / "external-final"
            external.mkdir()
            marker = external / "report.md"
            marker.write_text("outside\\n", encoding="utf-8")
            report_dir.symlink_to(external, target_is_directory=True)
            rejected_final_link = self.cli_result("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
            self.assertEqual(2, rejected_final_link.returncode)
            self.assertIn("正式报告目录已存在", rejected_final_link.stderr)
            self.assertEqual("outside\\n", marker.read_text(encoding="utf-8"))
            report_dir.unlink()
            final = self.cli("finalize-v2", "--root", tmp, "--run-id", "module-fast", "--model", str(model))
''',
)
replace_once(
    "tests/test_workflows_v2.py",
    '''            self.assertEqual([], list((root / "pangea-data" / "runs" / "module-fast" / "tmp").iterdir()))
''',
    '''            self.assertFalse((root / "pangea-data" / "runs" / "module-fast" / "tmp").exists())
''',
)
