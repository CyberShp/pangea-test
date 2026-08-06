from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import index_runtime
from tooling.pangea_cli import assetctl, indexctl, inputctl, projectctl, workflowctl


ROOT = Path(__file__).resolve().parents[1]

REPORT_MODEL = {
    "title": "预览报告",
    "task_contract": {"分析模式": "module-analysis", "目标模块": "连接", "代码版本": "abc123", "测试重点": "恢复", "排除范围": "无"},
    "code_map": [{"title": "连接", "test_explanation": "连接建立后进入接收状态。", "source_evidence": "connection.c: ready"}],
    "flows": [{"title": "连接流程", "test_explanation": "建立连接后进入接收。", "steps": ["建立连接", "进入接收"], "source_evidence": "connection.c:10"}],
    "branches": [{"title": "提前报文", "test_explanation": "接收准备前发送报文。", "source_evidence": "connection.c:20"}],
    "risks": [{"id": "R-1", "title": "恢复失败", "severity": "High", "confidence": "高", "dfx": ["资源与规格"], "translation": "Graybox-ready", "test_explanation": "压力解除后业务恢复。", "trigger": "超过上限", "propagation": "额度下降", "impact": "业务受阻", "observation": "性能指标", "recovery": "无需重启", "source_evidence": "counter increment"}],
    "scenarios": [{"scenario_id": "SC-1", "title": "规格回落", "risk_ids": ["R-1"], "description": "超过规格后回落。", "trigger": "压力超过上限", "expected": "业务恢复"}],
    "test_cases": [{"id": "TC-1", "title": "压力回落", "risk_ids": ["R-1"], "preconditions": "存在可控负载", "steps": ["提高并发至规格上限以上，再降回规格内。"], "expected": "业务恢复", "observation": "性能指标", "cleanup": "停止负载", "instrumentation": "延迟状态置位，不生成插桩代码"}],
    "unresolved": [], "next_steps": [],
}


