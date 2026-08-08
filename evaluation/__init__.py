"""Evaluator-owned benchmark helpers and sealed OpenCode runner.

Nothing in this package is imported by the PANGEA production runtime.  In
particular, the evaluator is responsible for keeping private answers out of a
candidate agent's filesystem and prompt.  ``execute_opencode`` performs an
OpenCode version/agent-resolution preflight, applies the frozen fair-track
policy, and validates native JSONL telemetry.  It does not clone repositories
or invoke a model provider itself.
"""

from .benchmark import (
    apply_adapter_review,
    BenchmarkContractError,
    RunReceipt,
    RunSpec,
    build_opencode_command,
    evaluate_gates,
    execute_opencode,
    load_corpus_manifest,
    load_frozen_config,
    load_sealed_oracle,
    normalize_candidate_output,
    parse_jsonl_telemetry,
    score_dimensions,
    validate_public_bundle,
)
from .composer import ComposerCallbacks, ComposerError, compose, compose_complete_run
from .pangea_execution import PangeaExecutionError, execute_pangea_as_shipped

__all__ = [
    "apply_adapter_review",
    "BenchmarkContractError",
    "RunReceipt",
    "RunSpec",
    "build_opencode_command",
    "evaluate_gates",
    "execute_opencode",
    "load_corpus_manifest",
    "load_frozen_config",
    "load_sealed_oracle",
    "normalize_candidate_output",
    "parse_jsonl_telemetry",
    "score_dimensions",
    "validate_public_bundle",
    "ComposerCallbacks",
    "ComposerError",
    "compose",
    "compose_complete_run",
    "PangeaExecutionError",
    "execute_pangea_as_shipped",
]
