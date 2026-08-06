from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, index_runtime


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(path.rglob("*")):
        digest.update(item.relative_to(path).as_posix().encode())
        if item.is_file():
            digest.update(item.read_bytes())
    return digest.hexdigest()


class IndexRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        workspace = data_runtime.ensure_layout(self.root)
        self.repo = workspace / "repositories" / "driver"; self.repo.mkdir()
        self.git("init"); self.git("config", "user.email", "test@example.invalid"); self.git("config", "user.name", "Test")
        (self.repo / "driver.c").write_text("int ready;\n", encoding="utf-8")
        self.git("add", "driver.c"); self.git("commit", "-m", "initial")
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(self.repo), *args], text=True, capture_output=True, check=True)

    def runner(self, command: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
        self.calls.append(command)
        if command[0] == "/mock/gitnexus":
            if command[-1] == "--version": return subprocess.CompletedProcess(command, 0, "GitNexus 1.6.4", "")
            if command[-2:] == ["analyze", "--help"]: return subprocess.CompletedProcess(command, 0, "--skip-agents-md --no-stats", "")
            if command[-1] == "--help": return subprocess.CompletedProcess(command, 0, "analyze detect-changes", "")
            self.assertEqual("analyze", command[1])
            shadow = Path(command[2]); self.assertIn("pangea-data/indexes/shadows/driver", str(shadow))
            self.assertNotIn(str(self.repo), command)
            (shadow / ".gitnexus").mkdir(exist_ok=True)
            return subprocess.CompletedProcess(command, 0, "Files: 2\nSymbols: 5", "")
        return subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout)

    def test_cold_unchanged_and_updated_indexing_never_mutates_source(self) -> None:
        before = fingerprint(self.repo)
        first = index_runtime.index_repository(self.root, "driver", runner=self.runner, which=lambda _: "/mock/gitnexus")
        self.assertEqual("indexed", first["status"]); self.assertEqual("cold", first["index_mode"])
        self.assertEqual(2, first["statistics"]["files"]); self.assertEqual(5, first["statistics"]["symbols"])
        self.assertEqual(before, fingerprint(self.repo))
        nexus_calls = [call for call in self.calls if call[:2] == ["/mock/gitnexus", "analyze"] and call[-1] != "--help"]
        self.assertTrue(nexus_calls); self.assertTrue(all("indexes/shadows/driver" in " ".join(call) for call in nexus_calls))
        self.calls.clear()
        same = index_runtime.index_repository(self.root, "driver", runner=self.runner, which=lambda _: "/mock/gitnexus")
        self.assertEqual("unchanged", same["status"]); self.assertFalse(any(call[0] == "/mock/gitnexus" and len(call) > 1 and call[1] == "analyze" and len(call) > 2 and not call[-1] == "--help" for call in self.calls))
        (self.repo / "driver.c").write_text("int ready = 1;\n", encoding="utf-8"); self.git("commit", "-am", "update")
        updated_source = fingerprint(self.repo)
        updated = index_runtime.index_repository(self.root, "driver", runner=self.runner, which=lambda _: "/mock/gitnexus")
        self.assertEqual("indexed", updated["status"]); self.assertEqual("updated", updated["index_mode"])
        self.assertEqual(updated_source, fingerprint(self.repo))

    def test_unavailable_dangerous_name_and_all_are_structured(self) -> None:
        skipped = index_runtime.index_repository(self.root, "driver", runner=self.runner, which=lambda _: None)
        self.assertEqual("skipped", skipped["status"])
        with self.assertRaises(index_runtime.IndexRuntimeError):
            index_runtime.index_repository(self.root, "../driver", runner=self.runner, which=lambda _: None)
        broken = data_runtime.ensure_layout(self.root) / "repositories" / "broken"; broken.mkdir()
        (data_runtime.ensure_layout(self.root) / "repositories" / "bad name").mkdir()
        result = index_runtime.index_all(self.root, runner=self.runner, which=lambda _: None)
        self.assertEqual(3, len(result["repositories"])); self.assertEqual(2, result["failed"])

    def test_failed_analyze_is_retried_at_same_commit_and_can_succeed(self) -> None:
        attempts = 0

        def fail_once(command: list[str], cwd: Path | None, timeout: int) -> subprocess.CompletedProcess[str]:
            nonlocal attempts
            if command[:2] == ["/mock/gitnexus", "analyze"] and command[-1] != "--help":
                attempts += 1
                if attempts == 1:
                    self.calls.append(command)
                    return subprocess.CompletedProcess(command, 7, "", "analysis failed")
            return self.runner(command, cwd, timeout)

        first = index_runtime.index_repository(self.root, "driver", runner=fail_once, which=lambda _: "/mock/gitnexus")
        self.assertEqual("failed", first["status"])
        self.assertEqual("cold", first["index_mode"])
        second = index_runtime.index_repository(self.root, "driver", runner=fail_once, which=lambda _: "/mock/gitnexus")
        self.assertEqual("indexed", second["status"])
        self.assertEqual("retry", second["index_mode"])
        self.assertEqual("previous_status_failed", second["baseline"]["reason"])
        self.assertEqual(2, attempts)

    def test_rejects_plain_registered_directory_inside_parent_git_repository(self) -> None:
        subprocess.run(["git", "-C", str(self.root), "init"], text=True, capture_output=True, check=True)
        workspace = data_runtime.ensure_layout(self.root)
        plain = workspace / "repositories" / "plain"
        plain.mkdir()

        result = index_runtime.index_repository(self.root, "plain", runner=self.runner, which=lambda _: "/mock/gitnexus")

        self.assertEqual("failed", result["status"])
        self.assertIn("独立 Git 工作树根目录", result["failure"])
        self.assertFalse((workspace / "indexes" / "shadows" / "plain").exists())
        self.assertFalse(any(call[0] == "/mock/gitnexus" for call in self.calls))


if __name__ == "__main__":
    unittest.main()
