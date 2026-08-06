from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, repository_runtime


def tree_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        digest.update(item.relative_to(path).as_posix().encode())
        if item.is_file(): digest.update(item.read_bytes())
    return digest.hexdigest()


class RepositoryRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        workspace = data_runtime.ensure_layout(self.root)
        self.repo = workspace / "repositories" / "driver"; self.repo.mkdir()
        self.git("init"); self.git("config", "user.email", "test@example.invalid"); self.git("config", "user.name", "Test")
        (self.repo / "state.txt").write_text("old\n", encoding="utf-8"); self.git("add", "state.txt"); self.git("commit", "-m", "old")
        self.old = self.git("rev-parse", "HEAD").stdout.strip()
        (self.repo / "state.txt").write_text("new\n", encoding="utf-8"); self.git("commit", "-am", "new")
        data_runtime.create_run(self.root, "run-one", {"schema_version":"1.0", "mode":"module_analysis", "goal":"test", "target":"driver", "repositories":["driver"], "analysis_depth":"complete", "created_by":"pangea-test"})

    def tearDown(self) -> None: self.temp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True, capture_output=True, check=True)

    def test_snapshot_uses_requested_commit_without_mutating_source_repository(self) -> None:
        before = tree_hash(self.repo)
        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", self.old)
        after = tree_hash(self.repo)
        snapshot = Path(result["snapshot_dir"])
        self.assertEqual(before, after)
        self.assertEqual("old\n", (snapshot / "state.txt").read_text(encoding="utf-8"))
        self.assertEqual(self.old, result["manifest"]["commit_sha"])
        self.assertRegex(result["manifest"]["content_sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue((snapshot / repository_runtime.MANIFEST_NAME).is_file())
        self.assertFalse(os.access(snapshot / "state.txt", os.W_OK))

    def test_rejects_escape_and_cleanup_is_idempotent(self) -> None:
        with self.assertRaises(repository_runtime.RepositoryRuntimeError):
            repository_runtime.create_snapshot(self.root, "run-one", "../driver")
        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", snapshot_id="current")
        with self.assertRaises(repository_runtime.RepositoryRuntimeError):
            repository_runtime.cleanup_snapshot(self.root, "run-one", "../escape")
        self.assertTrue(repository_runtime.cleanup_snapshot(self.root, "run-one", "current")["removed"])
        self.assertFalse(repository_runtime.cleanup_snapshot(self.root, "run-one", "current")["removed"])
        self.assertFalse(Path(result["snapshot_dir"]).exists())

    def test_run_cleanup_removes_only_managed_snapshot_content_and_rejects_symlink_escape(self) -> None:
        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", self.old)
        tmp = data_runtime.ensure_layout(self.root) / "runs" / "run-one" / "tmp"
        (tmp / "user-note.txt").write_text("keep", encoding="utf-8")
        cleaned = repository_runtime.cleanup_run_tmp(self.root, "run-one")
        self.assertEqual(["snapshots"], cleaned["removed"])
        self.assertFalse(Path(result["snapshot_dir"]).exists())
        self.assertEqual("keep", (tmp / "user-note.txt").read_text(encoding="utf-8"))
        self.assertEqual([], repository_runtime.cleanup_run_tmp(self.root, "run-one")["removed"])

        outside = self.root / "outside"; outside.mkdir()
        (tmp / "snapshots").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(repository_runtime.RepositoryRuntimeError):
            repository_runtime.cleanup_run_tmp(self.root, "run-one")
        self.assertTrue(outside.exists())

    def test_run_tmp_cannot_link_to_another_runs_physical_tmp(self) -> None:
        second = data_runtime.create_run(self.root, "run-two", {
            "schema_version":"1.0", "mode":"module_analysis", "goal":"test", "target":"driver",
            "repositories":["driver"], "analysis_depth":"complete", "created_by":"pangea-test",
        })
        run_one_tmp = data_runtime.ensure_layout(self.root) / "runs" / "run-one" / "tmp"
        run_two_tmp = Path(second["run_dir"]) / "tmp"
        sentinel = run_two_tmp / "must-stay.txt"
        sentinel.write_text("run-two", encoding="utf-8")
        run_one_tmp.rmdir()
        run_one_tmp.symlink_to(run_two_tmp, target_is_directory=True)

        operations = (
            lambda: repository_runtime.create_snapshot(self.root, "run-one", "driver"),
            lambda: repository_runtime.snapshot_status(self.root, "run-one"),
            lambda: repository_runtime.verify_snapshots_against_source(self.root, "run-one"),
            lambda: repository_runtime.cleanup_snapshot(self.root, "run-one", "driver"),
            lambda: repository_runtime.cleanup_run_tmp(self.root, "run-one"),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(repository_runtime.RepositoryRuntimeError, "Run 固定目录 tmp"):
                    operation()

        self.assertEqual("run-two", sentinel.read_text(encoding="utf-8"))
        self.assertEqual(["must-stay.txt"], sorted(path.name for path in run_two_tmp.iterdir()))

    def test_run_manifest_must_match_requested_run_id(self) -> None:
        run_dir = data_runtime.ensure_layout(self.root) / "runs" / "run-one"
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        manifest["run_id"] = "run-two"
        data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)

        with self.assertRaisesRegex(repository_runtime.RepositoryRuntimeError, "manifest 无效"):
            repository_runtime.snapshot_status(self.root, "run-one")

    def test_snapshot_status_reads_manifest_without_touching_source_repository(self) -> None:
        before = tree_hash(self.repo)
        repository_runtime.create_snapshot(self.root, "run-one", "driver", self.old, "mr-driver")
        status = repository_runtime.snapshot_status(self.root, "run-one")
        self.assertEqual(before, tree_hash(self.repo))
        self.assertEqual(self.old, status["snapshots"][0]["commit_sha"])
        self.assertEqual("driver", status["snapshots"][0]["repository"])

    def test_snapshot_status_detects_content_tampering_after_permissions_are_restored(self) -> None:
        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", self.old, "tamper-check")
        snapshot_file = Path(result["snapshot_dir"]) / "state.txt"
        snapshot_file.chmod(0o644)
        snapshot_file.write_text("modified after archive\n", encoding="utf-8")
        snapshot_file.chmod(0o444)
        status = repository_runtime.snapshot_status(self.root, "run-one")
        self.assertEqual([], status["snapshots"])
        self.assertIn("SHA-256 不匹配", status["coverage_gaps"][0]["detail"])

    def test_unfinished_run_keeps_snapshot_for_resume(self) -> None:
        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", self.old, "resume-driver")
        tmp = data_runtime.ensure_layout(self.root) / "runs" / "run-one" / "tmp"
        self.assertTrue(Path(result["snapshot_dir"]).is_dir())
        self.assertEqual("resume-driver", repository_runtime.snapshot_status(self.root, "run-one")["snapshots"][0]["snapshot_id"])
        self.assertTrue((tmp / "snapshots" / "resume-driver" / repository_runtime.MANIFEST_NAME).is_file())

    def test_related_repository_gap_does_not_block_current_snapshot(self) -> None:
        result = repository_runtime.create_snapshots(self.root, "run-one", [{"repository":"driver"}, {"repository":"missing"}])
        self.assertEqual(1, len(result["snapshots"]))
        self.assertEqual("missing", result["coverage_gaps"][0]["repository"])

    def test_gitlink_metadata_survives_status_and_source_verification(self) -> None:
        linked = self.root / "linked"; linked.mkdir()
        subprocess.run(["git", "-C", str(linked), "init"], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(linked), "config", "user.email", "test@example.invalid"], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(linked), "config", "user.name", "Test"], text=True, capture_output=True, check=True)
        (linked / "module.txt").write_text("linked\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(linked), "add", "module.txt"], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(linked), "commit", "-m", "linked"], text=True, capture_output=True, check=True)
        linked_commit = subprocess.run(["git", "-C", str(linked), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{linked_commit},vendor/linked")
        self.git("commit", "-m", "add gitlink")

        before = tree_hash(self.repo)
        before_status = self.git("status", "--porcelain").stdout
        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", snapshot_id="gitlink")
        status = repository_runtime.snapshot_status(self.root, "run-one")
        verified = repository_runtime.verify_snapshots_against_source(self.root, "run-one")

        expected_gap = {
            "snapshot_id": "gitlink", "repository": "driver", "kind": "gitlink",
            "path": "vendor/linked", "commit_sha": linked_commit,
            "detail": repository_runtime.GITLINK_GAP_DETAIL,
        }
        manifest_gap = dict(expected_gap)
        self.assertEqual([manifest_gap], result["manifest"]["coverage_gaps"])
        self.assertEqual([expected_gap], status["coverage_gaps"])
        self.assertEqual([expected_gap], verified["coverage_gaps"])
        self.assertEqual(before, tree_hash(self.repo))
        self.assertEqual(before_status, self.git("status", "--porcelain").stdout)

    def test_snapshot_status_fails_closed_for_invalid_manifest_coverage_gaps(self) -> None:
        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", snapshot_id="invalid-gaps")
        manifest_path = Path(result["snapshot_dir"]) / repository_runtime.MANIFEST_NAME
        manifest_path.chmod(0o644)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["coverage_gaps"] = [{"kind": "submodule"}]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path.chmod(0o444)

        status = repository_runtime.snapshot_status(self.root, "run-one")

        self.assertEqual([], status["snapshots"])
        self.assertEqual("invalid-gaps", status["coverage_gaps"][0]["snapshot_id"])
        self.assertIn("coverage_gaps 条目格式无效", status["coverage_gaps"][0]["detail"])

    def test_gitlink_metadata_tampering_fails_closed_in_status_and_source_verification(self) -> None:
        linked = self.root / "linked"; linked.mkdir()
        subprocess.run(["git", "-C", str(linked), "init"], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(linked), "config", "user.email", "test@example.invalid"], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(linked), "config", "user.name", "Test"], text=True, capture_output=True, check=True)
        (linked / "module.txt").write_text("linked\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(linked), "add", "module.txt"], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(linked), "commit", "-m", "linked"], text=True, capture_output=True, check=True)
        linked_commit = subprocess.run(["git", "-C", str(linked), "rev-parse", "HEAD"], text=True, capture_output=True, check=True).stdout.strip()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{linked_commit},vendor/linked")
        self.git("commit", "-m", "add gitlink")

        result = repository_runtime.create_snapshot(self.root, "run-one", "driver", snapshot_id="gitlink-tamper")
        manifest_path = Path(result["snapshot_dir"]) / repository_runtime.MANIFEST_NAME
        manifest_path.chmod(0o644)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["coverage_gaps"][0]["commit_sha"] = "0" * 40
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_path.chmod(0o444)

        status = repository_runtime.snapshot_status(self.root, "run-one")
        verified = repository_runtime.verify_snapshots_against_source(self.root, "run-one")

        self.assertEqual(["0" * 40], [gap["commit_sha"] for gap in status["coverage_gaps"]])
        self.assertEqual([], verified["snapshots"])
        self.assertIn("gitlink 元数据与登记源仓 commit 不匹配", verified["coverage_gaps"][-1]["detail"])

    def test_rejects_plain_registered_directory_inside_parent_git_repository(self) -> None:
        subprocess.run(["git", "-C", str(self.root), "init"], text=True, capture_output=True, check=True)
        plain = data_runtime.ensure_layout(self.root) / "repositories" / "plain"
        plain.mkdir()

        with self.assertRaisesRegex(repository_runtime.RepositoryRuntimeError, "独立 Git 工作树根目录"):
            repository_runtime.create_snapshot(self.root, "run-one", "plain")

        snapshots = data_runtime.ensure_layout(self.root) / "runs" / "run-one" / "tmp" / "snapshots"
        self.assertFalse((snapshots / "plain").exists())
        self.assertTrue((self.repo / ".git").exists())

    def test_rejects_repository_symlink_even_when_it_points_to_a_registered_worktree(self) -> None:
        alias = data_runtime.ensure_layout(self.root) / "repositories" / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        with self.assertRaisesRegex(repository_runtime.RepositoryRuntimeError, "符号链接仓库"):
            repository_runtime.create_snapshot(self.root, "run-one", "alias")


if __name__ == "__main__":
    unittest.main()
