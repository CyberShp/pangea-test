from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import data_runtime, index_runtime
from runtime import process_runtime


class WindowsSubprocessTests(unittest.TestCase):
    def test_run_text_decodes_utf8_bytes_and_normalizes_missing_streams(self) -> None:
        with patch.object(
            process_runtime.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(["git"], 0, "中文路径".encode("utf-8"), None),
        ):
            result = process_runtime.run_text(["git", "rev-parse"])
        self.assertEqual("中文路径", result.stdout)
        self.assertEqual("", result.stderr)

    def test_safe_pull_treats_missing_top_level_output_as_structured_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
            repo.mkdir()
            responses = [
                subprocess.CompletedProcess(["git"], 0, "true", ""),
                subprocess.CompletedProcess(["git"], 0, None, None),
            ]
            with patch("runtime.data_runtime._git", side_effect=responses):
                result = data_runtime.safe_pull_repositories(root)
        self.assertEqual("skipped", result[0]["status"])
        self.assertEqual("无法确认 Git 工作树根目录", result[0]["reason"])

    def test_index_records_missing_top_level_output_instead_of_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
            repo.mkdir()

            def runner(command, cwd, timeout):
                if command[-1] == "--is-inside-work-tree":
                    return subprocess.CompletedProcess(command, 0, "true", "")
                if command[-1] == "--show-toplevel":
                    return subprocess.CompletedProcess(command, 0, None, None)
                raise AssertionError(command)

            result = index_runtime.index_repository(root, "driver", runner=runner, which=lambda _: None)
        self.assertEqual("failed", result["status"])
        self.assertIn("无法确定 Git 工作树根目录", result["failure"])


if __name__ == "__main__":
    unittest.main()