class CliV2Tests(unittest.TestCase):
    def cli(self, *args: str, root: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        if root is not None:
            environment["PANGEA_ROOT"] = str(root)
        return subprocess.run(
            [sys.executable, "-m", "tooling.pangea_cli", *args],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_main_routes_new_domains(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tool = self.cli("tool", "setup-plan", "gitnexus", root=root)
            library = self.cli("library", "refresh-hints", root=root)
            index = self.cli("index", "all", root=root)

        self.assertEqual(0, tool.returncode, tool.stderr)
        self.assertEqual("setup_plan", json.loads(tool.stdout)["artifact_type"])
        self.assertEqual(0, library.returncode, library.stderr)
        self.assertIn("updated", json.loads(library.stdout))
        self.assertEqual(0, index.returncode, index.stderr)
        self.assertEqual([], json.loads(index.stdout)["repositories"])

    def test_workflow_registry_exposes_only_v2_entrypoints(self) -> None:
        registry = json.loads((ROOT / "registry" / "workflows.json").read_text(encoding="utf-8"))
        scenarios = json.loads((ROOT / "registry" / "scenarios.json").read_text(encoding="utf-8"))
        self.assertEqual({"mr-regression", "module-analysis"}, set(registry["workflows"]))
        self.assertEqual({"mr-regression", "module-analysis"}, set(scenarios["scenarios"]))
        self.assertNotIn("legacy_aliases", registry)
        self.assertNotIn("legacy_aliases", scenarios)

    def test_v2_cli_has_no_v1_platform_dispatch(self) -> None:
        source = (ROOT / "tooling" / "pangea_cli" / "__main__.py").read_text(encoding="utf-8")
        for retired in ("projectctl", "inputctl", "assetctl", "workflowctl"):
            self.assertNotIn(retired, source)

        for domain in ("project", "input", "asset", "workflow"):
            result = self.cli(domain, "--help")
            self.assertNotEqual(0, result.returncode)
            self.assertIn("invalid choice", result.stderr)

        for module in ("projectctl", "inputctl", "assetctl", "workflowctl"):
            retired_module = subprocess.run(
                [sys.executable, "-m", f"tooling.pangea_cli.{module}", "--help"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(2, retired_module.returncode)
            self.assertIn("已退役", retired_module.stderr)

    def test_retired_modules_expose_only_the_sentinel(self) -> None:
        retired_modules = (projectctl, inputctl, assetctl, workflowctl)
        retired_symbols = {
            "parser", "init_project", "select_project", "list_projects", "show_project",
            "detect_project", "scan_inputs", "add_input", "list_inputs", "index_assets",
            "search_assets", "show_asset", "ensure_platform_layout", "asset_record",
            "artifact_record", "parse_markdown_frontmatter",
        }
        for module in retired_modules:
            with self.subTest(module=module.__name__):
                self.assertEqual({"annotations", "main", "sys"}, {
                    name for name in vars(module) if not name.startswith("__")
                })
                for symbol in retired_symbols:
                    self.assertFalse(hasattr(module, symbol), symbol)

    def test_runctl_advertises_the_only_v2_workflow_creator(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "runtime" / "runctl.py"), "--help"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("create-v2", result.stdout)

    def test_indexctl_returns_user_error_for_runtime_failure(self) -> None:
        stderr = io.StringIO()
        with patch.object(index_runtime, "index_all", side_effect=index_runtime.IndexRuntimeError("bad index")):
            with contextlib.redirect_stderr(stderr):
                exit_code = indexctl.main(["all"])

        self.assertEqual(2, exit_code)
        self.assertEqual("ERROR: bad index\n", stderr.getvalue())

    def test_report_render_rejects_run_finals_with_or_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.json"
            model.write_text(json.dumps(REPORT_MODEL), encoding="utf-8")
            completed = root / "pangea-data" / "runs" / "completed" / "final"
            completed.mkdir(parents=True)
            (completed.parent / "manifest.json").write_text('{"state": "completed"}', encoding="utf-8")
            missing = root / "pangea-data" / "runs" / "missing-manifest" / "final"
            missing.mkdir(parents=True)

            for destination in (completed / "preview", missing / "preview"):
                result = self.cli("report", "render", "--model", str(model), "--output-dir", str(destination))
                self.assertEqual(2, result.returncode)
                self.assertIn("finalize-v2", result.stderr)

    def test_report_render_rejects_symlink_aliases_and_existing_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.json"
            model.write_text(json.dumps(REPORT_MODEL), encoding="utf-8")
            run_dir = root / "pangea-data" / "runs" / "run-1"
            run_dir.mkdir(parents=True)
            alias = root / "run-alias"
            alias.symlink_to(run_dir, target_is_directory=True)
            existing = root / "existing-preview"
            existing.mkdir()
            existing_file = root / "existing-preview-file"
            existing_file.write_text("not a directory", encoding="utf-8")
            dangling = root / "dangling-preview"
            dangling.symlink_to(root / "missing-preview")

            aliased = self.cli("report", "render", "--model", str(model), "--output-dir", str(alias / "preview"))
            exists = self.cli("report", "render", "--model", str(model), "--output-dir", str(existing))
            file_exists = self.cli("report", "render", "--model", str(model), "--output-dir", str(existing_file))
            dangling_link = self.cli("report", "render", "--model", str(model), "--output-dir", str(dangling))
            model_alias = root / "model-alias.json"
            model_alias.symlink_to(model)
            linked_model = self.cli("report", "render", "--model", str(model_alias), "--output-dir", str(root / "fresh-preview"))

        self.assertEqual(2, aliased.returncode)
        self.assertIn("finalize-v2", aliased.stderr)
        self.assertEqual(2, exists.returncode)
        self.assertIn("必须尚不存在", exists.stderr)
        self.assertEqual(2, file_exists.returncode)
        self.assertIn("必须尚不存在", file_exists.stderr)
        self.assertEqual(2, dangling_link.returncode)
        self.assertIn("必须尚不存在", dangling_link.stderr)
        self.assertEqual(2, linked_model.returncode)
        self.assertIn("符号链接报告模型", linked_model.stderr)

    def test_report_render_writes_a_new_preview_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "model.json"
            model.write_text(json.dumps(REPORT_MODEL), encoding="utf-8")
            preview = root / "preview"

            result = self.cli("report", "render", "--model", str(model), "--output-dir", str(preview))
            self.assertTrue((preview / "report.md").is_file())
            self.assertTrue((preview / "report.html").is_file())

        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
