from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "runtime/data_runtime.py",
    '''def session_prepare(root: Path, stale_hours: int = 24) -> dict[str, Any]:
    workspace = ensure_layout(root)
    inbox = scan_inbox(root)
    return {"data_root": str(workspace), "inbox": inbox, "document_import": convert_catalog(root), "repositories": safe_pull_repositories(root),
            "incomplete_runs": incomplete_runs(root), "tmp_cleanup": cleanup_stale_tmp(root, stale_hours)}''',
    '''def session_prepare(root: Path, stale_hours: int = 24) -> dict[str, Any]:
    workspace = ensure_layout(root)
    inbox = scan_inbox(root)
    document_import = convert_catalog(root)
    step_errors: dict[str, dict[str, str]] = {}
    try:
        repositories = safe_pull_repositories(root)
    except (DataRuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        repositories = []
        step_errors["repositories"] = {
            "type": type(exc).__name__,
            "message": str(exc) or "仓库准备失败",
        }
    return {
        "data_root": str(workspace),
        "inbox": inbox,
        "document_import": document_import,
        "repositories": repositories,
        "incomplete_runs": incomplete_runs(root),
        "tmp_cleanup": cleanup_stale_tmp(root, stale_hours),
        "step_errors": step_errors,
    }''',
)

Path("tests/test_session_prepare_isolation.py").write_text(
    '''from __future__ import annotations

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
        self.assertEqual("OSError", prepared["step_errors"]["repositories"]["type"])
        self.assertIn("decode failed", prepared["step_errors"]["repositories"]["message"])
        self.assertEqual("run-one", prepared["incomplete_runs"][0]["run_id"])
        self.assertIn("tmp_cleanup", prepared)
        json.dumps(prepared, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)
