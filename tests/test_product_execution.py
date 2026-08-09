from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from evaluation import benchmark
from runtime import product_execution


class ProductExecutionTests(unittest.TestCase):
    def _fixture(self, count: int):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name).resolve()
        run = root / "pangea-data/runs/r2-run"
        (run / "internal/fragments").mkdir(parents=True)
        (run / "internal/telemetry").mkdir()
        contexts = {}
        assignments = []
        for ordinal in range(count):
            fragment_id = f"frag-{ordinal:04d}"
            context = run / "internal" / f"context-{ordinal:04d}.json"
            context.write_text(json.dumps({
                "payload": {"candidate": {"compact_context": {
                    "v": 1, "fragment_id": fragment_id,
                }}},
            }), encoding="utf-8")
            contexts[fragment_id] = context
            assignments.append({"fragment_id": fragment_id})
        return temporary, root, run, assignments, contexts

    def test_product_worker_batches_execute_more_than_ab_cap_and_commit_in_order(self):
        temporary, root, run, assignments, contexts = self._fixture(41)
        active = peak = 0
        lock = threading.Lock()
        committed = []

        def execute(_role, artifacts):
            nonlocal active, peak
            fragment_id = artifacts["COMPACT_CONTEXT.json"]["fragment_id"]
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.002 * (4 - int(fragment_id[-1]) % 4))
            with lock:
                active -= 1
            return fragment_id

        def write_worker(_run, _context, execution):
            imported = run / "tmp" / f"{execution}.json"
            imported.parent.mkdir(exist_ok=True)
            imported.write_text(json.dumps({"fragment_id": execution}), encoding="utf-8")
            return imported

        def apply(_root, _run_id, imported):
            value = json.loads(imported.read_text(encoding="utf-8"))
            fragment_id = value["fragment_id"]
            committed.append(fragment_id)
            (run / "internal/fragments" / f"{fragment_id}.json").write_text("{}", encoding="utf-8")
            return {"applied": True}

        def telemetry(_run, _managed, _context, execution):
            target = run / "internal/telemetry" / f"{execution}.json"
            target.write_text("{}", encoding="utf-8")
            return target

        callbacks = product_execution.ProductBatchCallbacks(
            execute_role=execute, write_worker=write_worker, apply_fragment=apply,
            write_telemetry=telemetry,
            write_assessment_batch=lambda *_args: [],
        )
        try:
            result = product_execution._execute_worker_batches(
                root, run, assignments, contexts, callbacks,
                parallelism=4, max_batches=None,
            )
        finally:
            temporary.cleanup()
        self.assertEqual(4, peak)
        self.assertEqual([row["fragment_id"] for row in assignments], committed)
        self.assertEqual(41, result["worker_completed"])
        self.assertEqual(0, result["worker_remaining"])
        self.assertTrue(result["workers_complete"])

    def test_product_worker_batch_failure_commits_no_later_result(self):
        temporary, root, run, assignments, contexts = self._fixture(6)
        committed = []

        def execute(_role, artifacts):
            fragment_id = artifacts["COMPACT_CONTEXT.json"]["fragment_id"]
            if fragment_id == "frag-0001":
                raise RuntimeError("synthetic provider failure")
            return fragment_id

        def write_worker(_run, _context, execution):
            imported = run / "tmp" / f"{execution}.json"
            imported.parent.mkdir(exist_ok=True)
            imported.write_text(json.dumps({"fragment_id": execution}), encoding="utf-8")
            return imported

        def apply(_root, _run_id, imported):
            fragment_id = json.loads(imported.read_text(encoding="utf-8"))["fragment_id"]
            committed.append(fragment_id)
            (run / "internal/fragments" / f"{fragment_id}.json").write_text("{}", encoding="utf-8")
            return {"applied": True}

        callbacks = product_execution.ProductBatchCallbacks(
            execute_role=execute, write_worker=write_worker, apply_fragment=apply,
            write_telemetry=lambda *_args: Path("unused"),
            write_assessment_batch=lambda *_args: [],
        )
        try:
            with self.assertRaisesRegex(product_execution.ProductExecutionError, "frag-0001"):
                product_execution._execute_worker_batches(
                    root, run, assignments, contexts, callbacks,
                    parallelism=4, max_batches=None,
                )
        finally:
            temporary.cleanup()
        self.assertEqual(["frag-0000"], committed)

    def test_product_worker_retries_only_bounded_output_contract_failures(self):
        temporary, root, run, assignments, contexts = self._fixture(1)
        stdout = ""
        execution = benchmark.TrustedRoleExecution(
            {"passed": True, "failures": [],
             "stdout_sha256": __import__("hashlib").sha256(stdout.encode()).hexdigest()},
            stdout, benchmark._EXECUTION_AUTHORITY,
        )
        attempts = []
        committed = []
        original = benchmark._validated_worker_execution
        validations = 0

        def execute(_role, _artifacts):
            attempts.append(1)
            return execution

        def validate(*_args):
            nonlocal validations
            validations += 1
            if validations < product_execution.PRODUCT_WORKER_ATTEMPT_LIMIT:
                raise benchmark.BenchmarkContractError(
                    "compact native claim action ordinal is invalid")
            return {}, {}, {}, {}

        def write_worker(_run, _context, _execution):
            target = run / "tmp/frag-0000.json"
            target.parent.mkdir(exist_ok=True)
            target.write_text("{}", encoding="utf-8")
            return target

        def apply(_root, _run_id, _path):
            committed.append("frag-0000")
            (run / "internal/fragments/frag-0000.json").write_text("{}", encoding="utf-8")
            return {"applied": True}

        def telemetry(*_args):
            target = run / "internal/telemetry/frag-0000.json"
            target.write_text("{}", encoding="utf-8")
            return target

        callbacks = product_execution.ProductBatchCallbacks(
            execute_role=execute, write_worker=write_worker, apply_fragment=apply,
            write_telemetry=telemetry, write_assessment_batch=lambda *_args: [],
        )
        benchmark._validated_worker_execution = validate
        try:
            result = product_execution._execute_worker_batches(
                root, run, assignments, contexts, callbacks,
                parallelism=1, max_batches=None,
            )
        finally:
            benchmark._validated_worker_execution = original
            temporary.cleanup()
        self.assertEqual(product_execution.PRODUCT_WORKER_ATTEMPT_LIMIT, len(attempts))
        self.assertEqual(["frag-0000"], committed)
        self.assertTrue(result["workers_complete"])

        self.assertEqual("action_rows_invalid",
                         product_execution._retryable_worker_output_error(
                             benchmark.BenchmarkContractError("compact native action rows are invalid")))
        self.assertEqual("expanded_contribution_invalid",
                         product_execution._retryable_worker_output_error(
                             benchmark.BenchmarkContractError("compact contribution claim is invalid")))
        self.assertIsNone(product_execution._retryable_worker_output_error(
            benchmark.BenchmarkContractError("analysis-worker CONTEXT binding mismatch")))
        retryable_summary = {
            "exit_code": 0, "model_calls_completed": 1,
            "model_requests_admitted": 1, "pre_request_budget_blocked": False,
            "event_parse_errors": 0, "event_schema_errors": 0, "native_errors": 0,
            "final_finish_reason": "stop", "truncated": False,
            "final_text_present": True, "output_payload_bound": False,
        }
        self.assertTrue(product_execution._retryable_unbound_final_json(
            {"failures": ["provider_execution_failed"]}, retryable_summary))
        changed = dict(retryable_summary, exit_code=1)
        self.assertFalse(product_execution._retryable_unbound_final_json(
            {"failures": ["provider_execution_failed"]}, changed))
        budget_summary = dict(retryable_summary,
                              exit_code=1, model_calls_completed=0,
                              pre_request_budget_blocked=True, native_errors=1,
                              final_finish_reason=None, final_text_present=False)
        self.assertTrue(product_execution._retryable_single_request_budget_block(
            {"failures": ["provider_execution_failed", "budget_exceeded"]},
            budget_summary))
        self.assertFalse(product_execution._retryable_single_request_budget_block(
            {"failures": ["provider_execution_failed"]}, budget_summary))


if __name__ == "__main__":
    unittest.main()
