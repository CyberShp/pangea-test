from pathlib import Path

root = Path(__file__).resolve().parents[2]

path = root / "tests/test_analysis_report_projection.py"
text = path.read_text(encoding="utf-8")
old = '"instrumentation_request": None, "evidence": [{"path": "driver.c", "line": 1, "fact": "error path"}],\n'
new = '"instrumentation_request": None, "evidence": [{"location": "driver.c:1", "observation": "error path"}],\n'
if text.count(old) != 1:
    raise SystemExit(f"risk evidence count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")

path = root / "tests/test_e2e_v2.py"
text = path.read_text(encoding="utf-8")
old = '''        analysis_path.write_text(
            json.dumps(AnalysisDepthContractTests.model(run_dir), ensure_ascii=False), encoding="utf-8"
        )
'''
new = '''        complete_analysis = AnalysisDepthContractTests.model(run_dir)
        for scenario in complete_analysis["test_scenarios"]:
            scenario["risk_ids"] = ["R-RECOVER"]
        for case in complete_analysis["test_cases"]:
            case["risk_ids"] = ["R-RECOVER"]
        analysis_path.write_text(json.dumps(complete_analysis, ensure_ascii=False), encoding="utf-8")
'''
if text.count(old) != 1:
    raise SystemExit(f"e2e analysis fixture count={text.count(old)}")
text = text.replace(old, new, 1)
old = '        self.assertIn(\'href="#case-TC-RECOVER"\', page)\n'
new = '        self.assertIn(\'href="#case-TC-1"\', page)\n'
if text.count(old) != 1:
    raise SystemExit(f"e2e case assertion count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
