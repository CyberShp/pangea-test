from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / "tests/test_e2e_v2.py"
text = path.read_text(encoding="utf-8")
old = '''from typing import Any


ROOT = Path(__file__).resolve().parents[1]
'''
new = '''from typing import Any

from tests.test_analysis_depth_contract import AnalysisDepthContractTests


ROOT = Path(__file__).resolve().parents[1]
'''
if text.count(old) != 1:
    raise SystemExit(f"import anchor count={text.count(old)}")
text = text.replace(old, new, 1)
old = '''        model_path = run_dir / "internal" / "report-model.json"
        model_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
'''
new = '''        analysis_path = self.root / "analysis-model.json"
        analysis_path.write_text(
            json.dumps(AnalysisDepthContractTests.model(run_dir), ensure_ascii=False), encoding="utf-8"
        )
        self.runctl("stage-analysis-v2", "--root", str(self.root), "--run-id", "module-complete",
                    "--file", str(analysis_path))
        draft_path = self.root / "report-model-draft.json"
        draft_path.write_text(json.dumps(model, ensure_ascii=False), encoding="utf-8")
        self.runctl("stage-report-v2", "--root", str(self.root), "--run-id", "module-complete",
                    "--file", str(draft_path))
        model_path = run_dir / "internal" / "report-model.json"
        model_sha256 = hashlib.sha256(model_path.read_bytes()).hexdigest()
'''
if text.count(old) != 1:
    raise SystemExit(f"report staging anchor count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
