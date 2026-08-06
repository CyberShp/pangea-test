from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, runctl

ROOT = Path(__file__).resolve().parents[1]
RUNCTL = ROOT / "runtime/runctl.py"


class ContractLifecycleTests(unittest.TestCase):
    @staticmethod
    def marked_root(root: Path) -> None:
        (root / ".opencode").mkdir(parents=True)
        (root / "runtime").mkdir(); (root / "runtime/runctl.py").write_text("# marker\n")
        (root / "tooling/pangea_cli").mkdir(parents=True)
        (root / "tooling/pangea_cli/__main__.py").write_text("# marker\n")
        (root / "registry").mkdir(); (root / "registry/scenarios.json").write_text("{}\n")

    @staticmethod
    def repository(root: Path) -> None:
        repo = data_runtime.ensure_layout(root) / "repositories/driver"
        repo.mkdir()
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True)
        (repo / "driver.c").write_text("int entry(void) { return 0; }\n")
        subprocess.run(["git", "-C", str(repo), "add", "driver.c"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=test@example.invalid",
                        "-c", "user.name=PANGEA Test", "commit", "--quiet", "-m", "initial"], check=True)

    @staticmethod
    def receipt(root: Path) -> None:
        workspace = data_runtime.ensure_layout(root)
        session = data_runtime._ensure_managed_directory(workspace / "session", workspace.resolve(), "session")
        payload = {
            "artifact_type": "preflight_receipt", "schema_version": "1.0",
            "created_at": data_runtime.utc_now(), "status": "ready",
            "project_root": str(root.resolve()), "data_root": str(workspace),
            "repository_root": str(workspace / "repositories"), "known_repositories": ["driver"],
            "allowed_next_actions": ["draft_contract"], "python_executable": sys.executable,
            "step_results": {}, "step_errors": {},
        }
        data_runtime.atomic_write_json(session / "preflight-receipt.json", payload)

    @staticmethod
    def cli(root: Path, *args: str, expected: int = 0) -> dict:
        result = subprocess.run([sys.executable, str(RUNCTL), *args, "--root", str(root)],
                                cwd=ROOT, text=True, capture_output=True, check=False)
        if result.returncode != expected:
            raise AssertionError(result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {"stderr": result.stderr}

    def prepare(self, root: Path) -> None:
        self.marked_root(root); self.repository(root); self.receipt(root)

    def test_complete_contract_requires_user_confirmation_before_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                             "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "chap-contract")
            self.assertTrue(draft["confirmation_required"])
            self.assertFalse((root / "pangea-data/runs/chap-run").exists())
            rejected = self.cli(root, "confirm-contract-v2", "--contract-id", "chap-contract",
                                "--source", "auto_unambiguous", "--materials-status", "confirmed_none", expected=2)
            self.assertIn("禁止自动确认", rejected["stderr"])
            self.cli(root, "confirm-contract-v2", "--contract-id", "chap-contract",
                     "--source", "user_reply", "--materials-status", "confirmed_none")
            activated = self.cli(root, "activate-contract-v2", "--contract-id", "chap-contract", "--run-id", "chap-run")
            run_dir = Path(activated["run_dir"])
            self.assertTrue((run_dir / "internal/contract-record.json").is_file())
            self.assertTrue((run_dir / "internal/contract-confirmation.json").is_file())
            record = json.loads((run_dir / "internal/contract-record.json").read_text())
            self.assertEqual("activated", record["status"])
            self.assertEqual("chap-run", record["activation"]["run_id"])

    def test_direct_create_is_rejected_on_marked_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            rejected = self.cli(root, "create-v2", "--scenario", "module-analysis", "--target", "chap",
                                "--repository", "driver", "--analysis-depth", "complete", expected=2)
            self.assertIn("禁止直接 create-v2", rejected["stderr"])
            self.assertEqual([], list((root / "pangea-data/runs").iterdir()))

    def test_fast_contract_may_use_auto_unambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                             "--repository", "driver", "--analysis-depth", "fast", "--contract-id", "fast-contract")
            self.assertFalse(draft["confirmation_required"])
            self.cli(root, "confirm-contract-v2", "--contract-id", "fast-contract",
                     "--source", "auto_unambiguous", "--materials-status", "unchanged")
            activated = self.cli(root, "activate-contract-v2", "--contract-id", "fast-contract", "--run-id", "fast-run")
            self.assertEqual("activated", activated["contract_status"])

    def test_changed_preflight_receipt_invalidates_confirmed_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                     "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "changed")
            self.cli(root, "confirm-contract-v2", "--contract-id", "changed",
                     "--source", "user_reply", "--materials-status", "confirmed_none")
            receipt = root / "pangea-data/session/preflight-receipt.json"
            payload = json.loads(receipt.read_text()); payload["step_errors"] = {"index": {"message": "changed"}}
            data_runtime.atomic_write_json(receipt, payload)
            rejected = self.cli(root, "activate-contract-v2", "--contract-id", "changed", "--run-id", "bad", expected=2)
            self.assertIn("发生变化", rejected["stderr"])


if __name__ == "__main__":
    unittest.main()
