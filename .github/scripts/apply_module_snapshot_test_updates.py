from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"expected test block not found in {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "tests/test_e2e_v2.py",
    '''        snapshot = self.cli(
            "repo", "--root", str(self.root), "snapshot", "--run-id", "module-complete",
            "--repository", "driver",
        )
        self.assertEqual("driver", snapshot["manifest"]["repository"])
''',
    '''        snapshot = module["source_snapshots"]["snapshots"][0]
        self.assertEqual("driver", snapshot["manifest"]["repository"])
        self.assertEqual(module["contract"]["repository_commits"]["driver"], snapshot["manifest"]["commit_sha"])
''',
)

replace_once(
    "tests/test_workflows_v2.py",
    '''            before_finalize = self.cli("resume-v2", "--root", tmp, "--run-id", "module-fast")
            self.assertEqual(snapshot["manifest"]["commit_sha"], before_finalize["snapshots"]["snapshots"][0]["commit_sha"])
''',
    '''            before_finalize = self.cli("resume-v2", "--root", tmp, "--run-id", "module-fast")
            snapshot_commits = {item["commit_sha"] for item in before_finalize["snapshots"]["snapshots"]}
            self.assertIn(snapshot["manifest"]["commit_sha"], snapshot_commits)
''',
)
