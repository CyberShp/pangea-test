"""Local, provider-free tests for the evaluator composition boundary."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from evaluation import composer
from runtime import fragment_runtime
from tests.role_execution_fixtures import signed_role_attestation


class _Execution:
    def __init__(self, role: str, artifacts: dict) -> None:
        if role=="auditor" and set(artifacts)=={"SEMANTIC_BATCH.json"}:
            batch=artifacts["SEMANTIC_BATCH.json"]
            output={"v":1,"a":[[row["ordinal"],True,"local source supports claim"] for row in batch["claims"]]}
        else:
            output={"v":1,"i":[],"a":[],"c":[]}
        self.attestation=signed_role_attestation(role,output,artifacts,role+"-session")
        self.receipt=self.attestation["receipt"]


class ComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        # These tests exercise high-level composer failure routing.  Exact
        # compact adapter projection/replay has dedicated protocol and pipeline
        # tests, so this shallow fixture supplies that already-verified boundary.
        adapter=patch.object(composer,"_compact_adapter_closure",return_value=None)
        adapter.start();self.addCleanup(adapter.stop)

    def _run(self, root: Path, *, extra: bool = False, workers: int = 1) -> Path:
        run = root / "pangea-data/runs/run-1"
        assignments = []
        for ordinal in range(workers):
            fid = "frag-" + chr(ord("a") + ordinal)
            (run / "internal/context-packs" / fid).mkdir(parents=True, exist_ok=True)
            candidate = {"context_pack": {"fragment_id": fid},"compact_context":{"v":1,"f":fid}}
            assignments.append({"fragment_id": fid, "candidate_sha256": composer._hash(candidate)})
            context = {"payload": {"candidate": candidate, "candidate_sha256": composer._hash(candidate)}}
            (run / "internal/context-packs" / fid / "CONTEXT.json").write_text(json.dumps(context))
        (run / "internal/assignment-index.json").write_text(json.dumps({"artifact_type": "assignment_index", "run_id": "run-1", "payload": {"assignments": assignments}}))
        if extra:
            (root / "pangea-data/runs/run-2").mkdir()
        return run

    def _callbacks(self, root: Path, *, fail_role: str | None = None) -> composer.ComposerCallbacks:
        def execute(role: str, _artifacts: dict) -> _Execution:
            if role == fail_role:
                raise RuntimeError("local mock role failure")
            return _Execution(role,_artifacts)

        def apply(_root: Path, run_id: str, imported: Path) -> dict:
            fid = imported.stem; suffix = fid[-1]
            fact={"obligation_id":"OBL-aaaaaaaaaaaaaaa"+suffix,"inventory_id":"INV-bbbbbbbbbbbbbbb"+suffix,"line_start":1,"line_count":1,"excerpt_sha256":"e"*64}
            fragment = {"run_id":run_id,"fragment_id": fid, "facts": [fact], "dispositions":[],
                        "contributions": {family: [] for family in fragment_runtime.CONTRIBUTION_FAMILIES}, "risk_cards": []}
            fragment["contributions"]["flows"] = [{"contribution_id": "C-ccccccccccccccc"+suffix, "fact_keys": [[fact["obligation_id"],fact["inventory_id"],1,1]]}]
            target = root / "pangea-data/runs" / run_id / "internal/fragments" / (fid + ".json"); target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({"artifact_type": "fragment_artifact", "run_id": run_id, "payload": fragment}))
            return {}

        def worker(run: Path, _context: Path, _execution: _Execution) -> Path:
            target = run / "tmp" / (_context.parent.name + ".json"); target.parent.mkdir(exist_ok=True); target.write_text("{}")
            receipt=run/"internal/execution-receipts"/(composer._hash(_execution.receipt)+".json"); receipt.parent.mkdir(parents=True,exist_ok=True); receipt.write_text(json.dumps(_execution.attestation))
            return target

        def telemetry(run: Path, _managed: Path, *_args: object) -> Path:
            target=run/"internal/telemetry"/(_managed.stem+".json"); target.parent.mkdir(parents=True,exist_ok=True); target.write_text(json.dumps({"artifact_type":"runner_telemetry","run_id":"run-1","fragment_id":_managed.stem}))
            return target

        def assessment_batch(run:Path,batch:dict,_execution:_Execution) -> list[Path]:
            receipt_hash=composer._hash(_execution.receipt)
            receipt=run/"internal/execution-receipts"/(receipt_hash+".json");receipt.parent.mkdir(parents=True,exist_ok=True)
            receipt.write_text(json.dumps(_execution.attestation));targets=[]
            for entry in batch["claims"]:
                claim=entry["claim"];facts=entry["facts"];claim_id=claim.get("contribution_id",claim.get("risk_id"))
                canonical={key:claim[key] for key in sorted(claim) if key not in {"contribution_id","risk_id"}}
                fact_map={(fact["obligation_id"],fact["inventory_id"],fact["line_start"],fact["line_count"]):fact for fact in facts}
                excerpts=[fact_map[tuple(key)]["excerpt_sha256"] for key in claim["fact_keys"]]
                value={"artifact_type":"semantic_assessment","schema_version":"1.0","claim_id":claim_id,
                       "claim_sha256":composer._hash(canonical),"fact_keys":claim["fact_keys"],
                       "source_excerpt_sha256s":excerpts,"supported":True,"reason":"local source supports claim",
                       "auditor_telemetry":{"model":composer.benchmark.DEEPSEEK_MODEL,"input_tokens":1,"output_tokens":1,
                       "finish_reason":"stop","valid_json":True,"captured_by":"opencode-runner",
                       "session_id":_execution.receipt["session_id"],"execution_receipt_sha256":receipt_hash}}
                target=run/"internal/semantic-assessments"/(claim_id+".json");target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(json.dumps(value));targets.append(target)
            return targets
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
            write_assessment_batch=assessment_batch, validate=lambda _root, _run: {"status": "verified"}, coverage_judge=judge,
            verify_attestation=lambda path, _role: path.stem,
        )

    @staticmethod
    def _fixture_fixed_judge_closure(run: Path, _run_id: str) -> tuple[dict, dict[str, str]]:
        judge = json.loads((run / "internal/coverage-judge.json").read_text())
        paths = {
            "analysis": run / "internal/analysis-model.json",
            "report": run / "internal/report-model.json",
            "coverage_judge": run / "internal/coverage-judge.json",
        }
        return judge, {
            name: composer.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()
        }

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

    def test_parallel_workers_peak_at_frozen_width_and_commit_in_assignment_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root, workers=5)
            active = [0]; peak = [0]; lock = threading.Lock()
            committed: list[tuple[str, str, str]] = []; applied: list[str] = []
            written: list[str] = []; telemetry: list[str] = []; assessment_writes = [0]
            transaction_threads: list[int] = []; provider_threads: set[int] = set()
            main_thread = threading.get_ident()

            def execute(role: str, artifacts: dict) -> _Execution:
                if role != "analysis-worker":
                    self.assertEqual("auditor", role)
                    self.assertEqual(main_thread, threading.get_ident())
                    return _Execution(role, artifacts)
                with lock:
                    provider_threads.add(threading.get_ident())
                    active[0] += 1; peak[0] = max(peak[0], active[0])
                try:
                    time.sleep(0.03)
                    return _Execution(role, artifacts)
                finally:
                    with lock:
                        active[0] -= 1

            base = self._callbacks(root)
            def worker(*args: object) -> Path:
                context = args[1]
                assert isinstance(context, Path)
                transaction_threads.append(threading.get_ident())
                written.append(context.parent.name)
                return base.write_worker(*args)  # type: ignore[arg-type]
            def apply(*args: object) -> dict:
                imported = args[2]
                assert isinstance(imported, Path)
                transaction_threads.append(threading.get_ident())
                applied.append(imported.stem)
                return base.apply_fragment(*args)  # type: ignore[arg-type]
            def write_telemetry(*args: object) -> Path:
                managed = args[1]
                assert isinstance(managed, Path)
                transaction_threads.append(threading.get_ident())
                telemetry.append(managed.stem)
                return base.write_telemetry(*args)  # type: ignore[arg-type]
            def assessment(*args: object) -> list[Path]:
                transaction_threads.append(threading.get_ident())
                assessment_writes[0] += 1
                return base.write_assessment_batch(*args)  # type: ignore[arg-type]
            def commit(role: str, artifacts: dict, _execution: _Execution, phase: str) -> None:
                transaction_threads.append(threading.get_ident())
                committed.append((
                    role,
                    artifacts.get("COMPACT_CONTEXT.json", {}).get("f", "semantic-batch"),
                    phase,
                ))
            callbacks = replace(
                base, execute_role=execute, write_worker=worker, apply_fragment=apply,
                write_telemetry=write_telemetry, write_assessment_batch=assessment,
                analysis_worker_parallelism=4,
                commit_leaf_execution=commit,
            )
            with patch.object(
                composer, "_fixed_judge_closure", side_effect=self._fixture_fixed_judge_closure,
            ):
                composer.compose(root, callbacks)
            expected = ["frag-a", "frag-b", "frag-c", "frag-d", "frag-e"]
            self.assertEqual(4, peak[0])
            self.assertEqual(
                [("analysis-worker", fid, "analysis-worker") for fid in expected]
                + [("auditor", "semantic-batch", "auditor")],
                committed,
            )
            self.assertEqual(expected, written)
            self.assertEqual(expected, applied)
            self.assertEqual(expected, telemetry)
            self.assertEqual(1, assessment_writes[0])
            self.assertTrue(provider_threads)
            self.assertNotIn(main_thread, provider_threads)
            self.assertEqual({main_thread}, set(transaction_threads))

    def test_parallel_wall_floor_accounts_for_overlap_without_summing_workers(self) -> None:
        self.assertEqual(
            18.5,
            composer._minimum_evaluator_wall_seconds(6.0, [10.0] * 5, 4),
        )
        self.assertEqual(
            26.0,
            composer._minimum_evaluator_wall_seconds(6.0, [20.0, 1.0, 1.0, 1.0], 4),
        )
        self.assertEqual(
            56.0,
            composer._minimum_evaluator_wall_seconds(6.0, [10.0] * 5, 1),
        )

    def test_parallel_worker_failure_waits_for_executor_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root, workers=5)
            active = [0]; lock = threading.Lock(); committed: list[str] = []
            written: list[str] = []; applied: list[str] = []; telemetry: list[str] = []
            def execute(role: str, artifacts: dict) -> _Execution:
                fid = artifacts["COMPACT_CONTEXT.json"]["f"]
                with lock: active[0] += 1
                try:
                    if fid == "frag-b":
                        raise RuntimeError("local worker failure")
                    time.sleep(0.02)
                    return _Execution(role, artifacts)
                finally:
                    with lock: active[0] -= 1
            base = self._callbacks(root)
            def worker(*args: object) -> Path:
                context = args[1]
                assert isinstance(context, Path)
                written.append(context.parent.name)
                return base.write_worker(*args)  # type: ignore[arg-type]
            def apply(*args: object) -> dict:
                imported = args[2]
                assert isinstance(imported, Path)
                applied.append(imported.stem)
                return base.apply_fragment(*args)  # type: ignore[arg-type]
            def write_telemetry(*args: object) -> Path:
                managed = args[1]
                assert isinstance(managed, Path)
                telemetry.append(managed.stem)
                return base.write_telemetry(*args)  # type: ignore[arg-type]
            callbacks = replace(
                base, execute_role=execute, write_worker=worker, apply_fragment=apply,
                write_telemetry=write_telemetry, analysis_worker_parallelism=4,
                commit_leaf_execution=lambda _role, artifacts, _execution, _phase:
                    committed.append(artifacts["COMPACT_CONTEXT.json"]["f"]),
            )
            with self.assertRaisesRegex(composer.ComposerError, "analysis-worker execution failed: frag-b"):
                composer.compose(root, callbacks)
            self.assertEqual(0, active[0])
            self.assertFalse(any(thread.name.startswith("pangea-analysis") for thread in threading.enumerate()))
            self.assertEqual(["frag-a"], committed)
            self.assertEqual(["frag-a"], written)
            self.assertEqual(["frag-a"], applied)
            self.assertEqual(["frag-a"], telemetry)

    def test_missing_claim_and_stale_report_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); self._run(root)
            missing = replace(self._callbacks(root), write_assessment_batch=lambda run, *_: [])
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
