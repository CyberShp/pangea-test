from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"


class ModuleSnapshotBindingTests(unittest.TestCase):
    def test_dirty_module_repository_is_bound_to_head_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = data_runtime.ensure_layout(root) / "repositories" / "driver"
            repo.mkdir()
            subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
            (repo / "tracked.c").write_text("int committed = 1;\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.c"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "--quiet", "-m", "initial"], check=True)
            commit = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            (repo / "tracked.c").unlink()
            (repo / "local.tmp").write_text("do not analyze", encoding="utf-8")

            env = os.environ.copy()
            env["PANGEA_VALIDATOR"] = "stdlib"
            result = subprocess.run(
                [
                    sys.executable,
                    str(RUNCTL),
                    "create-v2",
                    "--root",
                    str(root),
                    "--scenario",
                    "module-analysis",
                    "--target",
                    "connection",
                    "--repository",
                    "driver",
                    "--analysis-depth",
                    "fast",
                    "--run-id",
                    "module-dirty",
                ],
                cwd=ROOT,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)

            self.assertEqual(commit, payload["contract"]["repository_commits"]["driver"])
            self.assertEqual([], payload["source_snapshots"]["coverage_gaps"])
            binding = payload["source_snapshots"]["snapshots"][0]
            snapshot = Path(binding["snapshot_dir"])
            self.assertEqual("int committed = 1;\n", (snapshot / "tracked.c").read_text(encoding="utf-8"))
            self.assertFalse((snapshot / "local.tmp").exists())
            self.assertFalse((repo / "tracked.c").exists())
            self.assertTrue((repo / "local.tmp").exists())
            manifest = json.loads((snapshot / "snapshot-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(commit, manifest["commit_sha"])
            persisted = json.loads(
                (Path(payload["run_dir"]) / "internal" / "source-snapshots.json").read_text(encoding="utf-8")
            )
            self.assertEqual(commit, persisted["snapshots"][0]["manifest"]["commit_sha"])

    def test_module_policy_requires_snapshot_only_source_evidence(self) -> None:
        module = (ROOT / ".opencode" / "commands" / "module-analysis.md").read_text(encoding="utf-8")
        primary = (ROOT / ".opencode" / "agents" / "pangea-test.md").read_text(encoding="utf-8")
        for phrase in ("repository_commits", "source_snapshots", "只读 commit 快照", "不直接读取用户源工作区"):
            self.assertIn(phrase, module)
        self.assertIn("源码证据只来自 `tmp/snapshots/`", primary)
        self.assertIn("不得误报仓库无权限", primary)


if __name__ == "__main__":
    unittest.main()
