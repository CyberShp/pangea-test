from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "runtime" / "doctor.py"


class DoctorTests(unittest.TestCase):
    def test_doctor_reports_both_modes_available(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DOCTOR)], cwd=ROOT,
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, msg=result.stderr or result.stdout)
        payload = json.loads(result.stdout)
        self.assertEqual("AVAILABLE", payload["direct_expert_mode"])
        self.assertEqual("AVAILABLE", payload["managed_task_mode"])
        names = {item["name"] for item in payload["checks"]}
        self.assertIn("primary_agent_identity", names)
        self.assertIn("family_agent_tab_modes", names)
        self.assertIn("internal_agent_visibility", names)


if __name__ == "__main__":
    unittest.main()
