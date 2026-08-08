"""Local, provider-free tests for the evaluator composition boundary."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evaluation import composer
from runtime import fragment_runtime


class _Execution:
    def __init__(self, role: str) -> None:
        self.receipt = {"agent": role, "passed": True, "session_id": role + "-session"}


class ComposerTests(unittest.TestCase):
    def _run(self, root: Path, *, extra: bool = False) -> Path:
        run = root / "pangea-data/runs/run-1"
        (run / "internal/context-packs/frag-a").mkdir(parents=True)
        candidate = {"context_pack": {"fragment_id": "frag-a"}}
        assignment = {"fragment_id": "frag-a", "candidate_sha256": composer._hash(candidate)}
        (run / "internal/assignment-index.json").write_text(json.dumps({"artifact_type": "assignment_index", "run_id": "run-1", "payload": {"assignments": [assignment]}}))
        context = {"payload": {"candidate": candidate, "candidate_sha256": composer._hash(candidate)}}
        (run / "internal/context-packs/frag-a/CONTEXT.json").write_text(json.dumps(context))
        if extra:
            (root / "pangea-data/runs/run-2").mkdir()
        return run

    def _callbacks(self, root: Path, *, fail_role: str | None = None) -> composer.ComposerCallbacks:
        def execute(role: str, _artifacts: dict) -> _Execution:
            if role == fail_role:
                raise RuntimeError("local mock role failure")
            return _Execution(role)

        def apply(_root: Path, run_id: str, _imported: Path) -> dict:
            fact={"obligation_id":"OBL-aaaaaaaaaaaaaaaa","inventory_id":"INV-bbbbbbbbbbbbbbbb","line_start":1,"line_count":1,"excerpt_sha256":"e"*64}
            fragment = {"run_id":run_id,"fragment_id": "frag-a", "facts": [fact], "dispositions":[],
                        "contributions": {family: [] for family in fragment_runtime.CONTRIBUTION_FAMILIES}, "risk_cards": []}
            fragment["contributions"]["flows"] = [{"contribution_id": "C-cccccccccccccccc", "fact_keys": [[fact["obligation_id"],fact["inventory_id"],1,1]]}]
            target = root / "pangea-data/runs" / run_id / "internal/fragments/frag-a.json"; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"artifact_type": "fragment_artifact", "run_id": run_id, "payload": fragment}))
            return {}

        def worker(run: Path, _context: Path, _execution: _Execution) -> Path:
            target = run / "tmp/worker.json"; target.parent.mkdir(exist_ok=True); target.write_text("{}")
            receipt=run/"internal/execution-receipts"/(composer._hash(_execution.receipt)+".json"); receipt.parent.mkdir(parents=True,exist_ok=True); receipt.write_text("{}")
            return target

        def telemetry(run: Path, _managed: Path, *_args: object) -> Path:
            target=run/"internal/telemetry/frag-a.json"; target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps({"artifact_type":"runner_telemetry","run_id":"run-1","fragment_id":"frag-a"}))
            return target

        def assessment(run: Path, claim: dict, facts: list, _execution: _Execution) -> Path:
            claim_id=claim["contribution_id"]; canonical={k:claim[k] for k in sorted(claim) if k!="contribution_id"}
            value={"artifact_type":"semantic_assessment","schema_version":"1.0","claim_id":claim_id,"claim_sha256":composer._hash(canonical),"fact_keys":claim["fact_keys"],"source_excerpt_sha256s":[facts[0]["excerpt_sha256"]],"supported":True,"reason":"local source supports claim","auditor_telemetry":{"model":composer.benchmark.DEEPSEEK_MODEL,"input_tokens":1,"output_tokens":1,"finish_reason":"stop","valid_json":True,"captured_by":"opencode-runner","session_id":"local","execution_receipt_sha256":"a"*64}}
            receipt=run/"internal/execution-receipts"/(composer._hash(_execution.receipt)+".json"); receipt.parent.mkdir(parents=True,exist_ok=True); receipt.write_text("{}")
            target=run/"internal/semantic-assessments"/(claim_id+".json"); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps(value)); return target
        def judge(run: Path) -> dict:
            analysis=run/"internal/analysis-model.json"; report=run/"internal/report-model.json"; analysis.write_text("{}") ; report.write_text("{}")
            bindings=[{"path":"internal/analysis-model.json","sha256":composer.sha256(analysis.read_bytes()).hexdigest()},{"path":"internal/report-model.json","sha256":composer.sha256(report.read_bytes()).hexdigest()}]
            value={"artifact_type":"coverage_judge_r2","schema_version":"1.0","run_id":"run-1","verdict":"PASS","input_artifacts":bindings}
            target=run/"internal/coverage-judge.json"; target.write_text(json.dumps(value)); return value
        def finalize(run: Path, _inputs: dict) -> dict:
            hashes={name:composer.sha256((run/"internal"/file).read_bytes()).hexdigest() for name,file in {"analysis":"analysis-model.json","report":"report-model.json","coverage_judge":"coverage-judge.json"}.items()}
            return {"analysis_bound":True,"report_bound":True,"judge_bound":True,"final_text":"local final","bindings":hashes}
        return composer.ComposerCallbacks(
            primary_intake=lambda: {"passed": False, "failures": ["external_role_execution_required"], "executed_roles": ["primary"]},
            primary_finalize=finalize,
            build_denominator=lambda _root, _run: {}, issue_context=lambda _root, _run: {}, execute_role=execute,
            write_worker=worker, apply_fragment=apply, write_telemetry=telemetry,
            write_assessment=assessment, validate=lambda _root, _run: {"status": "verified"}, coverage_judge=judge,
            verify_attestation=lambda path, _role: path.stem,
        )

    def test_schema_invalid_callback_judge_is_not_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root)
            with self.assertRaisesRegex(composer.ComposerError, "deterministic recomputation"):
                composer.compose(root, self._callbacks(root))

    def test_rejects_primary_failure_leaf_and_extra_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root, extra=True)
            with self.assertRaises(composer.ComposerError): composer.compose(root, self._callbacks(root))
            with self.assertRaises(composer.ComposerError): composer._primary_blocked({"passed": False, "failures": ["other"]})
            with self.assertRaises(composer.ComposerError): composer._primary_blocked({"passed": False, "failures": ["external_role_execution_required"], "leaf_tasks": ["x"]})

    def test_worker_or_auditor_failure_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root)
            for role in ("analysis-worker", "auditor"):
                with self.subTest(role=role):
                    with self.assertRaises(composer.ComposerError): composer.compose(root, self._callbacks(root, fail_role=role))

    def test_missing_claim_and_stale_report_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root)
            missing = replace(self._callbacks(root), write_assessment=lambda run, *_: run / "internal/semantic-assessments/missing.json")
            with self.assertRaisesRegex(composer.ComposerError, "semantic assessments"):
                composer.compose(root, missing)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root)
            good = self._callbacks(root)
            stale = replace(good, primary_finalize=lambda _run, _inputs: {
                    "analysis_bound": True, "report_bound": True, "judge_bound": True, "final_text": "local final",
                    "bindings": {"analysis": "a" * 64, "report": "0" * 64, "coverage_judge": "0" * 64},
                })
            fixed = ({"artifact_type": "coverage_judge_r2", "verdict": "PASS"},
                     {"analysis": "1" * 64, "report": "2" * 64, "coverage_judge": "3" * 64})
            with patch.object(composer, "_fixed_judge_closure", return_value=fixed):
                with self.assertRaisesRegex(composer.ComposerError, "stale"):
                    composer.compose(root, stale)
