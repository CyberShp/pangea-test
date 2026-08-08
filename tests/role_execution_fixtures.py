from __future__ import annotations

import copy
import json

from evaluation import benchmark
from runtime import fragment_runtime


TOKENIZED_HOOK_URI = "file://{ISOLATED_EVALUATOR_ROOT}/model-budget-hook/pre-request-budget.js"


def signed_role_attestation(
    agent: str,
    output: object,
    artifacts: dict[str, object],
    session: str,
    *,
    injected_test_runner: bool = False,
) -> dict[str, object]:
    """Build one canonical strict signed receipt for provider-free tests."""
    bindings = [
        {
            "name": name,
            "payload_sha256": fragment_runtime.digest(payload),
            "file_sha256": "9" * 64,
        }
        for name, payload in sorted(artifacts.items())
    ]
    hook_sha256 = "8" * 64
    execution_agent = {
        ("analysis-worker", frozenset({"COMPACT_CONTEXT.json"})): "analysis-leaf",
        ("auditor", frozenset({"SEMANTIC_BATCH.json"})): "audit-leaf",
    }.get((agent, frozenset(artifacts)), agent)
    plugin_closure = {
        "plugin_uri": TOKENIZED_HOOK_URI,
        "plugin_sha256": hook_sha256,
        "plugin_count": 1,
        "plugin_array_sha256": fragment_runtime.digest([TOKENIZED_HOOK_URI]),
    }
    receipt = {
        "artifact_type": "role_execution_receipt",
        "schema_version": "1.0",
        "captured_by": "evaluator",
        "agent": agent,
        "logical_role": agent,
        "execution_agent": execution_agent,
        "model": "deepseek/deepseek-v4-flash",
        "opencode_version": "1.18.4",
        "cwd_manifest_sha256": "a" * 64,
        "artifact_bindings": bindings,
        "command_sha256": "b" * 64,
        "overlay_sha256": "c" * 64,
        "resolved_config_sha256": "d" * 64,
        "resolved_permission_rules_sha256": "e" * 64,
        "output_payload_sha256": fragment_runtime.digest(output),
        "model_call_limit": 40,
        "model_budget_hook_sha256": hook_sha256,
        "model_calls_completed": 1,
        "model_requests_admitted": 1,
        "pre_request_budget_blocked": False,
        "pre_request_budget_enforced": not injected_test_runner,
        "injected_test_runner": injected_test_runner,
        "evidence_class": "test-only" if injected_test_runner else "production",
        "plugin_closure": plugin_closure,
        "resolved_plugin_closure": {
            **plugin_closure,
            "parsed": True,
            "resolved_plugin_count": 1,
            "exact": True,
        },
        "session_id": session,
        "stdout_sha256": "f" * 64,
        "exit_code": 0,
        "passed": True,
        "failures": [],
    }
    return resign_role_attestation({
        "artifact_type": "role_execution_attestation",
        "schema_version": "1.0",
        "receipt": receipt,
        "signature": "",
    })


def resign_role_attestation(attestation: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(attestation)
    raw = json.dumps(
        value["receipt"], sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()
    value["signature"] = benchmark._EXECUTION_PRIVATE_KEY.sign(raw).hex()
    return value
