from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCTOR = ROOT / "runtime" / "doctor.py"


class DoctorTests(unittest.TestCase):
    def test_doctor_accepts_the_exact_runtime_role_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, str(DOCTOR)], cwd=ROOT,
            text=True, capture_output=True, check=False,
        )
        payload = json.loads(result.stdout)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("AVAILABLE", payload["direct_expert_mode"])
        self.assertEqual("AVAILABLE", payload["managed_task_mode"])
        names = {item["name"] for item in payload["checks"]}
        self.assertIn("primary_agent_identity", names)
        self.assertIn("retired_family_agents", names)
        self.assertIn("runtime_role_contract", names)
        self.assertIn("opencode_resolved_permissions", names)
        self.assertIn("data_runtime", names)
        self.assertIn("internal_agent_visibility", names)
        self.assertIn("v2_workflow_entrypoints", names)


if __name__ == "__main__":
    unittest.main()
