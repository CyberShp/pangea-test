"""Production evaluator wiring for a complete PANGEA as-shipped run.

Callers provide a frozen :class:`RunSpec`; they do not assemble composer
callbacks.  Primary phases and every leaf role are separate OpenCode
processes owned by the evaluator.  Tests may pass a local subprocess-shaped
runner, but no model provider is contacted by this module itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import contextlib
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping

from evaluation import benchmark, composer
from runtime import runctl


class PangeaExecutionError(composer.ComposerError):
    """The production evaluator path failed closed."""


def _canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


_PRIMARY_PHASES = ("intake", "resume", "finalize")
_PRIMARY_TELEMETRY_FIELDS = (
    "model_calls", "tool_calls", "input_tokens", "output_tokens",
    "max_step_input_tokens", "max_step_output_tokens", "finish_reasons",
    "final_finish_reason", "finish_reason_observed", "session_ids", "truncated",
    "model_call_limit", "model_calls_completed", "model_requests_admitted",
    "pre_request_budget_blocked", "pre_request_budget_enforced", "injected_test_runner",
    "tool_input_policy_violation_summary",
)


def _initialize_authoritative_staged_workspace(
    root: Path, binding: benchmark._ValidatedPublicBundleBinding,
) -> bool:
    """Initialize only the exact empty managed root emitted by corpus staging."""
    from runtime import data_runtime

    workspace = root / "pangea-data"
    scratch = workspace / ".evaluator-scratch"
    expected_entries = {"pangea-data", "pangea-data/.evaluator-scratch"}
    try:
        workspace_stat = workspace.lstat(); scratch_stat = scratch.lstat()
        members = set(os.listdir(workspace))
        scratch_members = set(os.listdir(scratch))
        manifest_payload = benchmark._stable_regular_file_bytes(
            root / "public-bundle-manifest.json", "public bundle manifest", read_only=True,
        )
        manifest = json.loads(manifest_payload.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, benchmark.BenchmarkContractError):
        return False
    snapshot_entries = {
        value for value in binding.snapshot.get("entries", [])
        if isinstance(value, str) and value.split("/", 1)[0] == "pangea-data"
    }
    snapshot_files = {
        value for value in binding.snapshot.get("files", {})
        if isinstance(value, str) and value.split("/", 1)[0] == "pangea-data"
    }
    manifest_directories = {
        value for value in manifest.get("directories", [])
        if isinstance(value, str) and value.split("/", 1)[0] == "pangea-data"
    } if isinstance(manifest.get("directories"), list) else set()
    manifest_files = {
        value for value in manifest.get("files", {})
        if isinstance(value, str) and value.split("/", 1)[0] == "pangea-data"
    } if isinstance(manifest.get("files"), dict) else set()
    if (binding.root != root or binding.managed_root != "pangea-data"
            or members != {".evaluator-scratch"} or scratch_members
            or not stat.S_ISDIR(workspace_stat.st_mode) or not stat.S_ISDIR(scratch_stat.st_mode)
            or stat.S_ISLNK(workspace_stat.st_mode) or stat.S_ISLNK(scratch_stat.st_mode)
            or workspace_stat.st_uid != os.geteuid() or scratch_stat.st_uid != os.geteuid()
            or workspace_stat.st_mode & 0o022 or scratch_stat.st_mode & 0o022
            or snapshot_entries != expected_entries or snapshot_files
            or manifest_directories != expected_entries or manifest_files):
        return False
    workspace.chmod(stat.S_IMODE(workspace_stat.st_mode) | 0o700)
    workspace = data_runtime.ensure_layout(root)
    session = workspace / "session"
    receipt_path = session / "preflight-receipt.json"
    if os.path.lexists(receipt_path):
        raise PangeaExecutionError("staged workspace preflight replacement is not permitted")
    data_runtime.atomic_write_json(receipt_path, {
        "artifact_type": "preflight_receipt", "schema_version": "1.0",
        "created_at": data_runtime.utc_now(), "status": "ready",
        "project_root": str(root), "data_root": str(workspace),
        "repository_root": str(workspace / "repositories"), "known_repositories": [],
        "allowed_next_actions": ["draft_contract"], "python_executable": sys.executable,
        "step_results": {}, "step_errors": {},
    })
    return True


def _prepare_evaluator_intake(
    spec: benchmark.RunSpec,
    root: Path,
    binding: benchmark._ValidatedPublicBundleBinding,
) -> dict[str, Any]:
    """Create and confirm the one canonical contract before OpenCode starts."""
    from runtime import data_runtime

    try:
        benchmark._validate_runspec_case_binding(spec, binding)
        case_payload = benchmark._stable_regular_file_bytes(root / "CASE.json", "canonical CASE", read_only=True)
        case = json.loads(case_payload.decode("utf-8"))
    except (benchmark.BenchmarkContractError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PangeaExecutionError("canonical case intake binding failed") from exc
    workspace = root / "pangea-data"
    session = workspace / "session"
    contracts = workspace / "contracts"
    runs = workspace / "runs"
    intake_spec_path = workspace / runctl.EVALUATOR_INTAKE_SPEC_RELATIVE
    if _initialize_authoritative_staged_workspace(root, binding) is not True:
        raise PangeaExecutionError(
            "evaluator intake requires zero candidate-prepared spec, contract, and Run state"
        )
    try:
        if (session.is_symlink() or not session.is_dir()
                or set(os.listdir(session)) != {"preflight-receipt.json"}
                or contracts.is_symlink()
                or (contracts.exists() and (not contracts.is_dir() or bool(list(contracts.iterdir()))))
                or runs.is_symlink() or not runs.is_dir() or list(runs.iterdir())
                or os.path.lexists(intake_spec_path)):
            raise PangeaExecutionError("evaluator intake requires zero candidate-prepared spec, contract, and Run state")
    except OSError as exc:
        raise PangeaExecutionError("evaluator intake state closure is unavailable") from exc
    repository = case["repository_id"]
    try:
        stage_payload = benchmark._stable_regular_file_bytes(
            root / "stage-receipt.json", "public stage receipt", read_only=True,
        )
        stage_receipt = json.loads(stage_payload.decode("utf-8"))
        benchmark._validate_stage_receipt(
            stage_receipt, root=root, candidate_manifest_sha256=spec.candidate_manifest_sha256,
        )
        bundle_manifest, _ = runctl._stable_unique_json(root / "public-bundle-manifest.json", "public bundle manifest")
    except (runctl.RunCtlError, benchmark.BenchmarkContractError,
            UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PangeaExecutionError("canonical staged repository binding failed") from exc
    repository_rows = stage_receipt["repositories"]
    staged_repository = next((row for row in repository_rows
                              if row["id"] == repository), None)
    files = bundle_manifest.get("files")
    directories = bundle_manifest.get("directories")
    prefix = f"repositories/{repository}"
    source_files = [{"path": path, "sha256": digest} for path, digest in sorted(files.items())
                    if path.startswith(prefix + "/")] if isinstance(files, dict) else []
    source_directories = sorted(path for path in directories
                                if path == prefix or path.startswith(prefix + "/")) if isinstance(directories, list) else []
    if (not isinstance(staged_repository, dict)
            or staged_repository["commit"] != case["frozen_commit"]
            or staged_repository["materialization_version"] != "git-object-v1"
            or not source_files or prefix not in source_directories):
        raise PangeaExecutionError("canonical staged repository materialization differs from CASE.json")
    source_manifest = {"files": source_files, "directories": source_directories}
    case_sha256 = sha256(case_payload).hexdigest()
    if case_sha256 != spec.public_case_sha256:
        raise PangeaExecutionError("canonical case hash differs from RunSpec")
    contract_id = "case-" + case_sha256
    source_paths = case["source_scope"]["paths"]
    contract_projection = case["contract"]
    draft_args = argparse.Namespace(
        root=str(root), scenario=contract_projection["scenario"], target=case["id"],
        repository=[repository], repository_commit=None,
        source_scope=[f"{repository}={path}" for path in source_paths],
        contract_id=contract_id, mr_url=None, goal=case["agent_input"],
        analysis_depth=contract_projection["analysis_depth"], version=case["frozen_commit"],
        topology=case["repository_url"], test_focus=list(case["source_scope"].get("symbol_hints", [])),
        input_ref=[], exclude=[case["safety_boundary"]], tool_gap=[], known_gap=[], signal=[],
        resource_emphasis=False, created_by="evaluator",
        _evaluator_contract={
            "repository_commit": case["frozen_commit"],
            "source_manifest_sha256": _canonical_hash(source_manifest),
        },
    )
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            runctl.draft_contract_v2(draft_args)
        draft_result = json.loads(output.getvalue())
        output.seek(0); output.truncate(0)
        confirm_args = argparse.Namespace(
            root=str(root), contract_id=contract_id, revision=1,
            source=contract_projection["confirmation_source"],
            materials_status=contract_projection["materials_status"], note=None,
        )
        with contextlib.redirect_stdout(output):
            runctl.confirm_contract_v2(confirm_args)
        confirm_result = json.loads(output.getvalue())
    except (runctl.RunCtlError, json.JSONDecodeError, OSError) as exc:
        raise PangeaExecutionError("evaluator contract preparation failed") from exc
    finally:
        output.close()
    record_path = contracts / contract_id / "contract.json"
    record, _ = runctl._stable_unique_json(record_path, "evaluator confirmed contract")
    expected_contract = record.get("task_contract")
    confirmation = record.get("confirmation")
    if (draft_result.get("contract_id") != contract_id or draft_result.get("status") != "draft"
            or confirm_result.get("contract_id") != contract_id or confirm_result.get("status") != "confirmed"
            or record.get("status") != "confirmed" or record.get("revision") != 1
            or record.get("contract_id") != contract_id or record.get("confirmation_required") is not True
            or not isinstance(confirmation, dict)
            or confirmation.get("source") != contract_projection["confirmation_source"]
            or confirmation.get("materials_status") != contract_projection["materials_status"]
            or expected_contract != draft_result.get("task_contract")
            or expected_contract.get("repository_commits") != {repository: case["frozen_commit"]}
            or expected_contract.get("source_scopes") != {repository: sorted(source_paths)}
            or expected_contract.get("version") != case["frozen_commit"]
            or expected_contract.get("topology") != case["repository_url"]):
        raise PangeaExecutionError("evaluator prepared contract does not exactly project CASE.json")
    record_path.chmod(0o400)
    persisted_record, _ = runctl._stable_unique_json(
        record_path, "evaluator confirmed contract", read_only=True,
    )
    if persisted_record != record:
        raise PangeaExecutionError("evaluator confirmed contract stable read failed")
    record_sha256 = runctl._canonical_json_sha256(persisted_record)
    task_payload = benchmark._stable_regular_file_bytes(root / "TASK.md", "canonical TASK")
    source_scope_sha256 = runctl._canonical_json_sha256({repository: sorted(source_paths)})
    intake_spec = {
        "artifact_type": "evaluator_intake_spec", "schema_version": "2.0",
        "owner": "evaluator", "mode": "canonical-case-one-shot-v2",
        "case": {"path": "CASE.json", "id": case["id"], "sha256": case_sha256},
        "task": {"path": "TASK.md", "sha256": sha256(task_payload).hexdigest(),
                 "agent_input_sha256": sha256(case["agent_input"].encode("utf-8")).hexdigest()},
        "contract": {
            "id": contract_id, "record_path": f"pangea-data/contracts/{contract_id}/contract.json",
            "confirmed_sha256": record_sha256, "revision": 1,
            "task_contract_sha256": runctl._canonical_json_sha256(expected_contract),
            "confirmation_source": contract_projection["confirmation_source"],
            "materials_status": contract_projection["materials_status"],
        },
        "repository": {
            "id": repository, "url": case["repository_url"], "commit": case["frozen_commit"],
            "git_tree": staged_repository["git_tree"],
            "materialization_sha256": staged_repository["materialization_sha256"],
            "stage_repository_sha256": _canonical_hash(staged_repository),
            "source_manifest_sha256": _canonical_hash(source_manifest),
        },
        "source_scope_sha256": source_scope_sha256, "expected_run_id": contract_id,
    }
    try:
        runctl._validate_evaluator_intake_spec_value(intake_spec)
    except runctl.RunCtlError as exc:
        raise PangeaExecutionError("evaluator intake spec construction failed") from exc
    if os.path.lexists(intake_spec_path):
        raise PangeaExecutionError("evaluator intake spec replacement is not permitted")
    data_runtime.atomic_write_json(intake_spec_path, intake_spec)
    intake_spec_path.chmod(0o400)
    validated, spec_sha256, validated_record = runctl._validate_evaluator_intake_spec(root)
    if validated != intake_spec or validated_record != record_path:
        raise PangeaExecutionError("evaluator intake spec stable validation failed")
    return {
        "contract_id": contract_id, "expected_run_id": contract_id,
        "input_bindings": runctl._evaluator_intake_input_bindings(
            intake_spec, spec_sha256, record_sha256,
        ),
    }


def _primary_receipt_payload(
    phase: str, receipt: benchmark.RunReceipt, evidence_class: str,
    input_bindings: list[dict[str, str]],
) -> dict[str, Any]:
    """Return the durable, non-secret evaluator record for one primary phase."""
    if phase not in _PRIMARY_PHASES or not isinstance(receipt, benchmark.RunReceipt):
        raise PangeaExecutionError("invalid typed primary receipt")
    telemetry = receipt.telemetry if isinstance(receipt.telemetry, dict) else {}
    return {
        "artifact_type": "primary_run_receipt",
        "schema_version": "1.1",
        "captured_by": "evaluator",
        "evidence_class": evidence_class,
        "phase": phase,
        "candidate": receipt.candidate,
        "track": receipt.track,
        "case_id": receipt.case_id,
        "command_sha256": _canonical_hash(receipt.command),
        "exit_code": receipt.exit_code,
        "duration_seconds": receipt.duration_seconds,
        "preflight_sha256": _canonical_hash(receipt.preflight),
        "policy_sha256": _canonical_hash(receipt.policy_receipt),
        "phase_prompt_sha256": receipt.policy_receipt.get("phase_prompt_sha256"),
        "model_budget_hook_sha256": receipt.policy_receipt.get("model_budget_hook_sha256"),
        "input_bindings": input_bindings,
        "output_sha256": sha256(str(telemetry.get("final_text", "")).encode()).hexdigest(),
        "telemetry": {key: telemetry.get(key) for key in _PRIMARY_TELEMETRY_FIELDS},
        "passed": receipt.passed,
        "failures": list(receipt.failures),
    }


def _persist_primary_receipt_directory(
    directory: Path, phase: str, receipt: benchmark.RunReceipt, evidence_class: str,
    input_bindings: list[dict[str, str]],
) -> str:
    """Exclusively persist and stably re-read one evaluator-owned receipt."""
    payload = _primary_receipt_payload(phase, receipt, evidence_class, input_bindings)
    expected_before = {f"{name}.json" for name in _PRIMARY_PHASES[:_PRIMARY_PHASES.index(phase)]}
    try:
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise PangeaExecutionError("primary receipt directory is invalid")
        else:
            directory.mkdir(mode=0o700, parents=True)
        os.chmod(directory, 0o700)
        target = directory / f"{phase}.json"
        if os.path.lexists(target):
            raise PangeaExecutionError("primary receipt replacement is not permitted")
        members = list(directory.iterdir())
        if (any(member.is_symlink() or not member.is_file() or not stat.S_ISREG(member.stat().st_mode)
                for member in members)
                or {member.name for member in members} != expected_before):
            raise PangeaExecutionError("primary receipt member closure is not exact")
        encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        with tempfile.NamedTemporaryFile("wb", dir=directory, delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.link(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        os.chmod(target, 0o400)
        directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                               | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(directory_fd)
            descriptor = os.open(target.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                                 dir_fd=directory_fd)
            try:
                before = os.fstat(descriptor)
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1024 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                after = os.fstat(descriptor)
                if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                        or before.st_mode & 0o222 or before.st_nlink != 1
                        or (before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
                            before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        != (after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
                            after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        or b"".join(chunks) != encoded):
                    raise PangeaExecutionError("primary receipt stable read failed")
            finally:
                os.close(descriptor)
            if set(os.listdir(directory_fd)) != expected_before | {target.name}:
                raise PangeaExecutionError("primary receipt member closure is not exact")
        finally:
            os.close(directory_fd)
    except PangeaExecutionError:
        raise
    except OSError as exc:
        raise PangeaExecutionError("primary receipt persistence failed") from exc
    return sha256(encoded).hexdigest()


def _persist_primary_receipt(evaluator_root: Path, phase: str,
                             receipt: benchmark.RunReceipt, evidence_class: str = "production",
                             input_bindings: list[dict[str, str]] | None = None) -> str:
    """Persist the pre-validation evaluator intake record."""
    if input_bindings is None:
        input_bindings = [{"name": "phase_prompt", "sha256": str(
            receipt.policy_receipt.get("phase_prompt_sha256", ""))}]
    return _persist_primary_receipt_directory(
        evaluator_root / "primary-receipts", phase, receipt, evidence_class, input_bindings,
    )


def _persist_managed_primary_receipt(run: Path, phase: str,
                                     receipt: benchmark.RunReceipt, evidence_class: str,
                                     input_bindings: list[dict[str, str]] | None = None) -> str:
    """Persist the durable copy inside the one evaluator-selected managed Run."""
    run = Path(run)
    internal = run / "internal"
    try:
        if (run.is_symlink() or run.resolve(strict=True) != run
                or internal.is_symlink() or not internal.is_dir()
                or internal.resolve(strict=True) != internal
                or internal.stat().st_uid != os.geteuid()):
            raise PangeaExecutionError("managed primary receipt path is not closed inside the Run")
    except OSError as exc:
        raise PangeaExecutionError("managed primary receipt path is unavailable") from exc
    if input_bindings is None:
        input_bindings = [{"name": "phase_prompt", "sha256": str(
            receipt.policy_receipt.get("phase_prompt_sha256", ""))}]
    return _persist_primary_receipt_directory(
        internal / "primary-receipts", phase, receipt, evidence_class, input_bindings,
    )


@dataclass
class _AggregateBudget:
    runtime: Mapping[str, Any]
    max_tool_calls: int
    started_at: float = field(default_factory=time.monotonic)
    clock: Callable[[], float] = time.monotonic
    evidence_class: str = "production"
    model_calls: int = 0
    model_requests_admitted: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    sessions: set[str] = field(default_factory=set)
    phases: list[dict[str, Any]] = field(default_factory=list)
    primary_receipt_sha256s: dict[str, str] = field(default_factory=dict)
    intake_input_bindings: list[dict[str, str]] = field(default_factory=list)

    def bind_primary_receipt(self, phase: str, receipt_sha256: str) -> None:
        if phase not in _PRIMARY_PHASES or phase in self.primary_receipt_sha256s:
            raise PangeaExecutionError("primary receipt binding is invalid")
        self.primary_receipt_sha256s[phase] = receipt_sha256

    def remaining_model_calls(self) -> int:
        remaining = self.runtime["max_model_calls"] - self.model_requests_admitted
        if remaining < 0:
            raise PangeaExecutionError("aggregate frozen model budget exceeded")
        return remaining

    def _add(self, role: str, phase: str, telemetry: Mapping[str, Any], duration: float,
             receipt_hash: str, command_hash: str, output_hash: str,
             input_bindings: list[dict[str, str]], *, expected_model_limit: int | None = None) -> None:
        sessions = telemetry.get("session_ids")
        if not isinstance(sessions, list) or len(sessions) != 1 or not isinstance(sessions[0], str):
            raise PangeaExecutionError(f"{phase} has no exact session binding")
        session = sessions[0]
        if session in self.sessions:
            raise PangeaExecutionError("execution session was reused across phases")
        for key in ("model_calls", "tool_calls", "input_tokens", "output_tokens"):
            if type(telemetry.get(key)) is not int or telemetry[key] < 0:
                raise PangeaExecutionError(f"{phase} has invalid aggregate telemetry")
        expected_limit = self.remaining_model_calls() if expected_model_limit is None else expected_model_limit
        if (type(telemetry.get("model_call_limit")) is not int
                or telemetry["model_call_limit"] != expected_limit
                or telemetry["model_call_limit"] < 1):
            raise PangeaExecutionError(f"{phase} did not receive the aggregate remaining model budget")
        admitted = telemetry.get("model_requests_admitted")
        completed = telemetry.get("model_calls_completed")
        if (telemetry["model_calls"] < 1
                or type(completed) is not int or completed != telemetry["model_calls"]
                or type(admitted) is not int or admitted < completed or admitted > expected_limit
                or telemetry.get("pre_request_budget_blocked") is not False):
            raise PangeaExecutionError(f"{phase} has invalid pre-request model telemetry")
        next_model_calls = self.model_calls + telemetry["model_calls"]
        next_model_requests_admitted = self.model_requests_admitted + admitted
        next_tool_calls = self.tool_calls + telemetry["tool_calls"]
        next_input_tokens = self.input_tokens + telemetry["input_tokens"]
        next_output_tokens = self.output_tokens + telemetry["output_tokens"]
        if (next_model_requests_admitted > self.runtime["max_model_calls"]
                or next_tool_calls > self.max_tool_calls
                or next_input_tokens > self.runtime["context_window"] * self.runtime["max_model_calls"]
                or next_output_tokens > self.runtime["max_output_tokens"] * self.runtime["max_model_calls"]
                or telemetry.get("max_step_input_tokens", 0) > self.runtime["context_window"]
                or telemetry.get("max_step_output_tokens", 0) > self.runtime["max_output_tokens"]
                or telemetry.get("truncated") is True):
            raise PangeaExecutionError("aggregate frozen model budget exceeded")
        self.check_wall()
        phase_row = {
            "phase": phase, "role": role, "evidence_class": self.evidence_class, "session_id": session,
            "receipt_sha256": receipt_hash, "command_sha256": command_hash,
            "input_bindings": input_bindings, "output_sha256": output_hash,
            "telemetry": {
                "model_calls": telemetry["model_calls"], "tool_calls": telemetry["tool_calls"],
                "model_requests_admitted": admitted,
                "model_calls_completed": telemetry.get("model_calls_completed"),
                "model_call_limit": telemetry["model_call_limit"],
                "pre_request_budget_blocked": telemetry.get("pre_request_budget_blocked"),
                "pre_request_budget_enforced": telemetry.get("pre_request_budget_enforced"),
                "injected_test_runner": telemetry.get("injected_test_runner"),
                "input_tokens": telemetry["input_tokens"], "output_tokens": telemetry["output_tokens"],
                "max_step_input_tokens": telemetry.get("max_step_input_tokens"),
                "max_step_output_tokens": telemetry.get("max_step_output_tokens"),
                "truncated": telemetry.get("truncated"),
                "duration_seconds": duration,
            },
        }
        self.sessions.add(session)
        self.model_calls = next_model_calls
        self.model_requests_admitted = next_model_requests_admitted
        self.tool_calls = next_tool_calls
        self.input_tokens = next_input_tokens
        self.output_tokens = next_output_tokens
        self.phases.append(phase_row)

    def check_wall(self) -> float:
        elapsed = self.clock() - self.started_at
        if elapsed < 0 or elapsed > self.runtime["max_wall_clock_seconds"]:
            raise PangeaExecutionError("aggregate frozen wall-clock budget exceeded")
        return elapsed

    def primary_input_bindings(
        self, phase: str, receipt: benchmark.RunReceipt,
    ) -> list[dict[str, str]]:
        bindings = [{"name": "phase_prompt", "sha256": str(
            receipt.policy_receipt.get("phase_prompt_sha256", ""))}]
        if phase == "intake":
            bindings.extend(self.intake_input_bindings)
        return bindings

    def add_primary(self, phase: str, receipt: benchmark.RunReceipt,
                    duration: float | None = None) -> dict[str, Any]:
        self.check_wall()
        value = benchmark.run_receipt_payload(receipt)
        if phase == "intake":
            composer._primary_blocked(value)
        elif receipt.passed is not True or receipt.failures:
            raise PangeaExecutionError(f"primary {phase} failed: {receipt.failures}")
        telemetry = receipt.telemetry
        final_text = telemetry.get("final_text") if isinstance(telemetry, dict) else None
        if not isinstance(final_text, str) or not final_text.strip():
            raise PangeaExecutionError(f"primary {phase} did not produce final text")
        if phase == "intake" and (telemetry.get("model_calls", 0) > 4 or telemetry.get("tool_calls", 0) > 2):
            raise PangeaExecutionError("primary intake exceeded its one-shot budget")
        bindings = self.primary_input_bindings(phase, receipt)
        self._add(
            "primary", phase, telemetry, receipt.duration_seconds if duration is None else duration,
            _canonical_hash(value),
            _canonical_hash(receipt.command), sha256(final_text.encode()).hexdigest(),
            bindings, expected_model_limit=min(self.remaining_model_calls(), 4) if phase == "intake" else 1,
        )
        return value

    def add_leaf(self, role: str, artifacts: Mapping[str, Any],
                 execution: benchmark.TrustedRoleExecution, duration: float,
                 phase: str | None = None) -> None:
        self.check_wall()
        receipt, stdout = execution._trusted_payload()
        if receipt.get("passed") is not True or receipt.get("failures"):
            failures = receipt.get("failures") if isinstance(receipt.get("failures"), list) else []
            detail = f"output_bound={isinstance(receipt.get('output_payload_sha256'), str)};exit={receipt.get('exit_code')}"
            raise PangeaExecutionError(role + " execution failed: " + ",".join(map(str, failures)) + ";" + detail)
        telemetry = benchmark.parse_jsonl_telemetry(stdout.splitlines(True))
        telemetry.update({
            "model_call_limit": receipt.get("model_call_limit"),
            "model_calls_completed": receipt.get("model_calls_completed"),
            "model_requests_admitted": receipt.get("model_requests_admitted"),
            "pre_request_budget_blocked": receipt.get("pre_request_budget_blocked"),
            "pre_request_budget_enforced": receipt.get("pre_request_budget_enforced"),
            "injected_test_runner": receipt.get("injected_test_runner"),
        })
        self._add(
            role, phase or role, telemetry, duration, _canonical_hash(receipt),
            str(receipt.get("command_sha256", "")), str(receipt.get("output_payload_sha256", "")),
            [{"name": name, "sha256": _canonical_hash(value)} for name, value in sorted(artifacts.items())],
            expected_model_limit=1,
        )

    def snapshot(self) -> dict[str, Any]:
        elapsed = self.check_wall()
        return {
            "evidence_class": self.evidence_class,
            "phases": list(self.phases),
            "primary_receipt_sha256s": dict(self.primary_receipt_sha256s),
            "aggregate_telemetry": {
                "model_calls": self.model_calls, "tool_calls": self.tool_calls,
                "model_requests_admitted": self.model_requests_admitted,
                "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "wall_seconds": elapsed,
            },
        }


def _formal_outputs(root: Path, run: Path) -> list[dict[str, str]]:
    root = Path(root).resolve()
    manifest = composer._json(run / "manifest.json")
    deliverables = manifest.get("deliverables")
    paths: list[Path] = []
    if isinstance(deliverables, dict):
        for key in ("report_md", "report_html"):
            relative = deliverables.get(key)
            if isinstance(relative, str):
                paths.append(root / "pangea-data" / relative)
    if len(paths) != 2:
        raise PangeaExecutionError("primary finalize did not publish report.md and report.html")
    rows: list[dict[str, str]] = []
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise PangeaExecutionError("formal output escaped the evaluation root") from exc
        if path.suffix not in {".md", ".html"} or path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_size == 0:
            raise PangeaExecutionError("formal output is not a non-empty regular file")
        rows.append({"path": resolved.relative_to(root).as_posix(), "sha256": sha256(path.read_bytes()).hexdigest()})
    try:
        return composer._verify_formal_output_closure(root, rows)
    except composer.ComposerError as exc:
        raise PangeaExecutionError(str(exc)) from exc


def _execute_pangea(
    spec: benchmark.RunSpec,
    root: Path,
    frozen_policy: Path | None = None,
    *,
    run=subprocess.run,
    environ: Mapping[str, str] | None = None,
    evaluator_root: Path | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    evidence_class: str = "production",
) -> dict[str, Any]:
    """Execute and seal the complete PANGEA as-shipped evaluator topology."""
    if evidence_class not in {"production", "test-only"}:
        raise PangeaExecutionError("invalid evidence class")
    evaluator_started = monotonic()
    root = Path(root).resolve()
    if spec.candidate != "pangea" or spec.track != "as-shipped" or spec.public_bundle.resolve() != root:
        raise PangeaExecutionError("production entry requires the PANGEA as-shipped public bundle root")
    policy = spec.isolated_policy.resolve()
    if frozen_policy is not None and Path(frozen_policy).resolve() != policy:
        raise PangeaExecutionError("RunSpec and frozen policy differ")
    config = benchmark.load_frozen_config()
    expected = benchmark._track(config, "as-shipped", "pangea")
    if benchmark._load_json(policy) != expected:
        raise PangeaExecutionError("frozen as-shipped policy mismatch")
    runs_root = root / "pangea-data/runs"
    existing_runs: list[Path] = []
    public_bundle_binding: benchmark._ValidatedPublicBundleBinding | None = None
    if runs_root.exists():
        if runs_root.is_symlink() or not runs_root.is_dir():
            raise PangeaExecutionError("managed Run root is invalid")
        existing_runs = list(runs_root.iterdir())
        if existing_runs and (len(existing_runs) != 1
                              or not (existing_runs[0] / "internal/composed-receipt.json").is_file()):
            raise PangeaExecutionError("partial Run cannot resume without historical aggregate telemetry")
        if existing_runs and evidence_class == "production":
            try:
                historical = composer._json(existing_runs[0] / "internal/composed-receipt.json")
            except composer.ComposerError as exc:
                raise PangeaExecutionError("completed Run receipt is unreadable") from exc
            if historical.get("evidence_class") == "test-only":
                raise PangeaExecutionError("production entry rejects a test-only completed Run")
    if not existing_runs:
        bundle_errors = benchmark.validate_public_bundle(root)
        if bundle_errors:
            raise PangeaExecutionError("public bundle failed initial validation: " + "; ".join(bundle_errors))
        public_bundle_binding = benchmark._capture_validated_public_bundle_binding(root)

    owned_temporary: tempfile.TemporaryDirectory[str] | None = None
    if evaluator_root is None:
        owned_temporary = tempfile.TemporaryDirectory(prefix="pangea-composition-")
        evaluator = Path(owned_temporary.name)
    else:
        evaluator = Path(evaluator_root).resolve(); evaluator.mkdir(parents=True, exist_ok=True)
    try:
        return _execute_pangea_with_evaluator(
            spec, root, config=config, expected=expected,
            evaluator_started=evaluator_started, monotonic=monotonic,
            evidence_class=evidence_class, existing_runs=existing_runs,
            public_bundle_binding=public_bundle_binding, evaluator=evaluator,
            run=run, environ=environ,
        )
    finally:
        if owned_temporary is not None:
            owned_temporary.cleanup()


def _execute_pangea_with_evaluator(
    spec: benchmark.RunSpec,
    root: Path,
    *,
    config: Mapping[str, Any],
    expected: Mapping[str, Any],
    evaluator_started: float,
    monotonic: Callable[[], float],
    evidence_class: str,
    existing_runs: list[Path],
    public_bundle_binding: benchmark._ValidatedPublicBundleBinding | None,
    evaluator: Path,
    run: Callable[..., Any],
    environ: Mapping[str, str] | None,
) -> dict[str, Any]:
    budget = _AggregateBudget(config["runtime"], expected["max_tool_calls"], evaluator_started,
                              monotonic, evidence_class=evidence_class)
    intake_preparation: dict[str, Any] | None = None
    if not existing_runs:
        if public_bundle_binding is None:
            raise PangeaExecutionError("evaluator intake lacks a validated public bundle binding")
        intake_preparation = _prepare_evaluator_intake(spec, root, public_bundle_binding)
        budget.intake_input_bindings = list(intake_preparation["input_bindings"])
    try:
        immutable_public_bundle = benchmark._immutable_public_bundle_binding(root)
    except benchmark.BenchmarkContractError as exc:
        raise PangeaExecutionError("immutable public bundle binding failed") from exc

    def immutable_public_bundle_closure() -> Mapping[str, Any]:
        try:
            current = benchmark._immutable_public_bundle_binding(root)
        except benchmark.BenchmarkContractError as exc:
            raise PangeaExecutionError("immutable public bundle binding failed") from exc
        if current != immutable_public_bundle:
            raise PangeaExecutionError("immutable public bundle changed during evaluation")
        return immutable_public_bundle

    def primary(phase: str, prompt: str,
                managed_run: Path | None = None) -> tuple[benchmark.RunReceipt, dict[str, Any]]:
        budget.check_wall(); phase_started = monotonic()
        phase_model_limit = min(budget.remaining_model_calls(), 4) if phase == "intake" else 1
        receipt = benchmark.execute_pangea_primary_phase(
            spec, phase, prompt, evaluator, run=run, environ=environ,
            model_call_limit=phase_model_limit,
            public_bundle_binding=public_bundle_binding, evidence_class=evidence_class,
        )
        input_bindings = budget.primary_input_bindings(phase, receipt)
        intake_hash = _persist_primary_receipt(
            evaluator, phase, receipt, evidence_class, input_bindings,
        )
        if managed_run is None:
            if phase != "intake":
                raise PangeaExecutionError("managed Run is required for primary receipt persistence")
            try:
                managed_run = composer._single_active_run(root, None)
            except composer.ComposerError as exc:
                raise PangeaExecutionError("primary intake did not establish one managed Run") from exc
            if intake_preparation is None or managed_run.name != intake_preparation["expected_run_id"]:
                raise PangeaExecutionError("primary intake established a Run outside the evaluator spec")
        managed_hash = _persist_managed_primary_receipt(
            managed_run, phase, receipt, evidence_class, input_bindings,
        )
        if managed_hash != intake_hash:
            raise PangeaExecutionError("primary receipt durable copies differ")
        budget.bind_primary_receipt(phase, managed_hash)
        return receipt, budget.add_primary(phase, receipt, monotonic() - phase_started)

    def intake() -> Mapping[str, Any]:
        receipt, value = primary(
            "intake",
            "Execute exactly this one command:\n"
            f"{runctl.EVALUATOR_INTAKE_COMMAND}\n"
            "After it succeeds, stop immediately. Do not call any other tool or command.",
        )
        value["executed_roles"] = ["primary"]
        return value

    def execute_role(role: str, artifacts: Mapping[str, Any], *, phase: str | None = None) -> benchmark.TrustedRoleExecution:
        budget.check_wall(); started = monotonic()
        if public_bundle_binding is not None:
            benchmark._validate_bound_public_bundle(root, public_bundle_binding)
        execution = benchmark.execute_isolated_role(
            role, artifacts, run=run, environ=environ, scratch_parent=evaluator,
            model_call_limit=1,
            evidence_class=evidence_class,
        )
        budget.add_leaf(role, artifacts, execution, monotonic() - started, phase)
        return execution

    def fixed_judge(run_dir: Path) -> Mapping[str, Any]:
        _, resume = primary(
            "resume",
            f"Resume Run {run_dir.name}. Consume the evaluator-applied fragments and semantic assessments, "
            "stage the fixed analysis model and report model, then stop. Task delegation is unavailable.",
            run_dir,
        )
        if resume["telemetry"]["model_calls"] < 1:
            raise PangeaExecutionError("primary resume made no model call")
        contract = composer._json(run_dir / "internal/task-contract.json")
        budget.check_wall()
        judged = runctl._run_coverage_judge(run_dir, contract)
        budget.check_wall()
        return judged

    def finalize(run_dir: Path, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        audit_artifacts = {
            "TASK_CONTRACT.json": composer._json(run_dir / "internal/task-contract.json"),
            "ANALYSIS_MODEL.json": composer._json(run_dir / "internal/analysis-model.json"),
            "COVERAGE_JUDGE.json": composer._json(run_dir / "internal/coverage-judge.json"),
            "RISK_LEDGER.json": composer._json(run_dir / "internal/risk-ledger.json"),
            "REPORT_MODEL.json": composer._json(run_dir / "internal/report-model.json"),
        }
        audit_execution = execute_role("auditor", audit_artifacts, phase="report-auditor")
        opinion_path = benchmark.write_native_report_audit(run_dir, audit_artifacts, audit_execution)
        opinion = composer._json(opinion_path)
        if opinion.get("verdict") != "PASS":
            raise PangeaExecutionError("final report auditor did not pass")
        try:
            budget.check_wall()
            runctl.apply_audit_v2(argparse.Namespace(root=str(root), run_id=run_dir.name,
                                                        file=str(opinion_path)))
            budget.check_wall()
        except runctl.RunCtlError as exc:
            raise PangeaExecutionError("final report audit application failed") from exc
        receipt, _ = primary(
            "finalize",
            f"Finalize Run {run_dir.name} using only its fixed PASS Coverage Judge and formal report. "
            "Do not alter analysis, report, Judge, fragments, or assessments. Task delegation is unavailable.",
            run_dir,
        )
        fixed = {
            "analysis": sha256((run_dir / "internal/analysis-model.json").read_bytes()).hexdigest(),
            "report": sha256((run_dir / "internal/report-model.json").read_bytes()).hexdigest(),
            "coverage_judge": sha256((run_dir / "internal/coverage-judge.json").read_bytes()).hexdigest(),
        }
        final_text = receipt.telemetry["final_text"]
        return {
            "analysis_bound": True, "report_bound": True, "judge_bound": True,
            "final_text": final_text, "bindings": fixed,
            "formal_outputs": _formal_outputs(root, run_dir),
        }

    callbacks = composer.ComposerCallbacks(
        primary_intake=intake,
        primary_finalize=finalize,
        execute_role=lambda role, artifacts: execute_role(role, artifacts),
        coverage_judge=fixed_judge,
        execution_closure=budget.snapshot,
        public_bundle_closure=immutable_public_bundle_closure,
    )
    try:
        return composer.compose_complete_run(root, callbacks)
    except PangeaExecutionError:
        raise
    except composer.ComposerError as exc:
        raise PangeaExecutionError(str(exc)) from exc


def execute_pangea_as_shipped(
    spec: benchmark.RunSpec,
    root: Path,
    frozen_policy: Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    evaluator_root: Path | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Execute production PANGEA using only the evaluator-owned runner.

    A runner callback is deliberately not part of this public API.  Tests use
    the private harness below, while production tests can patch
    ``subprocess.run`` without changing the evidence class.
    """
    return _execute_pangea(spec, root, frozen_policy, run=subprocess.run,
                           environ=environ, evaluator_root=evaluator_root,
                           monotonic=monotonic, evidence_class="production")


def _execute_pangea_test_harness(
    spec: benchmark.RunSpec,
    root: Path,
    frozen_policy: Path | None = None,
    *,
    run: Callable[..., Any],
    environ: Mapping[str, str] | None = None,
    evaluator_root: Path | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Private test-only entry for deterministic subprocess-shaped runners."""
    return _execute_pangea(spec, root, frozen_policy, run=run, environ=environ,
                           evaluator_root=evaluator_root, monotonic=monotonic,
                           evidence_class="test-only")


# Concise public spelling.
execute = execute_pangea_as_shipped
