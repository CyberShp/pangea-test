from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import data_runtime, index_runtime


class RepositoryReadinessTests(unittest.TestCase):
    def _repo(self, root: Path) -> Path:
        repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        (repo / "tracked.c").write_text("int tracked;\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "tracked.c"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True, text=True)
        return repo

    def test_tracked_deletion_skips_only_pull(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root)
            (repo / "tracked.c").unlink()
            calls: list[tuple[str, ...]] = []
            real_git = data_runtime._git

            def recording(repo_path: Path, *args: str):
                calls.append(args)
                return real_git(repo_path, *args)

            with patch("runtime.data_runtime._git", side_effect=recording):
                result = data_runtime.safe_pull_repositories(root)[0]

        self.assertEqual("ready", result["access_status"])
        self.assertEqual("dirty", result["worktree_status"])
        self.assertEqual(1, result["worktree_changes"]["deleted"])
        self.assertEqual("skipped", result["update_status"])
        self.assertTrue(result["index_eligible"])
        self.assertTrue(result["snapshot_eligible"])
        self.assertNotIn(("pull", "--ff-only"), calls)

    def test_dirty_repository_is_still_accepted_by_index_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = self._repo(root)
            commit = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
            (repo / "tracked.c").unlink()
            record = index_runtime.index_repository(root, "driver", which=lambda _: None)

        self.assertEqual("skipped", record["status"])
        self.assertEqual(commit, record["source_commit"])
        self.assertIsNone(record["failure"])
        self.assertIn("GitNexus 不可用", record["degradation"])


if __name__ == "__main__":
    unittest.main()
