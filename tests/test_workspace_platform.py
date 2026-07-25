from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class WorkspacePlatformTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("source", "inputs", "workspace", "outputs", "projects", "assets", "registry", "runtime"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        (self.root / "registry" / "workflows.json").write_text((ROOT / "registry" / "workflows.json").read_text(encoding="utf-8"), encoding="utf-8")
        (self.root / "runtime" / "runctl.py").write_text(
            'import argparse,json\nfrom pathlib import Path\np=argparse.ArgumentParser();s=p.add_subparsers(dest="c",required=True);i=s.add_parser("init")\n'
            'for x in ["scenario","target","source-path","runs-root","task-id","max-parallel","max-audit-rounds"]: i.add_argument("--"+x,required=True)\n'
            'a=p.parse_args();d=Path(a.runs_root)/a.task_id;d.mkdir(parents=True);(d/"final").mkdir();'
            '(d/"manifest.json").write_text(json.dumps({"summary_status":"pending","audit":{"rounds":0,"max_rounds":int(a.max_audit_rounds),"status":"pending","opinion_file":None}}),encoding="utf-8");'
            'print(json.dumps({"task_id":a.task_id,"run_dir":str(d)}))\n', encoding="utf-8")
        self.env = os.environ.copy(); self.env["PANGEA_ROOT"] = str(self.root); self.env["PYTHONPATH"] = str(ROOT)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def cli(self, *args: str, expect: int = 0) -> dict:
        result = subprocess.run([sys.executable, "-m", "tooling.pangea_cli", *args], cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(expect, result.returncode, msg=result.stderr or result.stdout)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def create_project(self) -> None:
        source = self.root / "source" / "nvme-tcp"; source.mkdir(parents=True)
        (source / "driver.c").write_text("int driver(void){return 0;}\n", encoding="utf-8")
        self.cli("project", "init", "--project-id", "nvme-tcp", "--asset-profile", "nvme")

    def test_project_init_keeps_source_clean(self) -> None:
        source = self.root / "source" / "nvme-tcp"; source.mkdir(parents=True)
        (source / "driver.c").write_text("int x;\n", encoding="utf-8")
        before = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
        result = self.cli("project", "init", "--project-id", "nvme-tcp")
        after = sorted(path.relative_to(source).as_posix() for path in source.rglob("*"))
        self.assertEqual(before, after); self.assertEqual("nvme-tcp", result["current_project"])
        self.assertTrue((self.root / "workspace" / "nvme-tcp").is_dir())

    def test_input_scan_classifies_roles_and_hashes(self) -> None:
        self.create_project(); base = self.root / "inputs" / "nvme-tcp"
        (base / "design").mkdir(parents=True); (base / "coverage").mkdir()
        (base / "design" / "连接设计V8.md").write_text("design", encoding="utf-8")
        (base / "coverage" / "index.html").write_text("coverage", encoding="utf-8")
        result = self.cli("input", "scan"); self.assertEqual(2, result["count"])
        catalog = json.loads((base / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual({"design", "coverage"}, {item["role"] for item in catalog["artifacts"]})
        self.assertTrue(all(len(item["sha256"]) == 64 for item in catalog["artifacts"]))

    def test_asset_index_and_search_uses_profiles(self) -> None:
        self.create_project(); path = self.root / "assets" / "failure-modes" / "buffer-leak.json"; path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"asset_id":"failure-buffer-leak","asset_type":"failure_mode","title":"异常路径缓冲区泄漏","tags":["resource","leak"],"profiles":["storage-common","nvme"],"status":"approved"}, ensure_ascii=False), encoding="utf-8")
        self.cli("asset", "index")
        result = self.cli("asset", "search", "--type", "failure_mode", "--profile", "nvme", "--tag", "leak")
        self.assertEqual("failure-buffer-leak", result["matches"][0]["asset_id"])

    def test_workflow_start_separates_project_workflow_run(self) -> None:
        self.create_project(); base = self.root / "inputs" / "nvme-tcp" / "design"; base.mkdir(parents=True)
        (base / "设计.md").write_text("design", encoding="utf-8"); self.cli("input", "scan")
        asset = self.root / "assets" / "feature-knowledge" / "connection.json"; asset.parent.mkdir(parents=True)
        asset.write_text(json.dumps({"asset_id":"feature-connection","asset_type":"feature_knowledge","title":"连接知识","profiles":["nvme"],"status":"approved"}, ensure_ascii=False), encoding="utf-8")
        self.cli("asset", "index")
        result = self.cli("workflow", "start", "--workflow-id", "module-full-analysis", "--target", "connection")
        run_dir = Path(result["run_dir"])
        self.assertEqual(self.root / "workspace" / "nvme-tcp" / "module-full-analysis", run_dir.parent)
        lock = json.loads((run_dir / "inputs.lock.json").read_text(encoding="utf-8"))
        self.assertEqual(1, len(lock["inputs"])); self.assertEqual(1, len(lock["assets"]))
        self.assertTrue((self.root / "outputs" / "nvme-tcp" / "module-full-analysis" / "latest.json").exists())

    def test_unmanaged_workflow_is_rejected(self) -> None:
        self.create_project()
        result = subprocess.run([sys.executable, "-m", "tooling.pangea_cli", "workflow", "start", "--workflow-id", "mr-analysis", "--target", "MR-1"], cwd=ROOT, env=self.env, text=True, capture_output=True, check=False)
        self.assertEqual(2, result.returncode); self.assertIn("尚未机器化", result.stderr)


if __name__ == "__main__": unittest.main()
