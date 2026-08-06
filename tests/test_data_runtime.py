from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from runtime import data_runtime


ROOT = Path(__file__).resolve().parents[1]


class DataRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def contract(self) -> dict[str, object]:
        return {
            "schema_version": "1.0", "mode": "module_analysis", "goal": "分析连接恢复",
            "target": "iscsi", "repositories": ["driver"], "analysis_depth": "complete",
            "created_by": "pangea-test",
        }

    def test_initialize_and_incremental_inbox_catalog_preserves_original(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        self.assertTrue((workspace / "repositories").is_dir())
        source = workspace / "inbox" / "需求" / "recovery.docx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"first")
        first = data_runtime.scan_inbox(self.root)
        self.assertEqual((1, 0, 0), (first["added"], first["changed"], first["unchanged"]))
        self.assertEqual(b"first", source.read_bytes())
        second = data_runtime.scan_inbox(self.root)
        self.assertEqual(1, second["unchanged"])
        source.write_bytes(b"second")
        third = data_runtime.scan_inbox(self.root)
        self.assertEqual(1, third["changed"])
        records = [json.loads(line) for line in (workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual("需求/recovery.docx", records[0]["source_path"])
        self.assertTrue((workspace / "library" / "sources").is_dir())

    def test_duplicate_content_reuses_conversion_and_preserves_metadata(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        archive = workspace / "inbox" / "one.docx"
        import zipfile
        with zipfile.ZipFile(archive, "w") as document:
            document.writestr("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>需求</w:t></w:r></w:p></w:body></w:document>')
        duplicate = workspace / "inbox" / "copies" / "two.docx"; duplicate.parent.mkdir()
        duplicate.write_bytes(archive.read_bytes())
        data_runtime.scan_inbox(self.root)
        first = data_runtime.convert_catalog(self.root)
        self.assertEqual(1, first["converted"])
        second_scan = data_runtime.scan_inbox(self.root)
        self.assertEqual(2, second_scan["unchanged"])
        second = data_runtime.convert_catalog(self.root)
        self.assertEqual(2, second["reused"])
        rows = [json.loads(line) for line in (workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual({"converted"}, {row["conversion_status"] for row in rows})
        self.assertEqual(1, len({row["source_archive_path"] for row in rows}))
        self.assertTrue(all(row.get("markdown_path") for row in rows))
        self.assertTrue(archive.exists())

    def test_legacy_office_and_text_files_are_catalogued_as_pending_or_converted(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        (workspace / "inbox" / "legacy.xls").write_bytes(b"old-binary")
        (workspace / "inbox" / "notes.txt").write_text("recovery path", encoding="utf-8")
        data_runtime.scan_inbox(self.root)
        result = data_runtime.convert_catalog(self.root)
        self.assertEqual(1, result["pending"])
        self.assertEqual(1, result["converted"])
        rows = [json.loads(line) for line in (workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8").splitlines()]
        by_name = {row["source_path"]: row for row in rows}
        self.assertEqual("pending", by_name["legacy.xls"]["conversion_status"])
        self.assertEqual("converted", by_name["notes.txt"]["conversion_status"])
        self.assertTrue((workspace / by_name["legacy.xls"]["markdown_path"]).exists())

    def test_safe_pull_skips_dirty_and_detached_repositories_without_mutating_them(self) -> None:
        repos = data_runtime.ensure_layout(self.root) / "repositories"
        dirty = repos / "dirty"; dirty.mkdir()
        subprocess.run(["git", "init", str(dirty)], text=True, capture_output=True, check=True)
        (dirty / "note.txt").write_text("keep", encoding="utf-8")
        detached = repos / "detached"; detached.mkdir()
        subprocess.run(["git", "init", str(detached)], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(detached), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(detached), "config", "user.name", "Test"], check=True)
        (detached / "tracked.txt").write_text("tracked", encoding="utf-8")
        subprocess.run(["git", "-C", str(detached), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(detached), "commit", "-m", "initial"], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(detached), "checkout", "--detach"], text=True, capture_output=True, check=True)
        result = data_runtime.safe_pull_repositories(self.root)
        by_name = {row["repository"]: row for row in result}
        self.assertEqual("skipped", by_name["dirty"]["status"])
        self.assertIn("未提交", by_name["dirty"]["reason"])
        self.assertEqual("keep", (dirty / "note.txt").read_text(encoding="utf-8"))
        self.assertEqual("skipped", by_name["detached"]["status"])
        self.assertIn("未附着", by_name["detached"]["reason"])

    def test_safe_pull_rejects_plain_directory_absorbed_by_parent_repository(self) -> None:
        subprocess.run(["git", "init", str(self.root)], text=True, capture_output=True, check=True)
        repos = data_runtime.ensure_layout(self.root) / "repositories"
        plain = repos / "plain"; plain.mkdir()
        calls: list[tuple[str, ...]] = []
        real_git = data_runtime._git

        def recording_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return real_git(repo, *args)

        with patch("runtime.data_runtime._git", side_effect=recording_git):
            result = data_runtime.safe_pull_repositories(self.root)
        self.assertEqual([{"repository": "plain", "status": "skipped", "reason": "不是独立登记的 Git 工作树"}], result)
        self.assertNotIn(("pull", "--ff-only"), calls)

    def test_safe_pull_rejects_repository_symlink_without_touching_external_repository(self) -> None:
        repositories = data_runtime.ensure_layout(self.root) / "repositories"
        external = self.root / "external-repository"
        subprocess.run(["git", "init", str(external)], text=True, capture_output=True, check=True)
        subprocess.run(["git", "-C", str(external), "config", "user.email", "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(external), "config", "user.name", "Test"], check=True)
        (external / "tracked.txt").write_text("before", encoding="utf-8")
        subprocess.run(["git", "-C", str(external), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(external), "commit", "-m", "initial"], text=True, capture_output=True, check=True)
        before = subprocess.run(
            ["git", "-C", str(external), "status", "--porcelain=v1", "--branch"],
            text=True, capture_output=True, check=True,
        ).stdout
        (repositories / "escaped").symlink_to(external, target_is_directory=True)
        calls: list[tuple[str, ...]] = []
        real_git = data_runtime._git

        def recording_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
            calls.append(args)
            return real_git(repo, *args)

        with patch("runtime.data_runtime._git", side_effect=recording_git):
            result = data_runtime.safe_pull_repositories(self.root)

        after = subprocess.run(
            ["git", "-C", str(external), "status", "--porcelain=v1", "--branch"],
            text=True, capture_output=True, check=True,
        ).stdout
        self.assertEqual([{"repository": "escaped", "status": "skipped", "reason": "拒绝符号链接仓库目录"}], result)
        self.assertNotIn(("pull", "--ff-only"), calls)
        self.assertEqual([], calls)
        self.assertEqual(before, after)
        self.assertEqual("before", (external / "tracked.txt").read_text(encoding="utf-8"))

    def test_git_is_non_interactive_and_bounded(self) -> None:
        with patch("runtime.data_runtime.subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            data_runtime._git(self.root, "status", "--porcelain")
            _, kwargs = run.call_args
            self.assertEqual("0", kwargs["env"]["GIT_TERMINAL_PROMPT"])
            self.assertEqual(data_runtime.GIT_TIMEOUT_SECONDS, kwargs["timeout"])

    def test_session_prepare_preserves_unfinished_tmp_and_cleans_terminal_stale_tmp(self) -> None:
        created = data_runtime.create_run(self.root, "run-one", self.contract())
        run_dir = Path(created["run_dir"])
        stale_active = run_dir / "tmp" / "snapshot"; stale_active.mkdir()

        completed = data_runtime.create_run(self.root, "run-two", self.contract())
        completed_dir = Path(completed["run_dir"])
        completed_manifest = data_runtime.read_json(completed_dir / "manifest.json")
        completed_manifest["status"] = "completed"
        completed_manifest["machine_state"] = "completed"
        data_runtime.atomic_write_json(completed_dir / "manifest.json", completed_manifest)
        stale_terminal = completed_dir / "tmp" / "old"; stale_terminal.mkdir()
        fresh_terminal = completed_dir / "tmp" / "new"; fresh_terminal.mkdir()

        old = time.time() - 3 * 3600
        os.utime(stale_active, (old, old))
        os.utime(stale_terminal, (old, old))
        prepared = data_runtime.session_prepare(self.root, stale_hours=1)
        self.assertEqual("run-one", prepared["incomplete_runs"][0]["run_id"])
        self.assertTrue(stale_active.exists(), "未完成 Run 的 MR 快照必须可跨 session 恢复")
        self.assertFalse(stale_terminal.exists())
        self.assertTrue(fresh_terminal.exists())

    def test_data_workspace_symlink_is_rejected_before_layout_or_cleanup(self) -> None:
        external = self.root / "external"
        external.mkdir()
        victim = external / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        (self.root / "pangea-data").symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "符号链接 data workspace"):
            data_runtime.ensure_layout(self.root)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "符号链接 data workspace"):
            data_runtime.cleanup_stale_tmp(self.root)

        self.assertEqual("keep", victim.read_text(encoding="utf-8"))
        self.assertFalse((external / "runs").exists())

    def test_managed_root_and_run_fixed_directory_symlinks_are_rejected(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        external = self.root / "external"
        external.mkdir()
        (workspace / "indexes").rmdir()
        (workspace / "indexes").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "受管目录 indexes"):
            data_runtime.ensure_layout(self.root)
        self.assertFalse((external / "sources").exists())

        (workspace / "indexes").unlink()
        (workspace / "indexes").mkdir()
        created = data_runtime.create_run(self.root, "linked-run", self.contract())
        run_dir = Path(created["run_dir"])
        (run_dir / "internal" / "audit").rmdir()
        (run_dir / "internal" / "task-contract.json").unlink()
        (run_dir / "internal" / "risk-ledger.json").unlink()
        (run_dir / "internal").rmdir()
        (run_dir / "internal").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "Run 固定目录 internal"):
            data_runtime._load_run(self.root, "linked-run")

    def test_inbox_links_are_rejected_without_reading_external_files(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        external = self.root / "external.txt"
        external.write_text("outside", encoding="utf-8")
        linked = workspace / "inbox" / "outside.txt"
        linked.symlink_to(external)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "非普通 inbox 项"):
            data_runtime.scan_inbox(self.root)
        self.assertEqual("outside", external.read_text(encoding="utf-8"))

    def test_existing_archive_must_be_regular_and_match_its_content_address(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        source = workspace / "inbox" / "notes.txt"
        source.write_text("trusted bytes", encoding="utf-8")
        data_runtime.scan_inbox(self.root)
        record = json.loads((workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8"))
        archive = workspace / record["source_archive_path"]
        archive.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "摘要不匹配"):
            data_runtime.scan_inbox(self.root)

        archive.unlink()
        external = self.root / "external.txt"
        external.write_text("outside", encoding="utf-8")
        archive.symlink_to(external)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "非普通文件"):
            data_runtime.scan_inbox(self.root)

    def test_inbox_hash_and_archive_use_the_same_staged_bytes_during_source_race(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        source = workspace / "inbox" / "race.txt"
        source.write_bytes(b"before")
        real_copy = data_runtime.shutil.copyfileobj

        def change_after_copy(source_handle, destination_handle, *args, **kwargs):
            result = real_copy(source_handle, destination_handle, *args, **kwargs)
            source.write_bytes(b"after")
            return result

        with patch("runtime.data_runtime.shutil.copyfileobj", side_effect=change_after_copy):
            data_runtime.scan_inbox(self.root)

        record = json.loads((workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8"))
        archive = workspace / record["source_archive_path"]
        self.assertEqual(b"before", archive.read_bytes())
        self.assertEqual(data_runtime.sha256_file(archive), record["sha256"])
        self.assertEqual(b"after", source.read_bytes())

    def test_conversion_uses_staged_archive_when_content_address_is_replaced(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        source = workspace / "inbox" / "race.txt"
        source.write_text("before conversion", encoding="utf-8")
        data_runtime.scan_inbox(self.root)
        record = json.loads((workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8"))
        archive = workspace / record["source_archive_path"]
        from runtime import converters
        real_convert = converters.convert_document

        def replace_archive(staged, *args, **kwargs):
            self.assertNotEqual(archive, Path(staged))
            archive.write_text("after replacement", encoding="utf-8")
            return real_convert(staged, *args, **kwargs)

        with patch("runtime.converters.convert_document", side_effect=replace_archive):
            result = data_runtime.convert_catalog(self.root)
        self.assertEqual(1, result["converted"])
        markdown = workspace / "library" / "markdown" / f"{record['sha256']}.md"
        self.assertIn("before conversion", markdown.read_text(encoding="utf-8"))
        self.assertNotIn("after replacement", markdown.read_text(encoding="utf-8"))

    def test_conversion_rechecks_staged_digest_before_publishing(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        source = workspace / "inbox" / "mutated.txt"
        source.write_text("trusted", encoding="utf-8")
        data_runtime.scan_inbox(self.root)
        record = json.loads((workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8"))
        from runtime import converters
        real_convert = converters.convert_document

        def mutate_staging(staged, *args, **kwargs):
            converted = real_convert(staged, *args, **kwargs)
            Path(staged).write_text("mutated", encoding="utf-8")
            return converted

        with patch("runtime.converters.convert_document", side_effect=mutate_staging):
            with self.assertRaisesRegex(data_runtime.DataRuntimeError, "输入快照"):
                data_runtime.convert_catalog(self.root)
        markdown = workspace / "library" / "markdown" / f"{record['sha256']}.md"
        self.assertFalse(markdown.exists())

    def test_conversion_rejects_final_markdown_and_asset_symlinks(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        external = self.root / "external.bin"
        external.write_bytes(b"keep")
        text = workspace / "inbox" / "notes.txt"
        text.write_text("notes", encoding="utf-8")
        data_runtime.scan_inbox(self.root)
        record = json.loads((workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8"))
        markdown = workspace / "library" / "markdown" / f"{record['sha256']}.md"
        markdown.symlink_to(external)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "Markdown 输出"):
            data_runtime.convert_catalog(self.root)
        self.assertEqual(b"keep", external.read_bytes())

        markdown.unlink()
        text.unlink()
        import zipfile
        document = workspace / "inbox" / "media.docx"
        with zipfile.ZipFile(document, "w") as archive:
            archive.writestr("word/document.xml", '<w:document xmlns:w="w"><w:body><w:p><w:r><w:t>doc</w:t></w:r></w:p></w:body></w:document>')
            archive.writestr("word/media/image1.png", b"image")
        data_runtime.scan_inbox(self.root)
        record = json.loads((workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8"))
        asset = workspace / "library" / "assets" / record["sha256"] / "assets" / "image1.png"
        asset.parent.mkdir(parents=True)
        asset.symlink_to(external)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "转换资产"):
            data_runtime.convert_catalog(self.root)
        self.assertEqual(b"keep", external.read_bytes())

    def test_conversion_reuse_revalidates_existing_outputs(self) -> None:
        workspace = data_runtime.ensure_layout(self.root)
        external = self.root / "external.bin"
        external.write_bytes(b"keep")
        source = workspace / "inbox" / "reuse.txt"
        source.write_text("content", encoding="utf-8")
        data_runtime.scan_inbox(self.root)
        data_runtime.convert_catalog(self.root)
        record = json.loads((workspace / "library" / "catalog.jsonl").read_text(encoding="utf-8"))
        markdown = workspace / record["markdown_path"]
        markdown.unlink()
        markdown.symlink_to(external)
        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "既有 Markdown"):
            data_runtime.convert_catalog(self.root)
        self.assertEqual(b"keep", external.read_bytes())

    def test_cleanup_rejects_tmp_symlinks_without_following_or_deleting_them(self) -> None:
        created = data_runtime.create_run(self.root, "terminal", self.contract())
        run_dir = Path(created["run_dir"])
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        manifest["status"] = "completed"
        manifest["machine_state"] = "completed"
        data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)

        external = self.root / "external"
        external.mkdir()
        victim = external / "victim.txt"
        victim.write_text("keep", encoding="utf-8")
        escaped = run_dir / "tmp" / "escaped"
        escaped.symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "tmp 候选项"):
            data_runtime.cleanup_stale_tmp(self.root, stale_hours=1)

        self.assertEqual("keep", victim.read_text(encoding="utf-8"))
        self.assertTrue(escaped.is_symlink())

        escaped.unlink()
        broken = run_dir / "tmp" / "broken"
        broken.symlink_to(self.root / "missing-target", target_is_directory=True)

        with self.assertRaisesRegex(data_runtime.DataRuntimeError, "tmp 候选项"):
            data_runtime.cleanup_stale_tmp(self.root, stale_hours=1)

        self.assertEqual("keep", victim.read_text(encoding="utf-8"))
        self.assertTrue(broken.is_symlink())


if __name__ == "__main__":
    unittest.main()
