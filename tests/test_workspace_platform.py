from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tooling.pangea_cli import assetctl, inputctl, projectctl

ROOT = Path(__file__).resolve().parents[1]


class RetiredPlatformCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("source", "inputs", "workspace", "outputs", "projects", "assets", "registry", "runtime"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.env = os.environ.copy(); self.env["PANGEA_ROOT"] = str(self.root); self.env["PYTHONPATH"] = str(ROOT)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, "-m", "tooling.pangea_cli", *args], cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        return result

    def test_v1_platform_domains_are_unreachable(self) -> None:
        for domain in ("project", "input", "asset", "workflow"):
            with self.subTest(domain=domain):
                result = self.cli(domain, "--help")
                self.assertNotEqual(0, result.returncode)
                self.assertIn("invalid choice", result.stderr)

        self.assertFalse((self.root / "workspace" / "nvme-tcp").exists())
        self.assertFalse((self.root / "outputs" / "nvme-tcp").exists())

    def test_retired_platform_modules_have_no_v1_parsers(self) -> None:
        for module in (projectctl, inputctl, assetctl):
            with self.subTest(module=module.__name__):
                self.assertFalse(hasattr(module, "parser"))
                result = subprocess.run(
                    [sys.executable, "-m", module.__name__, "--help"],
                    cwd=ROOT, env=self.env, text=True, capture_output=True, check=False,
                )
                self.assertEqual(2, result.returncode)
                self.assertIn("已退役", result.stderr)


if __name__ == "__main__": unittest.main()
