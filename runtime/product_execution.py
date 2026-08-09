"""Checkpointed product execution for already-issued R2 analysis contexts.

This module is deliberately separate from the frozen A/B evaluator budget.
Every provider request keeps the per-request 200K/4096 model contract and a
one-call pre-request hook, while the number of deterministic assignments is
bounded only by the committed context publication.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Mapping

from evaluation import benchmark, composer
from runtime import analysis_pipeline, data_runtime, fragment_runtime


class ProductExecutionError(RuntimeError):
    """The product analysis batch failed closed."""


# Product execution has no A/B aggregate-call ceiling.  A finite ten-attempt
# convergence bound is cheaper than repeating the complete Run replay after a
# stochastic strict-JSON miss, while still failing closed for a persistently
# non-conforming assignment.
PRODUCT_WORKER_ATTEMPT_LIMIT = 10

_COMPACT_OUTPUT_ERROR_CATEGORIES = {
    "compact native action projection is incomplete": "action_projection_incomplete",
    "compact native action rows are invalid": "action_rows_invalid",
    "compact native claim action ordinal is invalid": "claim_action_ordinal_invalid",
    "compact native claim fallback is invalid": "claim_fallback_invalid",
    "compact native claim shape is invalid": "claim_shape_invalid",
    "compact native collection shape is invalid": "collection_shape_invalid",
    "compact native item projection is incomplete": "item_projection_incomplete",
    "compact native item rows are invalid": "item_rows_invalid",
    "compact native output closure is invalid": "output_closure_invalid",
    "compact native text cannot be safely normalized": "text_normalization_invalid",
    "compact native text has no safe token boundary": "text_boundary_invalid",
    "compact native text is invalid": "text_type_invalid",
    "compact item projection is incomplete": "expanded_item_projection_incomplete",
    "compact action projection is incomplete": "expanded_action_projection_incomplete",
    "compact item evidence is invalid": "expanded_item_evidence_invalid",
    "compact action result is invalid": "expanded_action_result_invalid",
    "compact claim is invalid": "expanded_claim_invalid",
    "compact contribution claim is invalid": "expanded_contribution_invalid",
    "compact rich-risk claim is invalid": "expanded_rich_risk_invalid",
    "compact claim kind is invalid": "expanded_claim_kind_invalid",
}


@dataclass(frozen=True)
class ProductBatchCallbacks:
    execute_role: Callable[[str, Mapping[str, Any]], Any]
    write_worker: Callable[[Path, Path, Any], Path]
    apply_fragment: Callable[[Path, str, Path], Mapping[str, Any]]
    write_telemetry: Callable[[Path, Path, Path, Any], Path]
    write_assessment_batch: Callable[[Path, dict[str, Any], Any], list[Path]]


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProductExecutionError("managed product execution artifact must be an object")
    return value


def _payload(path: Path) -> dict[str, Any]:
    value = _json(path)
    payload = value.get("payload", value)
    if not isinstance(payload, dict):
        raise ProductExecutionError("managed product execution payload must be an object")
    return payload


def _failed_execution_summary(execution: benchmark.TrustedRoleExecution) -> dict[str, Any]:
    receipt, jsonl = execution._trusted_payload()
    telemetry = benchmark.parse_jsonl_telemetry(jsonl.splitlines(True))
    return {
        "exit_code": receipt.get("exit_code"),
        "model_calls_completed": receipt.get("model_calls_completed"),
        "model_requests_admitted": receipt.get("model_requests_admitted"),
        "pre_request_budget_blocked": receipt.get("pre_request_budget_blocked"),
        "event_parse_errors": len(telemetry["parse_errors"]),
        "event_schema_errors": len(telemetry["schema_errors"]),
        "native_errors": len(telemetry["native_errors"]),
        "final_finish_reason": telemetry["final_finish_reason"],
        "max_step_input_tokens": telemetry["max_step_input_tokens"],
        "max_step_output_tokens": telemetry["max_step_output_tokens"],
        "truncated": telemetry["truncated"],
        "final_text_present": bool(telemetry["final_text"].strip()),
        "output_payload_bound": isinstance(receipt.get("output_payload_sha256"), str),
    }


def _retryable_worker_output_error(exc: benchmark.BenchmarkContractError) -> str | None:
    message = str(exc)
    if message in _COMPACT_OUTPUT_ERROR_CATEGORIES:
        return _COMPACT_OUTPUT_ERROR_CATEGORIES[message]
    if message == "invalid analysis-worker native output":
        return "native_stream_contract"
    if message == "analysis-worker final text must be fragment JSON":
        return "final_json_contract"
    return None


def _retryable_unbound_final_json(receipt: Mapping[str, Any], summary: Mapping[str, Any]) -> bool:
    return (
        receipt.get("failures") == ["provider_execution_failed"]
        and summary.get("exit_code") == 0
        and summary.get("model_calls_completed") == 1
        and summary.get("model_requests_admitted") == 1
        and summary.get("pre_request_budget_blocked") is False
        and summary.get("event_parse_errors") == 0
        and summary.get("event_schema_errors") == 0
        and summary.get("native_errors") == 0
        and summary.get("final_finish_reason") == "stop"
        and summary.get("truncated") is False
        and summary.get("final_text_present") is True
        and summary.get("output_payload_bound") is False
    )


def _retryable_single_request_budget_block(
    receipt: Mapping[str, Any], summary: Mapping[str, Any],
) -> bool:
    return (
        receipt.get("failures") == ["provider_execution_failed", "budget_exceeded"]
        and summary.get("exit_code") == 1
        and summary.get("model_calls_completed") == 0
        and summary.get("model_requests_admitted") == 1
        and summary.get("pre_request_budget_blocked") is True
        and summary.get("event_parse_errors") == 0
        and summary.get("event_schema_errors") == 0
        and summary.get("native_errors") == 1
        and summary.get("final_finish_reason") is None
        and summary.get("truncated") is False
        and summary.get("final_text_present") is False
        and summary.get("output_payload_bound") is False
    )


def _checkpoint_complete(run: Path, fragment_id: str) -> bool:
    managed = run / "internal/fragments" / f"{fragment_id}.json"
    telemetry = run / "internal/telemetry" / f"{fragment_id}.json"
    if managed.exists() != telemetry.exists():
        raise ProductExecutionError("worker recovery checkpoint is incomplete: " + fragment_id)
    if not managed.exists():
        return False
    if (managed.is_symlink() or telemetry.is_symlink()
            or not managed.is_file() or not telemetry.is_file()):
        raise ProductExecutionError("worker recovery checkpoint is invalid: " + fragment_id)
    receipt_hash = _json(telemetry).get("execution_receipt_sha256")
    if not isinstance(receipt_hash, str):
        raise ProductExecutionError("worker recovery receipt binding missing: " + fragment_id)
    try:
        composer._verify_attestation(
            run / "internal/execution-receipts" / f"{receipt_hash}.json", "analysis-worker",
        )
    except composer.ComposerError as exc:
        raise ProductExecutionError("worker recovery receipt is invalid: " + fragment_id) from exc
    return True


def _execute_worker_batches(
    root: Path,
    run: Path,
    assignments: list[dict[str, Any]],
    contexts: Mapping[str, Path],
    callbacks: ProductBatchCallbacks,
    *,
    parallelism: int,
    max_batches: int | None,
) -> dict[str, int | bool | str | None]:
    pending: list[tuple[str, Path, dict[str, Any]]] = []
    completed = 0
    for assignment in assignments:
        fragment_id = assignment["fragment_id"]
        if _checkpoint_complete(run, fragment_id):
            completed += 1
            continue
        context = contexts[fragment_id]
        compact = _payload(context).get("candidate", {}).get("compact_context")
        if not isinstance(compact, dict):
            raise ProductExecutionError("compact analysis context is missing: " + fragment_id)
        pending.append((fragment_id, context, compact))

    selected = pending if max_batches is None else pending[:max_batches * parallelism]
    committed_now = 0
    for start in range(0, len(selected), parallelism):
        batch = selected[start:start + parallelism]
        executor = ThreadPoolExecutor(max_workers=parallelism, thread_name_prefix="pangea-product-analysis")
        futures: dict[str, Future[Any]] = {}
        try:
            for fragment_id, _context, compact in batch:
                futures[fragment_id] = executor.submit(
                    callbacks.execute_role, "analysis-worker", {"COMPACT_CONTEXT.json": compact},
                )
            # Provider work is concurrent; every durable side effect is
            # committed by this thread in frozen assignment order.
            for fragment_id, context, compact in batch:
                try:
                    execution = futures[fragment_id].result()
                    attempt = 1
                    while isinstance(execution, benchmark.TrustedRoleExecution):
                        receipt, _jsonl = execution._trusted_payload()
                        if receipt.get("passed") is not True:
                            summary = _failed_execution_summary(execution)
                            retryable_receipt = (
                                _retryable_unbound_final_json(receipt, summary)
                                or _retryable_single_request_budget_block(receipt, summary)
                            )
                            if (retryable_receipt
                                    and attempt < PRODUCT_WORKER_ATTEMPT_LIMIT):
                                execution = callbacks.execute_role(
                                    "analysis-worker", {"COMPACT_CONTEXT.json": compact},
                                )
                                attempt += 1
                                continue
                            failures = receipt.get("failures")
                            categories = (sorted(set(failures)) if isinstance(failures, list)
                                          and all(isinstance(item, str) for item in failures)
                                          else ["invalid_execution_receipt"])
                            if retryable_receipt:
                                label = ("single_request_session_attempts_"
                                         if _retryable_single_request_budget_block(receipt, summary)
                                         else "final_json_contract_attempts_")
                                categories = [label + str(attempt)]
                            raise ProductExecutionError(
                                "analysis-worker provider receipt failed: " + fragment_id + ":"
                                + ",".join(categories) + ":"
                                + json.dumps(
                                    summary,
                                    sort_keys=True, separators=(",", ":"),
                                )
                            )
                        try:
                            benchmark._validated_worker_execution(run, context, execution)
                        except benchmark.BenchmarkContractError as exc:
                            category = _retryable_worker_output_error(exc)
                            if category is None or attempt >= PRODUCT_WORKER_ATTEMPT_LIMIT:
                                label = category or "non_retryable_worker_contract"
                                raise ProductExecutionError(
                                    "analysis-worker output contract failed: " + fragment_id
                                    + ":" + label + ":attempts=" + str(attempt)
                                ) from exc
                            execution = callbacks.execute_role(
                                "analysis-worker", {"COMPACT_CONTEXT.json": compact},
                            )
                            attempt += 1
                            continue
                        break
                    imported = callbacks.write_worker(run, context, execution)
                    callbacks.apply_fragment(root, run.name, imported)
                    managed = run / "internal/fragments" / f"{fragment_id}.json"
                    callbacks.write_telemetry(run, managed, context, execution)
                except ProductExecutionError:
                    raise
                except Exception as exc:
                    raise ProductExecutionError("analysis-worker execution failed: " + fragment_id) from exc
                completed += 1
                committed_now += 1
        except BaseException:
            for future in futures.values():
                future.cancel()
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        else:
            executor.shutdown(wait=True)

    total = len(assignments)
    remaining = total - completed
    next_fragment = pending[committed_now][0] if committed_now < len(pending) else None
    return {
        "worker_total": total,
        "worker_completed": completed,
        "worker_committed_now": committed_now,
        "worker_remaining": remaining,
        "next_fragment_id": next_fragment,
        "workers_complete": remaining == 0,
    }


def _semantic_claims(run: Path, assignments: list[dict[str, Any]]) -> dict[str, tuple[dict[str, Any], list[dict[str, Any]]]]:
    fragments = {
        row["fragment_id"]: _payload(run / "internal/fragments" / f"{row['fragment_id']}.json")
        for row in assignments
    }
    merged = fragment_runtime.merge_fragments([fragments[row["fragment_id"]] for row in assignments])
    claims: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for fragment in fragments.values():
        for family in fragment_runtime.CONTRIBUTION_FAMILIES:
            for claim in fragment["contributions"][family]:
                claim_id = claim["contribution_id"]
                if claim_id in claims:
                    raise ProductExecutionError("duplicate semantic claim: " + claim_id)
                claims[claim_id] = (claim, fragment["facts"])
        for claim in fragment["risk_cards"]:
            claim_id = claim["risk_id"]
            if claim_id in claims:
                raise ProductExecutionError("duplicate semantic claim: " + claim_id)
            claims[claim_id] = (claim, fragment["facts"])
    merged_ids = {
        claim.get("contribution_id", claim.get("risk_id"))
        for family in fragment_runtime.CONTRIBUTION_FAMILIES
        for claim in merged["contributions"][family]
    }
    merged_ids.update(claim["risk_id"] for claim in merged["risk_cards"])
    if not claims or set(claims) != merged_ids:
        raise ProductExecutionError("merged semantic claim closure is incomplete")
    return claims


def _execute_semantic_batches(
    run: Path,
    claims: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]],
    callbacks: ProductBatchCallbacks,
) -> dict[str, int | bool]:
    claim_ids = sorted(claims)
    completed = 0
    executed = 0
    for start in range(0, len(claim_ids), 100):
        entries: list[dict[str, Any]] = []
        for ordinal, claim_id in enumerate(claim_ids[start:start + 100]):
            claim, facts = claims[claim_id]
            keys = {tuple(key) for key in claim["fact_keys"]}
            selected = [
                fact for fact in facts
                if (fact.get("obligation_id"), fact.get("inventory_id"),
                    fact.get("line_start"), fact.get("line_count")) in keys
            ]
            entries.append({"ordinal": ordinal, "claim": claim, "facts": selected})
        existing = [run / "internal/semantic-assessments" / f"{claim_id}.json"
                    for claim_id in claim_ids[start:start + 100]]
        if any(path.exists() for path in existing):
            if not all(path.is_file() and not path.is_symlink() for path in existing):
                raise ProductExecutionError("semantic auditor recovery checkpoint is incomplete")
            completed += 1
            continue
        batch = {"v": 1, "claims": entries}
        try:
            execution = callbacks.execute_role("auditor", {"SEMANTIC_BATCH.json": batch})
            callbacks.write_assessment_batch(run, batch, execution)
        except Exception as exc:
            raise ProductExecutionError("semantic auditor batch execution failed") from exc
        completed += 1
        executed += 1
    total = (len(claim_ids) + 99) // 100
    return {
        "semantic_claims": len(claim_ids),
        "semantic_batches": total,
        "semantic_batches_completed": completed,
        "semantic_batches_executed_now": executed,
        "semantic_complete": completed == total,
    }


def _execute_product_analysis_batches(
    root: Path,
    run_id: str,
    callbacks: ProductBatchCallbacks,
    *,
    parallelism: int = 4,
    max_batches: int | None = None,
) -> dict[str, Any]:
    if type(parallelism) is not int or not 1 <= parallelism <= 4:
        raise ProductExecutionError("product analysis parallelism must be between one and four")
    if max_batches is not None and (type(max_batches) is not int or max_batches < 1):
        raise ProductExecutionError("product max-batches must be a positive integer")
    root = Path(root).resolve()
    try:
        run = data_runtime._load_run(root, run_id)[0]
        resolved_run_id, assignments, contexts = composer._assignments_and_contexts(run)
    except (OSError, composer.ComposerError) as exc:
        raise ProductExecutionError("product analysis Run publication is invalid") from exc
    if resolved_run_id != run_id:
        raise ProductExecutionError("product analysis Run binding mismatch")
    worker = _execute_worker_batches(
        root, run, assignments, contexts, callbacks,
        parallelism=parallelism, max_batches=max_batches,
    )
    result: dict[str, Any] = {
        "artifact_type": "product_analysis_batch_receipt",
        "schema_version": "1.0",
        "run_id": run_id,
        "parallelism": parallelism,
        **worker,
    }
    if not worker["workers_complete"]:
        result.update({
            "semantic_claims": 0,
            "semantic_batches": 0,
            "semantic_batches_completed": 0,
            "semantic_batches_executed_now": 0,
            "semantic_complete": False,
            "r2_status": "checkpointed",
        })
        return result
    claims = _semantic_claims(run, assignments)
    result.update(_execute_semantic_batches(run, claims, callbacks))
    try:
        validation = analysis_pipeline.validate_run_for_judge(root, run_id)
    except analysis_pipeline.PipelineError as exc:
        raise ProductExecutionError("product R2 replay validation failed") from exc
    if validation.get("status") != "verified":
        raise ProductExecutionError("product R2 replay validation did not pass")
    result["r2_status"] = "verified"
    return result


def execute_product_analysis_batches(
    root: Path,
    run_id: str,
    *,
    parallelism: int = 4,
    max_batches: int | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Execute all remaining product assignments without an A/B total cap."""
    root = Path(root).resolve()
    from runtime import analysis_modes, data_runtime
    run, _ = data_runtime._load_run(root, run_id)
    contract = data_runtime.read_json(run / "internal/task-contract.json")
    try:
        analysis_modes.require(contract, analysis_modes.LINE_OBLIGATION)
    except analysis_modes.AnalysisModeError as exc:
        raise ProductExecutionError(str(exc)) from exc
    inherited = dict(os.environ if environ is None else environ)
    # Keep the role cwd outside the project ancestry.  OpenCode discovers
    # project config while walking parent directories; placing the artifact-
    # only cwd below pangea-data would silently merge the candidate .opencode
    # tree into the frozen leaf and invalidate its exact permission closure.
    with tempfile.TemporaryDirectory(prefix="pangea-product-analysis-") as evaluator_name:
        evaluator = Path(evaluator_name).resolve()
        try:
            shared_config_home, seed_receipt = benchmark.provision_opencode_dependency_seed(
                inherited, evaluator, root,
            )
        except benchmark.BenchmarkContractError as exc:
            raise ProductExecutionError("frozen local OpenCode dependency readiness failed") from exc

        def execute_role(role: str, artifacts: Mapping[str, Any]) -> benchmark.TrustedRoleExecution:
            return benchmark.execute_isolated_role(
                role, artifacts, run=subprocess.run, environ=inherited,
                scratch_parent=evaluator, model_call_limit=1, evidence_class="production",
                shared_config_home=shared_config_home,
                dependency_seed_receipt=seed_receipt,
            )

        with analysis_pipeline.product_fragment_batch(root, run_id) as apply_one:
            callbacks = ProductBatchCallbacks(
                execute_role=execute_role,
                write_worker=benchmark.write_isolated_worker_fragment,
                apply_fragment=lambda _root, _run_id, path: apply_one(path),
                write_telemetry=benchmark.write_native_runner_telemetry,
                write_assessment_batch=benchmark.write_native_semantic_assessment_batch,
            )
            return _execute_product_analysis_batches(
                root, run_id, callbacks, parallelism=parallelism, max_batches=max_batches,
            )
