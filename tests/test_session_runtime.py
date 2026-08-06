from __future__ import annotations

import unittest
from pathlib import Path
import tempfile
import json
import os
import subprocess
import sys
from unittest.mock import patch

from runtime import data_runtime


class SessionRuntimeTests(unittest.TestCase):
    def test_state_machine_exposes_chinese_display_and_rejects_terminal_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = {"schema_version": "1.0", "mode": "mr_regression", "goal": "回归 MR",
                        "target": "target", "repositories": ["repo"], "analysis_depth": "focused",
                        "mr_url": "https://mr.example.invalid/1",
                        "repository_commits": {"repo": "0" * 40}, "created_by": "pangea-test"}
            data_runtime.create_run(root, "run-state", contract)
            manifest = json.loads((root / "pangea-data" / "runs" / "run-state" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual({"rounds": 0, "max_rounds": 2, "status": "pending", "opinion_file": None,
                              "required_actions": []}, manifest["audit"])
            expected_display = {
                "mapping": ("梳理中", "(._.)"),
                "analyzing": ("分析中", "(｀・ω・´)"),
                "mining": ("挖掘中", "(ง •̀_•́)ง"),
                "reviewing": ("审核中", "(¬_¬)"),
                "waiting": ("发呆中", "(－_－)"),
                "degraded": ("难过中", "(；へ：)"),
                "escalated": ("狂躁中", "(╬ಠ益ಠ)"),
                "completed": ("高兴中", "(￣▽￣)b"),
            }
            self.assertEqual(expected_display, {
                state: (display["label"], display["face"])
                for state, display in data_runtime.STATUS.items()
            })
            event = data_runtime.set_run_state(root, "run-state", "mining", "检查资源额度")
            self.assertEqual("挖掘中", event["display"]["label"])
            self.assertEqual("(ง •̀_•́)ง", event["display"]["face"])
            repeated = data_runtime.set_run_state(root, "run-state", "mining", "继续检查资源额度")
            self.assertTrue(repeated["deduplicated"])
            self.assertEqual("检查资源额度", repeated["message"])
            events = (root / "pangea-data" / "runs" / "run-state" / "internal" / "state-events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(events))
            with self.assertRaises(data_runtime.DataRuntimeError):
                data_runtime.set_run_state(root, "run-state", "unknown", "x")

    def test_data_cli_rejects_create_run_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"schema_version": "1.0", "mode": "module_analysis", "goal": "模块", "target": "target", "repositories": ["repo"], "analysis_depth": "complete", "created_by": "pangea-test"}
            env = os.environ.copy(); env["PANGEA_VALIDATOR"] = "stdlib"
            result = subprocess.run(
                [sys.executable, "-m", "tooling.pangea_cli", "data", "--root", str(root), "create-run", "--run-id", "run-cli-shape", "--json", json.dumps(payload, ensure_ascii=False)],
                cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(0, result.returncode)
            self.assertIn("invalid choice", result.stderr)
            self.assertFalse((root / "pangea-data" / "runs" / "run-cli-shape" / "manifest.json").exists())

    def test_skipped_checkpoint_requires_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = {"schema_version": "1.0", "mode": "module_analysis", "goal": "模块",
                        "target": "target", "repositories": ["repo"], "analysis_depth": "fast",
                        "created_by": "pangea-test"}
            data_runtime.create_run(root, "skip-run", contract)
            with self.assertRaises(data_runtime.DataRuntimeError):
                data_runtime.append_checkpoint(root, "skip-run", {"stage": "specialist", "status": "skipped",
                    "facts": [], "open_items": [], "next_step": "继续"})
            checkpoint = data_runtime.append_checkpoint(root, "skip-run", {"stage": "specialist", "status": "skipped",
                "skip_reason": "未命中专项信号", "facts": [], "open_items": [], "next_step": "继续"})
            self.assertEqual("skipped", checkpoint["status"])

    def test_checkpoint_append_rejects_count_rollback_without_overwriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = {"schema_version": "1.0", "mode": "module_analysis", "goal": "模块", "target": "target",
                        "repositories": ["repo"], "analysis_depth": "complete", "created_by": "pangea-test"}
            created = data_runtime.create_run(root, "history", contract)
            payload = {"stage": "code_map", "facts": [{"summary": "入口调用链已定位", "evidence": "a.c:1 记录入口源码锚点"}],
                       "open_items": [], "next_step": "继续"}
            data_runtime.append_checkpoint(root, "history", payload)
            run_dir = Path(created["run_dir"])
            first = run_dir / "checkpoints" / "001-code_map.json"
            before = first.read_bytes()
            manifest = data_runtime.read_json(run_dir / "manifest.json")
            manifest["checkpoint_count"] = 0
            data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
            with self.assertRaisesRegex(data_runtime.DataRuntimeError, "checkpoint 文件数量"):
                data_runtime.append_checkpoint(root, "history", payload)
            self.assertEqual(before, first.read_bytes())

    def test_checkpoint_append_validates_history_provenance_and_exclusive_target(self) -> None:
        contract = {"schema_version": "1.0", "mode": "module_analysis", "goal": "模块", "target": "target",
                    "repositories": ["repo"], "analysis_depth": "complete", "created_by": "pangea-test"}
        payload = {"stage": "code_map", "facts": [{"summary": "入口调用链已定位", "evidence": "a.c:1 记录入口源码锚点"}],
                   "open_items": [], "next_step": "继续"}
        for field, value, message in (("run_id", "other", "run_id"), ("sequence", 2, "sequence"), ("stage", "flow", "stage")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                created = data_runtime.create_run(root, "tampered", contract)
                data_runtime.append_checkpoint(root, "tampered", payload)
                checkpoint_path = Path(created["run_dir"]) / "checkpoints" / "001-code_map.json"
                checkpoint = data_runtime.read_json(checkpoint_path)
                checkpoint[field] = value
                data_runtime.atomic_write_json(checkpoint_path, checkpoint)
                before = checkpoint_path.read_bytes()
                with self.assertRaisesRegex(data_runtime.DataRuntimeError, message):
                    data_runtime.append_checkpoint(root, "tampered", payload)
                self.assertEqual(before, checkpoint_path.read_bytes())

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            created = data_runtime.create_run(root, "exclusive", contract)
            target_content = b"occupied"
            real_write = data_runtime._write_json_exclusive

            def occupy_target(path, value):
                Path(path).write_bytes(target_content)
                return real_write(path, value)

            with patch("runtime.data_runtime._write_json_exclusive", side_effect=occupy_target):
                with self.assertRaisesRegex(data_runtime.DataRuntimeError, "拒绝覆盖"):
                    data_runtime.append_checkpoint(root, "exclusive", payload)
            target = Path(created["run_dir"]) / "checkpoints" / "001-code_map.json"
            self.assertEqual(target_content, target.read_bytes())
            self.assertEqual(0, data_runtime.read_json(Path(created["run_dir"]) / "manifest.json")["checkpoint_count"])


if __name__ == "__main__":
    unittest.main()
