from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import workspace_runtime


class PortablePreflightTests(unittest.TestCase):
    @staticmethod
    def marked_root(root: Path) -> Path:
        (root / ".opencode").mkdir(parents=True)
        (root / "runtime").mkdir()
        (root / "runtime/runctl.py").write_text("# marker\n", encoding="utf-8")
        (root / "tooling/pangea_cli").mkdir(parents=True)
        (root / "tooling/pangea_cli/__main__.py").write_text("# marker\n", encoding="utf-8")
        (root / "registry").mkdir()
        (root / "registry/scenarios.json").write_text("{}\n", encoding="utf-8")
        return root

    def test_locates_only_current_directory_and_parents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "项目 根")
            nested = root / "a" / "b"; nested.mkdir(parents=True)
            result = workspace_runtime.locate_project_root(start=nested, package_root=None, env={})
            self.assertEqual(root.resolve(), result["project_root"])
            self.assertEqual("cwd_or_parent", result["root_source"])
            self.assertNotIn(str(Path(tmp).parent), result["checked_paths"])

    def test_explicit_wrong_root_never_creates_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wrong = Path(tmp) / "pangea_code"; wrong.mkdir()
            result = workspace_runtime.run_preflight(explicit_root=wrong, start=wrong)
            self.assertEqual("workspace_unresolved", result["status"])
            self.assertFalse((wrong / "pangea-data").exists())
            self.assertEqual(["ask_user_for_project_root"], result["allowed_next_actions"])

    def test_windows_rejects_msys_drive_path(self) -> None:
        with self.assertRaises(workspace_runtime.WorkspaceResolutionError):
            workspace_runtime.validate_project_root("/d/2026/pangea-test", platform_name="windows")

    def test_child_commands_are_argument_arrays_without_shell_operators(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "中文 空格")
            steps = workspace_runtime.build_preflight_steps(root, sys.executable)
            self.assertEqual(4, len(steps))
            for _, command in steps:
                self.assertEqual(sys.executable, command[0])
                self.assertFalse(set(command) & workspace_runtime.SHELL_OPERATORS)
                self.assertNotIn("cd", command)

    def test_failure_returns_stable_paths_and_does_not_guess(self) -> None:
        calls: list[tuple[list[str], str]] = []
        def runner(command: list[str], *, cwd: str) -> subprocess.CompletedProcess[str]:
            calls.append((command, cwd))
            return subprocess.CompletedProcess(command, 2, "", "simulated failure")
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "PANGEA 项目")
            result = workspace_runtime.run_preflight(explicit_root=root, runner=runner)
            self.assertEqual("degraded", result["status"])
            self.assertEqual(str(root.resolve()), result["project_root"])
            self.assertEqual(str(root.resolve() / "pangea-data/repositories"), result["repository_root"])
            self.assertEqual(4, len(result["step_errors"]))
            self.assertTrue(all(cwd == str(root.resolve()) for _, cwd in calls))

    def test_preflight_cli_always_emits_json_for_bad_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-m", "tooling.pangea_cli", "preflight", "--root", tmp],
                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            payload = json.loads(result.stdout)
            self.assertEqual("workspace_unresolved", payload["status"])
            self.assertIn("workspace", payload["step_errors"])

    def test_post_preflight_requires_exact_recorded_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "project")
            session = root / "pangea-data/session"
            session.mkdir(parents=True)
            receipt = {
                "artifact_type": "preflight_receipt",
                "schema_version": "1.0",
                "project_root": str(root.resolve()),
                "python_executable": sys.executable,
            }
            (session / "preflight-receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False), encoding="utf-8",
            )
            self.assertEqual(
                sys.executable,
                workspace_runtime.require_preflight_python(root),
            )
            other = root / "other-python"
            other.write_text("not an interpreter\n", encoding="utf-8")
            with self.assertRaisesRegex(
                workspace_runtime.WorkspaceResolutionError, "必须原样使用",
            ):
                workspace_runtime.require_preflight_python(root, current_executable=other)

    def test_library_classify_rejects_wrong_python_before_catalog_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.marked_root(Path(tmp) / "project")
            session = root / "pangea-data/session"
            session.mkdir(parents=True)
            other = root / "other-python"
            other.write_text("not an interpreter\n", encoding="utf-8")
            receipt = {
                "artifact_type": "preflight_receipt",
                "schema_version": "1.0",
                "project_root": str(root.resolve()),
                "python_executable": str(other),
            }
            (session / "preflight-receipt.json").write_text(
                json.dumps(receipt, ensure_ascii=False), encoding="utf-8",
            )
            classification = root / "classification.json"
            classification.write_text("{}\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, "-m", "tooling.pangea_cli", "library", "--root", str(root),
                    "classify", "--source-path", "document.md", "--file", str(classification),
                ],
                cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, result.returncode)
            self.assertIn("必须原样使用", result.stderr)
            self.assertFalse((root / "pangea-data/library/catalog.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
