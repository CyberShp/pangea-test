from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import data_runtime


class SessionPrepareIsolationTests(unittest.TestCase):
    def test_repository_failure_preserves_json_and_incomplete_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = {
                "schema_version": "1.0",
                "mode": "module_analysis",
                "goal": "分析连接恢复",
                "target": "iscsi",
                "repositories": ["driver"],
                "analysis_depth": "complete",
                "created_by": "pangea-test",
            }
            data_runtime.create_run(root, "run-one", contract)

            with patch(
                "runtime.data_runtime.safe_pull_repositories",
                side_effect=OSError("git output decode failed"),
            ):
                prepared = data_runtime.session_prepare(root)

        self.assertEqual([], prepared["repositories"])
        self.assertEqual(str(root.resolve()), prepared["project_root"])
        self.assertEqual(str(root.resolve() / "pangea-data/repositories"), prepared["repository_root"])
        self.assertEqual([], prepared["known_repositories"])
        self.assertEqual("OSError", prepared["step_errors"]["repositories"]["type"])
        self.assertIn("decode failed", prepared["step_errors"]["repositories"]["message"])
        self.assertEqual("run-one", prepared["incomplete_runs"][0]["run_id"])
        self.assertIn("tmp_cleanup", prepared)
        json.dumps(prepared, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
