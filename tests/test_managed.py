from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime" / "runctl.py"
MANAGED = ROOT / "runtime" / "managed.py"


class RetiredManagedWorkflowTests(unittest.TestCase):
    def test_managed_v1_cli_is_unconditionally_rejected(self) -> None:
        for command in ("smoke-init", "put-artifact", "plan-rework"):
            with self.subTest(command=command):
                result = subprocess.run(
                    [sys.executable, str(MANAGED), command],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(2, result.returncode)
                self.assertIn("managed v1 CLI 已退役", result.stderr)

    def test_runctl_exposes_only_v2_lifecycle_and_schema_validation(self) -> None:
        result = subprocess.run(
            [sys.executable, str(RUNCTL), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("create-v2", result.stdout)
        self.assertIn("finalize-v2", result.stdout)
        for retired in ("init", "put-artifact", "apply-audit", "resume"):
            rejected = subprocess.run(
                [sys.executable, str(RUNCTL), retired],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(2, rejected.returncode)
            self.assertIn("invalid choice", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
