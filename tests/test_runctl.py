from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"


class RunCtlTests(unittest.TestCase):
    def run_cli(self, *args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PANGEA_VALIDATOR"] = "stdlib"
        result = subprocess.run(
            [sys.executable, str(RUNCTL), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(expect, result.returncode, msg=result.stderr or result.stdout)
        return result

    def test_init_creates_valid_manifest_and_resume_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            runs = root / "runs"
            result = self.run_cli(
                "init",
                "--target", "nvmet_tcp",
                "--source-path", str(source),
                "--runs-root", str(runs),
                "--task-id", "test-run",
            )
            payload = json.loads(result.stdout)
            self.assertEqual("test-run", payload["task_id"])
            self.assertEqual("stdlib", payload["validation_backend"])
            run_dir = runs / "test-run"
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual("module-full-analysis", manifest["scenario_id"])
            self.assertGreaterEqual(len(manifest["planned_artifacts"]), 10)
            resumed = self.run_cli("resume", "--run-dir", str(run_dir))
            resume_payload = json.loads(resumed.stdout)
            self.assertEqual(len(manifest["planned_artifacts"]), len(resume_payload["next_tasks"]))

    def test_init_rejects_existing_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            runs = root / "runs"
            args = ("init", "--target", "x", "--source-path", str(source), "--runs-root", str(runs), "--task-id", "same")
            self.run_cli(*args)
            second = self.run_cli(*args, expect=2)
            self.assertIn("已存在", second.stderr)

    def test_stdlib_validator_rejects_invalid_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            invalid = Path(tmp) / "invalid.json"
            invalid.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
            result = self.run_cli(
                "validate",
                "--file", str(invalid),
                "--schema", "task-envelope.schema.json",
                expect=2,
            )
            self.assertIn("缺少必填字段", result.stderr)


if __name__ == "__main__":
    unittest.main()
