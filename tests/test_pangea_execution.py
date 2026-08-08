"""Provider-free checks for the public PANGEA production evaluator entry."""
from __future__ import annotations

import json
import argparse
import copy
import os
import shutil
import stat
import subprocess
import threading
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest
import warnings
from unittest.mock import Mock, patch

from evaluation import benchmark, composer, execute_pangea_as_shipped
from evaluation import pangea_execution
from runtime import data_runtime, fragment_runtime, runctl
from tests.test_analysis_depth_contract import AnalysisDepthContractTests
from tests.test_analysis_pipeline import AnalysisPipelineTests
from tests.test_analysis_report_projection import AnalysisReportProjectionTests
from tests.test_contract_lifecycle import ContractLifecycleTests
from tests.test_evaluation_benchmark import (_debug_config, _intake_convergence_stream,
                                             _native_stream, bind_canonical_case_fixture)
from tests.test_evaluation_corpus import make_repo, stage_fixture, _fixture_public_manifest
from benchmarks import stage as public_stage


_execute_pangea_test_harness = pangea_execution._execute_pangea_test_harness


def _reset_managed_workspace_to_staging_initial(root: Path) -> None:
    workspace = root / "pangea-data"
    if workspace.exists():
        for path in sorted(
            (candidate for candidate in workspace.rglob("*")
             if candidate.is_dir() and not candidate.is_symlink()),
            key=lambda candidate: len(candidate.parts), reverse=True,
        ):
            path.chmod((path.stat().st_mode & 0o7777) | 0o700)
        workspace.chmod((workspace.stat().st_mode & 0o7777) | 0o700)
        shutil.rmtree(workspace)
    (workspace / ".evaluator-scratch").mkdir(parents=True)


def _seal_authoritative_public_manifest(root: Path) -> None:
    manifest = root / "public-bundle-manifest.json"
    manifest.chmod(0o400)
    before = manifest.lstat()
    first = manifest.read_bytes()
    second = manifest.read_bytes()
    after = manifest.lstat()
    if (stat.S_IMODE(before.st_mode) != 0o400
            or not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode)
            or before.st_uid != os.geteuid() or before.st_nlink != 1
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or first != second):
        raise AssertionError("authoritative public manifest is not stable and read-only")


def _preinitialized_evaluator_fixture(base: Path) -> tuple[Path, benchmark.RunSpec]:
    root = (base / "bundle").resolve(); root.mkdir()
    ContractLifecycleTests().prepare(root)
    _reset_managed_workspace_to_staging_initial(root)
    task, case_id, case_hash = bind_canonical_case_fixture(
        root, framing="complete local module analysis", staged_repositories=True,
    )
    _seal_authoritative_public_manifest(root)
    workspace = data_runtime.ensure_layout(root)
    for directory in (workspace / "contracts", workspace / "runs"):
        directory.mkdir(mode=0o700, exist_ok=True); directory.chmod(0o700)
    data_runtime.atomic_write_json(workspace / "session/preflight-receipt.json", {
        "artifact_type": "preflight_receipt", "schema_version": "1.0",
        "created_at": data_runtime.utc_now(), "status": "ready",
        "project_root": str(root), "data_root": str(workspace),
        "repository_root": str(workspace / "repositories"), "known_repositories": [],
        "allowed_next_actions": ["draft_contract"], "python_executable": os.sys.executable,
        "step_results": {}, "step_errors": {},
    })
    public_manifest = root / "public-bundle-manifest.json"
    original_mode = public_manifest.stat().st_mode & 0o7777
    public_manifest.chmod(original_mode | 0o200)
    try:
        benchmark.write_public_bundle_manifest(root)
    finally:
        public_manifest.chmod(original_mode)
    _seal_authoritative_public_manifest(root)
    policy = base / "policy.json"
    policy.write_text(json.dumps(benchmark._track(
        benchmark.load_frozen_config(), "as-shipped", "pangea",
    )))
    return root, benchmark.RunSpec(
        "pangea", "as-shipped", root, task, policy, case_id, "CASE.json", case_hash,
    )


def _observable_temporary_directory(original, observed: list[object]):
    class ObservableTemporaryDirectory:
        def __init__(self, *args, **kwargs):
            self._inner = original(*args, **kwargs)
            self.name = self._inner.name
            self.cleanup_calls = 0
            if kwargs.get("prefix") == "pangea-composition-":
                observed.append(self)

        def cleanup(self):
            self.cleanup_calls += 1
            self._inner.cleanup()

        def __enter__(self):
            return self.name

        def __exit__(self, exc_type, exc, traceback):
            self.cleanup()
            return False

    return ObservableTemporaryDirectory


def _telemetry(session: str, *, inputs: int = 100, outputs: int = 20,
               limit: int = 40, admitted: int = 1, calls: int = 1,
               blocked: bool = False) -> dict:
    return {
        "session_ids": [session], "model_calls": calls, "tool_calls": 0,
        "model_calls_completed": calls, "model_call_limit": limit,
        "model_requests_admitted": admitted,
        "pre_request_budget_blocked": blocked,
        "pre_request_budget_enforced": False,
        "injected_test_runner": True,
        "input_tokens": inputs, "output_tokens": outputs,
        "max_step_input_tokens": inputs, "max_step_output_tokens": outputs,
        "truncated": False, "final_text": "phase complete",
    }


def _receipt(session: str, phase: str, *, inputs: int = 100, outputs: int = 20,
             limit: int | None = None, admitted: int = 1) -> benchmark.RunReceipt:
    intake = phase == "intake"
    if limit is None:
        limit = 4 if intake else 40
    telemetry=_telemetry(session, inputs=inputs, outputs=outputs, limit=limit, admitted=admitted)
    telemetry["intake_attempt_summary"] = ({"schema_version":"1.0","attempts":1,
        "denied_before_success":0,"completed_exact":1,"status_sequence":["completed_exact"]}
        if intake else None)
    if intake: telemetry["tool_calls"] = 1
    return benchmark.RunReceipt(
        candidate="pangea", track="as-shipped", case_id="case",
        command=["opencode", "run", phase], exit_code=0, duration_seconds=1.0,
        stdout_sha256="1" * 64, stderr_sha256="2" * 64,
        telemetry=telemetry,
        environment_keys=[], preflight={},
        policy_receipt={"phase_prompt_sha256": "3" * 64},
        passed=not intake,
        failures=["external_role_execution_required"] if intake else [],
    )


class PangeaExecutionTests(unittest.TestCase):
    def test_real_stage_initializes_evaluator_intake_before_provider_runner(self) -> None:
        class ReachedRunner(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); spdk = base / "spdk"; nvme = base / "nvme"
            make_repo(spdk); make_repo(nvme)
            for relative in (
                "include/spdk_internal/nvme_tcp.h",
                "lib/nvme/nvme_tcp.c",
                "test/unit/lib/nvme/nvme_tcp.c/nvme_tcp_ut.c",
            ):
                path = spdk / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("int local_scope_placeholder(void) { return 0; }\n")
            subprocess.run(["git", "-C", str(spdk), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(spdk), "commit", "-m", "add local case scope"],
                check=True, capture_output=True,
            )
            staged = base / "bundle"
            stage_fixture(
                staged, Path(__file__).resolve().parents[1], spdk, nvme,
                case_id="spdk-recv-state-diagnostics",
            )
            case = json.loads((staged / "CASE.json").read_text())
            policy = base / "policy.json"
            policy.write_text(json.dumps(benchmark._track(
                benchmark.load_frozen_config(), "as-shipped", "pangea",
            )))
            spec = benchmark.RunSpec(
                "pangea", "as-shipped", staged, staged / "TASK.md", policy,
                case["id"], "CASE.json", sha256((staged / "CASE.json").read_bytes()).hexdigest(),
            )
            rejected = base / "rejected"
            shutil.copytree(staged, rejected)
            local_manifest = _fixture_public_manifest(spdk, nvme)
            provider_runs = 0

            def runner(command, **kwargs):
                nonlocal provider_runs
                if command[:2] == ["opencode", "--version"]:
                    return Mock(returncode=0, stdout="1.18.4\n", stderr="")
                if command[:3] == ["opencode", "debug", "config"]:
                    configured = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
                    return Mock(returncode=0, stdout=json.dumps({"plugin": configured["plugin"]}), stderr="")
                if command[:3] == ["opencode", "debug", "agent"]:
                    agent = command[3]
                    if "OPENCODE_CONFIG_CONTENT" not in kwargs["env"]:
                        enabled = (benchmark.AS_SHIPPED_SAFE_TOOLS if agent == "pangea-test"
                                   else benchmark.AS_SHIPPED_ROLE_TOOLS[agent])
                        return Mock(returncode=0, stdout=_debug_config(
                            *enabled, name=agent,
                            mode="primary" if agent == "pangea-test" else "subagent",
                        ), stderr="")
                    if agent == "pangea-test":
                        debug = _debug_config(
                            "bash", safe_overlay=True, primary_task_enabled=False,
                            primary_phase="intake",
                        )
                    elif agent in {"analysis-leaf","audit-leaf"}:
                        debug = _debug_config(name=agent,mode="primary",tool_free=True)
                    else:
                        debug = _debug_config(
                            *benchmark.AS_SHIPPED_ROLE_TOOLS[agent], name=agent,
                            mode="subagent", safe_overlay=True,
                        )
                    return Mock(returncode=0, stdout=debug, stderr="")
                if command[:2] == ["opencode", "run"]:
                    provider_runs += 1
                    agent = command[command.index("--agent") + 1]
                    prompt = command[-1]
                    if agent == "pangea-test" and benchmark.EVALUATOR_INTAKE_COMMAND in prompt:
                        with patch.object(Path, "cwd", return_value=staged):
                            runctl.evaluator_intake_v2(argparse.Namespace())
                        stream = _native_stream(
                            text="primary phase complete", tool="bash",
                            tool_input={"command": benchmark.EVALUATOR_INTAKE_COMMAND},
                        ).replace('"ses_test"', '"real-stage-intake"')
                        return Mock(returncode=0, stdout=stream, stderr="")
                    self.assertEqual({"preflight-receipt.json", "evaluator-intake-spec.json"},
                                     {path.name for path in (staged / "pangea-data/session").iterdir()})
                    self.assertEqual(1, len(list((staged / "pangea-data/contracts").iterdir())))
                    self.assertEqual(1, len(list((staged / "pangea-data/runs").iterdir())))
                    raise ReachedRunner
                raise AssertionError(command)

            with patch.object(public_stage, "load_manifest", return_value=local_manifest), \
                    patch.object(public_stage, "_validate_manifest_snapshot", return_value=[]), \
                    self.assertRaisesRegex(
                        pangea_execution.PangeaExecutionError,
                        "analysis-worker execution failed",
                    ):
                    _execute_pangea_test_harness(
                        spec, staged, run=runner,
                        environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "test"},
                        evaluator_root=base / "evaluator",
                    )
            self.assertGreaterEqual(provider_runs, 2)

            variants = {
                "unknown": lambda managed: (managed / "candidate-prebuilt").mkdir(),
                "session": lambda managed: (
                    (managed / "session").mkdir(),
                    (managed / "session/evaluator-intake-spec.json").write_text("{}\n"),
                ),
                "contracts": lambda managed: (
                    (managed / "contracts/prebuilt").mkdir(parents=True),
                    (managed / "contracts/prebuilt/contract.json").write_text("{}\n"),
                ),
                "runs": lambda managed: (
                    (managed / "runs/prebuilt").mkdir(parents=True),
                    (managed / "runs/prebuilt/manifest.json").write_text("{}\n"),
                ),
                "root-activation": lambda managed: (
                    (managed / "contracts/prebuilt").mkdir(parents=True),
                    (managed / "contracts/prebuilt/contract.json").write_text(
                        '{"status":"activated","activation":{"run_id":"prebuilt"}}\n'
                    ),
                ),
            }
            for name, mutate in variants.items():
                with self.subTest(prebuilt=name):
                    variant_root = base / f"rejected-{name}"
                    shutil.copytree(rejected, variant_root)
                    variant_root.chmod(0o755)
                    managed = variant_root / "pangea-data"
                    managed.chmod(0o755)
                    mutate(managed)
                    public_manifest = variant_root / "public-bundle-manifest.json"
                    original_manifest_mode = public_manifest.stat().st_mode & 0o7777
                    public_manifest.chmod(original_manifest_mode | 0o200)
                    try:
                        benchmark.write_public_bundle_manifest(variant_root)
                    finally:
                        public_manifest.chmod(original_manifest_mode)
                    self.assertEqual(public_manifest.stat().st_mode & 0o222, 0)
                    variant_case = json.loads((variant_root / "CASE.json").read_text())
                    variant_spec = replace(
                        spec, public_bundle=variant_root, task=variant_root / "TASK.md",
                        case_id=variant_case["id"],
                    )
                    binding = benchmark._capture_validated_public_bundle_binding(variant_root)
                    with patch.object(public_stage, "load_manifest", return_value=local_manifest), \
                            patch.object(public_stage, "_validate_manifest_snapshot", return_value=[]), \
                            self.assertRaisesRegex(
                                pangea_execution.PangeaExecutionError, "zero candidate-prepared",
                            ):
                        pangea_execution._prepare_evaluator_intake(
                            variant_spec, variant_root, binding,
                        )

    def test_exact_prebuilt_managed_layout_is_rejected_before_evaluator_intake_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = (base / "bundle").resolve(); root.mkdir()
            helper = ContractLifecycleTests(); helper.prepare(root)
            _reset_managed_workspace_to_staging_initial(root)
            task, case_id, case_hash = bind_canonical_case_fixture(
                root, framing="complete local module analysis", staged_repositories=True,
            )
            _seal_authoritative_public_manifest(root)
            policy = base / "policy.json"
            policy.write_text(json.dumps(benchmark._track(
                benchmark.load_frozen_config(), "as-shipped", "pangea",
            )))
            spec = benchmark.RunSpec(
                "pangea", "as-shipped", root, task, policy, case_id, "CASE.json", case_hash,
            )
            staged_binding = benchmark._capture_validated_public_bundle_binding(root)
            workspace = root / "pangea-data"
            scratch = workspace / ".evaluator-scratch"
            manifest = json.loads((root / "public-bundle-manifest.json").read_text())
            self.assertEqual({".evaluator-scratch"}, set(os.listdir(workspace)))
            self.assertEqual([], list(scratch.iterdir()))
            self.assertEqual(root, staged_binding.root)
            self.assertEqual("pangea-data", staged_binding.managed_root)
            self.assertEqual(
                {"pangea-data", "pangea-data/.evaluator-scratch"},
                {entry for entry in staged_binding.snapshot["entries"]
                 if entry.split("/", 1)[0] == "pangea-data"},
            )
            self.assertEqual(
                set(),
                {entry for entry in staged_binding.snapshot["files"]
                 if entry.split("/", 1)[0] == "pangea-data"},
            )
            self.assertEqual(
                {"pangea-data", "pangea-data/.evaluator-scratch"},
                {entry for entry in manifest["directories"]
                 if entry.split("/", 1)[0] == "pangea-data"},
            )
            self.assertEqual(
                set(),
                {entry for entry in manifest["files"]
                 if entry.split("/", 1)[0] == "pangea-data"},
            )
            for directory in (workspace, scratch):
                self.assertTrue(directory.is_dir())
                self.assertFalse(directory.is_symlink())
                self.assertEqual(os.geteuid(), directory.stat().st_uid)
                self.assertEqual(0, directory.stat().st_mode & 0o022)

            workspace = data_runtime.ensure_layout(root)
            session = workspace / "session"
            contracts = workspace / "contracts"
            runs = workspace / "runs"
            for directory in (contracts, runs):
                directory.mkdir(mode=0o700, exist_ok=True)
                directory.chmod(0o700)
                self.assertTrue(directory.is_dir())
                self.assertFalse(directory.is_symlink())
                self.assertEqual(os.geteuid(), directory.stat().st_uid)
                self.assertEqual(0o700, directory.stat().st_mode & 0o777)
            preflight = workspace / "session/preflight-receipt.json"
            data_runtime.atomic_write_json(preflight, {
                "artifact_type": "preflight_receipt", "schema_version": "1.0",
                "created_at": data_runtime.utc_now(), "status": "ready",
                "project_root": str(root), "data_root": str(workspace),
                "repository_root": str(workspace / "repositories"), "known_repositories": [],
                "allowed_next_actions": ["draft_contract"], "python_executable": os.sys.executable,
                "step_results": {}, "step_errors": {},
            })
            preflight_before = preflight.read_bytes()
            preflight_hash_before = sha256(preflight_before).hexdigest()
            public_manifest = root / "public-bundle-manifest.json"
            original_manifest_mode = public_manifest.stat().st_mode & 0o7777
            public_manifest.chmod(original_manifest_mode | 0o200)
            try:
                benchmark.write_public_bundle_manifest(root)
            finally:
                public_manifest.chmod(original_manifest_mode)
            binding = benchmark._capture_validated_public_bundle_binding(root)
            contracts_before = list(contracts.iterdir())
            runs_before = list(runs.iterdir())
            session_before = list(session.iterdir())

            with patch.object(benchmark, "execute_pangea_primary_phase") as runner, \
                    self.assertRaisesRegex(
                        pangea_execution.PangeaExecutionError, "zero candidate-prepared",
                    ):
                pangea_execution._prepare_evaluator_intake(spec, root, binding)
            runner.assert_not_called()

            self.assertEqual(preflight.read_bytes(), preflight_before)
            self.assertEqual(sha256(preflight.read_bytes()).hexdigest(), preflight_hash_before)
            self.assertEqual(session_before, list(session.iterdir()))
            self.assertEqual(contracts_before, list(contracts.iterdir()))
            self.assertEqual(runs_before, list(runs.iterdir()))
            self.assertFalse((workspace / runctl.EVALUATOR_INTAKE_SPEC_RELATIVE).exists())

    def test_owned_evaluator_temporary_is_cleaned_on_strict_prepare_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root, spec = _preinitialized_evaluator_fixture(Path(temp))
            observed: list[object] = []
            original = tempfile.TemporaryDirectory
            temporary = _observable_temporary_directory(original, observed)
            runner = Mock()
            with warnings.catch_warnings(record=True) as caught, \
                    patch.object(pangea_execution.tempfile, "TemporaryDirectory", temporary), \
                    self.assertRaisesRegex(
                        pangea_execution.PangeaExecutionError, "zero candidate-prepared",
                    ):
                warnings.simplefilter("always", ResourceWarning)
                _execute_pangea_test_harness(spec, root, run=runner)
            runner.assert_not_called()
            self.assertEqual(1, len(observed))
            owned = observed[0]
            self.assertEqual(1, owned.cleanup_calls)
            self.assertFalse(Path(owned.name).exists())
            self.assertEqual([], [warning for warning in caught
                                  if issubclass(warning.category, ResourceWarning)])

    def test_owned_evaluator_temporary_is_cleaned_after_full_mock(self) -> None:
        observed: list[object] = []
        original = tempfile.TemporaryDirectory
        temporary = _observable_temporary_directory(original, observed)
        self._default_evaluator_root = True
        try:
            with patch.object(pangea_execution.tempfile, "TemporaryDirectory", temporary):
                self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._default_evaluator_root
        self.assertEqual(1, len(observed))
        owned = observed[0]
        self.assertEqual(1, owned.cleanup_calls)
        self.assertFalse(Path(owned.name).exists())

    def test_explicit_evaluator_root_is_preserved_on_strict_prepare_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, spec = _preinitialized_evaluator_fixture(base)
            evaluator = base / "explicit-evaluator"; evaluator.mkdir()
            marker = evaluator / "owned-by-caller.txt"; marker.write_text("preserve\n")
            runner = Mock()
            with self.assertRaisesRegex(
                pangea_execution.PangeaExecutionError, "zero candidate-prepared",
            ):
                _execute_pangea_test_harness(
                    spec, root, run=runner, evaluator_root=evaluator,
                )
            runner.assert_not_called()
            self.assertTrue(evaluator.is_dir())
            self.assertEqual("preserve\n", marker.read_text())

    def test_public_entry_completes_real_managed_run_with_mock_subprocess(self) -> None:
        extra_on_first_finalize = bool(getattr(self, "_extra_on_first_finalize", False))
        timeout_during_seal = bool(getattr(self, "_timeout_during_seal", False))
        extra_plugin_on_preflight = bool(getattr(self, "_extra_plugin_on_preflight", False))
        mutate_candidate_after_intake = bool(getattr(self, "_mutate_candidate_after_intake", False))
        mutate_case_after_intake = bool(getattr(self, "_mutate_case_after_intake", False))
        mutate_stage_receipt_after_intake = bool(getattr(self, "_mutate_stage_receipt_after_intake", False))
        add_project_plugin_after_intake = bool(getattr(self, "_add_project_plugin_after_intake", False))
        writable_confirmed_on_intake = bool(getattr(self, "_writable_confirmed_on_intake", False))
        intake_write_failure_index = getattr(self, "_intake_write_failure_index", None)
        immutable_drift_before_primary = getattr(self, "_immutable_drift_before_primary", None)
        default_evaluator_root = bool(getattr(self, "_default_evaluator_root", False))
        intake_permission_prefix = bool(getattr(self, "_intake_permission_prefix", False))
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); root = base / "bundle"; root.mkdir()
            helper = ContractLifecycleTests(); helper.prepare(root)
            _reset_managed_workspace_to_staging_initial(root)
            task, case_id, case_hash = bind_canonical_case_fixture(
                root, framing="complete local module analysis", staged_repositories=True,
            )
            _seal_authoritative_public_manifest(root)
            run_id = "case-" + case_hash
            policy = base / "policy.json"
            policy.write_text(json.dumps(benchmark._track(benchmark.load_frozen_config(), "as-shipped", "pangea")))
            spec = benchmark.RunSpec(
                "pangea", "as-shipped", root, task, policy, case_id, "CASE.json", case_hash,
            )
            pipeline_helper = AnalysisPipelineTests(); session_counter = 0; run_agents: list[str] = []
            runner_state_lock = threading.Lock()
            intake_exact_executions = 0
            immutable_drifted = False

            def stage_models(run_dir: Path) -> None:
                fragments=[json.loads(path.read_text())["payload"] for path in sorted((run_dir/"internal/fragments").glob("*.json"))]
                risk=next((risk for fragment in fragments for risk in fragment["risk_cards"]),None)
                fact_by_key={(fact["obligation_id"],fact["inventory_id"],fact["line_start"],fact["line_count"]):fact
                             for fragment in fragments for fact in fragment["facts"]}
                risk_fact=fact_by_key[tuple(risk["fact_keys"][0])] if risk is not None else None
                AnalysisDepthContractTests.complete_checkpoints(root, run_id)
                model = AnalysisDepthContractTests.model(run_dir)
                model["r2_projection"] = runctl._expected_r2_projection(run_dir)
                if risk is not None:
                    hc_id = risk["risk_id"]
                    model["test_scenarios"][0]["risk_ids"].append(hc_id)
                    model["test_cases"][0]["risk_ids"].append(hc_id)
                model_path = run_dir / "tmp/model.json"; model_path.parent.mkdir(exist_ok=True)
                model_path.write_text(json.dumps(model, ensure_ascii=False))
                helper.cli(root, "stage-analysis-v2", "--run-id", run_id, "--file", str(model_path))
                low = AnalysisReportProjectionTests.risk(); low["severity"] = "Low"
                data_runtime.upsert_risk(root, run_id, low)
                staged_risks=[low]
                if risk is not None and risk_fact is not None:
                    high = copy.deepcopy(low)
                    high.update({
                        "risk_id": risk["risk_id"], "title": risk["summary"], "severity": risk["severity"],
                        "trigger": risk["trigger"], "propagation": risk["propagation"],
                        "external_impact": risk["impact"], "observation": risk["observation"],
                        "recovery": risk["recovery"],
                        "test_explanation": f"Control: {risk['control']}\nOracle: {risk['oracle']}",
                        "evidence": [{"location": runctl._r2_fact_location(risk_fact),
                                      "observation": risk_fact["evidence"]}],
                    })
                    data_runtime.upsert_risk(root, run_id, high);staged_risks.append(high)
                contract = json.loads((run_dir / "internal/task-contract.json").read_text())
                draft = {"title": "R2 report", "task_contract": contract, "code_map": [{}], "flows": [{}],
                         "branches": [{}], "risks": staged_risks, "scenarios": [], "test_cases": [],
                         "unresolved": [], "next_steps": []}
                draft_path = run_dir / "tmp/report.json"; draft_path.write_text(json.dumps(draft, ensure_ascii=False))
                helper.cli(root, "stage-report-v2", "--run-id", run_id, "--file", str(draft_path))

            def runner(command, **kwargs):
                nonlocal session_counter, immutable_drifted, intake_exact_executions
                if command[:2] == ["opencode", "--version"]:
                    with runner_state_lock:
                        if immutable_drift_before_primary and not immutable_drifted:
                            if immutable_drift_before_primary == "managed-repository":
                                changed = root / "pangea-data/repositories/pre-primary-drift.txt"
                            elif immutable_drift_before_primary == "root-entry":
                                changed = root / "pre-primary-drift.txt"
                            else:
                                raise AssertionError(immutable_drift_before_primary)
                            changed.write_text("changed after evaluator intake preparation\n")
                            immutable_drifted = True
                    return Mock(returncode=0, stdout="1.18.4\n", stderr="")
                if command[:3] == ["opencode", "debug", "config"]:
                    resolved = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
                    plugins = list(resolved["plugin"])
                    if extra_plugin_on_preflight:
                        plugins.append("candidate-extra-plugin")
                    return Mock(returncode=0, stdout=json.dumps({"plugin": plugins}), stderr="")
                if command[:3] == ["opencode", "debug", "agent"]:
                    agent = command[3]; overlay = "OPENCODE_CONFIG_CONTENT" in kwargs["env"]
                    if not overlay:
                        enabled = benchmark.AS_SHIPPED_SAFE_TOOLS if agent == "pangea-test" else benchmark.AS_SHIPPED_ROLE_TOOLS[agent]
                        return Mock(returncode=0, stdout=_debug_config(*enabled, name=agent,
                            mode="primary" if agent == "pangea-test" else "subagent"), stderr="")
                    if agent == "pangea-test":
                        configured = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])["agent"][agent]
                        enabled = {name for name, value in configured["tools"].items() if value}
                        intake_phase = "intake" if enabled == {"bash"} else None
                        debug = _debug_config(*enabled, safe_overlay=True, primary_task_enabled=False,
                                              primary_phase=intake_phase)
                    elif agent in {"analysis-leaf","audit-leaf"}:
                        debug = _debug_config(name=agent,mode="primary",tool_free=True)
                    else:
                        debug = _debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS[agent], name=agent,
                                              mode="subagent", safe_overlay=True)
                    return Mock(returncode=0, stdout=debug, stderr="")
                if command[:2] != ["opencode", "run"]:
                    raise AssertionError(command)
                agent = command[command.index("--agent") + 1]; cwd = Path(kwargs["cwd"])
                with runner_state_lock:
                    if immutable_drift_before_primary == "root-entry":
                        self._root_drift_model_runner_calls += 1
                    session_counter += 1; session = f"session-{session_counter}"
                    run_agents.append(agent)
                if agent == "analysis-leaf":
                    run_dir = root / "pangea-data/runs" / run_id
                    compact=json.loads((cwd/"COMPACT_CONTEXT.json").read_text())
                    assignments=json.loads((run_dir/"internal/assignment-index.json").read_text())["payload"]["assignments"]
                    index=next(index for index,row in enumerate(assignments) if row["fragment_id"]==compact["f"])
                    candidate=json.loads((run_dir/f"internal/context-packs/{compact['f']}/CONTEXT.json").read_text())["payload"]["candidate"]
                    self.assertEqual(compact,candidate["compact_context"])
                    text=json.dumps(pipeline_helper.compact_native(candidate,index),separators=(",",":"))
                elif agent == "audit-leaf":
                    batch=json.loads((cwd/"SEMANTIC_BATCH.json").read_text())
                    text=json.dumps({"v":1,"a":[[row["ordinal"],True,"exact fact supports claim"] for row in batch["claims"]]},separators=(",",":"))
                elif agent == "auditor" and (cwd / "CLAIM.json").is_file():
                    text = json.dumps({"supported": True, "reason": "auditor confirmed exact excerpt support"})
                elif agent == "auditor":
                    report_path = root / "pangea-data/runs" / run_id / "internal/report-model.json"
                    passed = {"verdict": "PASS", "violations": [], "gaps": []}
                    text = json.dumps({
                        "artifact_type": "audit_opinion", "schema_version": "2.0",
                        "audited_artifact": "internal/report-model.json",
                        "audited_sha256": benchmark.sha256(report_path.read_bytes()).hexdigest(),
                        "verdict": "PASS", "checks": {name: dict(passed) for name in
                            ("traceability", "blackbox_executability", "coverage", "format_compliance")},
                        "required_actions": [],
                    })
                else:
                    prompt = command[-1]
                    tool = None; tool_input = None
                    if benchmark.EVALUATOR_INTAKE_COMMAND in prompt:
                        if writable_confirmed_on_intake:
                            confirmed_path = next((root / "pangea-data/contracts").glob("*/contract.json"))
                            confirmed_path.chmod(0o600)
                        try:
                            with patch.object(Path, "cwd", return_value=root):
                                runctl.evaluator_intake_v2(argparse.Namespace())
                            intake_exact_executions += 1
                        except runctl.RunCtlError:
                            error_stream = json.dumps({
                                "timestamp": 1, "sessionID": session, "type": "error",
                                "error": {"code": "evaluator_intake_failed"},
                            }) + "\n"
                            return Mock(
                                returncode=1, stdout=error_stream,
                                stderr="evaluator-intake-v2 failed\n",
                            )
                        tool = "bash"
                        tool_input = {"command": benchmark.EVALUATOR_INTAKE_COMMAND}
                    elif "stage the fixed analysis model" in prompt:
                        stage_models(root / "pangea-data/runs" / run_id)
                    elif "Finalize Run" in prompt:
                        runctl.finalize_v2(argparse.Namespace(
                            root=str(root), run_id=run_id,
                            model=str(root / "pangea-data/runs" / run_id / "internal/report-model.json"),
                        ))
                        if extra_on_first_finalize:
                            (root / "pangea-data/reports" / run_id / "extra.txt").write_text("extra\n")
                    text = "primary phase complete"
                stream = _native_stream(text=text, tool=locals().get("tool"),
                                        tool_input=locals().get("tool_input")).replace('"ses_test"', json.dumps(session))
                if intake_permission_prefix and locals().get("tool") == "bash":
                    stream = _intake_convergence_stream(root, [
                        ("bash", "error", {"command": "ls", "workdir": str(root)}, "permission"),
                        ("bash", "error", {"command": "ls && pwd", "workdir": str(root)}, "permission"),
                        ("bash", "completed", {"command": benchmark.EVALUATOR_INTAKE_COMMAND,
                                                "workdir": str(root)}, None),
                    ]).replace('"ses_test"', json.dumps(session))
                return Mock(returncode=0, stdout=stream, stderr="")

            execute_args = {"run": runner,
                "environ": {"PATH": "/bin", "DEEPSEEK_API_KEY": "test-provider-value"}}
            if not default_evaluator_root:
                execute_args["evaluator_root"] = Path(temp) / "evaluator"
            if writable_confirmed_on_intake:
                with self.assertRaises(pangea_execution.PangeaExecutionError):
                    _execute_pangea_test_harness(spec, root, **execute_args)
                self.assertEqual([], list((root / "pangea-data/runs").iterdir()))
                failed_receipt = json.loads(
                    (execute_args["evaluator_root"] / "primary-receipts/intake.json").read_text()
                )
                self.assertIn("nonzero_exit", failed_receipt["failures"])
                self.assertIn("native_error_event", failed_receipt["failures"])
                return
            if intake_write_failure_index is not None:
                original_write = runctl._write_evaluator_json_exclusive
                write_count = 0
                source_before = {
                    path.relative_to(root).as_posix(): (
                        path.lstat().st_mode, sha256(path.read_bytes()).hexdigest()
                    )
                    for repository_id in ("spdk", "nvme-cli")
                    for path in (root / "repositories" / repository_id).rglob("*")
                    if path.is_file()
                }

                def fail_evaluator_write(path, value, *, canonical):
                    nonlocal write_count
                    write_count += 1
                    if write_count == intake_write_failure_index:
                        raise runctl.RunCtlError("injected evaluator intake write failure")
                    return original_write(path, value, canonical=canonical)

                with patch.object(runctl, "_write_evaluator_json_exclusive", side_effect=fail_evaluator_write):
                    with self.assertRaises(pangea_execution.PangeaExecutionError):
                        _execute_pangea_test_harness(spec, root, **execute_args)
                self.assertEqual([], list((root / "pangea-data/runs").iterdir()))
                contract_record = json.loads(next((root / "pangea-data/contracts").glob("*/contract.json")).read_text())
                self.assertEqual("confirmed", contract_record["status"])
                self.assertIsNone(contract_record.get("activation"))
                self.assertEqual(
                    {"preflight-receipt.json", "evaluator-intake-spec.json"},
                    {path.name for path in (root / "pangea-data/session").iterdir()},
                )
                contract_directories = list((root / "pangea-data/contracts").iterdir())
                self.assertEqual(1, len(contract_directories))
                self.assertEqual({"contract.json"}, {path.name for path in contract_directories[0].iterdir()})
                source_after = {
                    path.relative_to(root).as_posix(): (
                        path.lstat().st_mode, sha256(path.read_bytes()).hexdigest()
                    )
                    for repository_id in ("spdk", "nvme-cli")
                    for path in (root / "repositories" / repository_id).rglob("*")
                    if path.is_file()
                }
                self.assertEqual(source_before, source_after)
                failed_receipt = json.loads(
                    (execute_args["evaluator_root"] / "primary-receipts/intake.json").read_text()
                )
                self.assertIn("nonzero_exit", failed_receipt["failures"])
                self.assertIn("native_error_event", failed_receipt["failures"])
                return
            if (mutate_candidate_after_intake or mutate_case_after_intake
                    or mutate_stage_receipt_after_intake or add_project_plugin_after_intake):
                self.assertEqual([], benchmark.validate_public_bundle(root))
                direct_binding = benchmark._capture_validated_public_bundle_binding(root)
                original_persist = pangea_execution._persist_managed_primary_receipt

                def persist_then_mutate(run_dir, phase, primary_receipt, evidence_class, input_bindings=None):
                    digest = original_persist(
                        run_dir, phase, primary_receipt, evidence_class, input_bindings,
                    )
                    if phase == "intake":
                        if mutate_candidate_after_intake:
                            task.write_text("changed after intake\n")
                        if mutate_case_after_intake:
                            case_path = root / "CASE.json"
                            case_path.chmod(0o644)
                            case_path.write_text('{"id":"changed-after-intake"}\n')
                            case_path.chmod(0o444)
                        if mutate_stage_receipt_after_intake:
                            stage_receipt_path = root / "stage-receipt.json"
                            expected_mode = stage_receipt_path.lstat().st_mode & 0o777
                            self.assertEqual(0, expected_mode & 0o222)
                            stage_receipt_path.chmod(expected_mode | 0o200)
                            try:
                                stage_receipt_path.write_text(
                                    '{"case_id":"changed-after-intake"}\n', encoding="utf-8",
                                )
                            finally:
                                stage_receipt_path.chmod(expected_mode)
                        if add_project_plugin_after_intake:
                            (root / "opencode.json").write_text('{"plugin":["candidate-extra-plugin"]}\n')
                    return digest

                with patch.object(pangea_execution, "_persist_managed_primary_receipt",
                                  side_effect=persist_then_mutate):
                    with self.assertRaisesRegex(pangea_execution.PangeaExecutionError,
                                                "analysis-worker execution failed"):
                        _execute_pangea_test_harness(spec, root, **execute_args)
                self.assertEqual(1, session_counter)
                self.assertEqual(["pangea-test"], run_agents)
                with self.assertRaisesRegex(benchmark.BenchmarkContractError,
                                            "bound public bundle integrity failed"):
                    benchmark._validate_bound_public_bundle(root, direct_binding)
                return
            if extra_plugin_on_preflight:
                with self.assertRaisesRegex(pangea_execution.PangeaExecutionError,
                                            "primary intake did not establish one managed Run"):
                    _execute_pangea_test_harness(spec, root, **execute_args)
                self.assertEqual(0, session_counter)
                intake_path = execute_args["evaluator_root"] / "primary-receipts/intake.json"
                intake_receipt = json.loads(intake_path.read_text())
                self.assertEqual(["resolved_plugin_closure_violation"], intake_receipt["failures"])
                self.assertFalse(intake_receipt["passed"])
                return
            if extra_on_first_finalize:
                with self.assertRaisesRegex(pangea_execution.PangeaExecutionError,
                                            "primary finalization failed"):
                    _execute_pangea_test_harness(spec, root, **execute_args)
                return
            if timeout_during_seal:
                now = [0.0]; original_bindings = composer._run_file_bindings
                def advancing_bindings(run_dir):
                    rows = original_bindings(run_dir); now[0] = 1800.01; return rows
                execute_args["monotonic"] = lambda: now[0]
                with patch.object(composer, "_run_file_bindings", side_effect=advancing_bindings):
                    with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "wall-clock"):
                        _execute_pangea_test_harness(spec, root, **execute_args)
                self.assertFalse((root / "pangea-data/runs" / run_id / "internal/composed-receipt.json").exists())
                return
            receipt = _execute_pangea_test_harness(spec, root, **execute_args)
            self.assertEqual("composed_run_receipt", receipt["artifact_type"])
            self.assertEqual("test-only", receipt["evidence_class"])
            managed_run=root/"pangea-data/runs"/run_id
            assignment_count=len(json.loads((managed_run/"internal/assignment-index.json").read_text())["payload"]["assignments"])
            capacity_plan=json.loads((managed_run/"internal/context-publication-state.json").read_text())["payload"]["capacity_plan"]
            self.assertLessEqual(capacity_plan["worst_model_calls"],40)
            self.assertEqual(assignment_count,run_agents.count("analysis-leaf"))
            self.assertEqual(1,run_agents.count("audit-leaf"));self.assertEqual(1,run_agents.count("auditor"))
            self.assertNotIn("analysis-worker",run_agents)
            self.assertEqual(1,intake_exact_executions)
            signed_pairs={(value["receipt"]["logical_role"],value["receipt"]["execution_agent"])
                          for value in (json.loads(path.read_text()) for path in (managed_run/"internal/execution-receipts").glob("*.json"))}
            self.assertIn(("analysis-worker","analysis-leaf"),signed_pairs)
            self.assertIn(("auditor","audit-leaf"),signed_pairs)
            report_attestation=json.loads(next((managed_run/"internal/final-audit-execution-receipts").glob("*.json")).read_text())
            self.assertEqual(("auditor","auditor"),(report_attestation["receipt"]["logical_role"],
                                                     report_attestation["receipt"]["execution_agent"]))
            self.assertEqual({".md", ".html"}, {Path(row["path"]).suffix for row in receipt["formal_outputs"]})
            self.assertIn("report-auditor", [row["phase"] for row in receipt["evaluator_execution"]["phases"]])
            remaining = 40
            for row in receipt["evaluator_execution"]["phases"]:
                telemetry=row["telemetry"]
                expected_limit = min(remaining, 4) if row["phase"] == "intake" else 1
                self.assertEqual(expected_limit, telemetry["model_call_limit"])
                self.assertLessEqual(telemetry["model_requests_admitted"],expected_limit)
                self.assertEqual(telemetry["model_calls_completed"],telemetry["model_calls"])
                self.assertTrue(telemetry["injected_test_runner"])
                self.assertFalse(telemetry["pre_request_budget_enforced"])
                remaining -= telemetry["model_requests_admitted"]
                self.assertGreaterEqual(remaining,0)
            aggregate=receipt["evaluator_execution"]["aggregate_telemetry"]
            self.assertEqual(40-remaining,aggregate["model_requests_admitted"])
            self.assertLessEqual(aggregate["model_requests_admitted"],40)
            if default_evaluator_root:
                return
            primary_receipts = execute_args["evaluator_root"] / "primary-receipts"
            self.assertEqual({"intake.json", "resume.json", "finalize.json"},
                             {path.name for path in primary_receipts.iterdir()})
            intake_receipt = json.loads((primary_receipts / "intake.json").read_text())
            self.assertEqual(["external_role_execution_required"], intake_receipt["failures"])
            self.assertFalse(intake_receipt["passed"])
            self.assertIn("command_sha256", intake_receipt)
            self.assertRegex(intake_receipt["model_budget_hook_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(
                ["phase_prompt", *runctl.EVALUATOR_INTAKE_INPUT_BINDING_NAMES],
                [row["name"] for row in intake_receipt["input_bindings"]],
            )
            self.assertNotIn("command", intake_receipt)
            self.assertNotIn("test-provider-value", (primary_receipts / "intake.json").read_text())
            self.assertEqual({"intake", "resume", "finalize"},
                             set(receipt["evaluator_execution"]["primary_receipt_sha256s"]))
            self.assertEqual("test-only", intake_receipt["evidence_class"])
            self.assertEqual(3 if intake_permission_prefix else 1,
                             intake_receipt["telemetry"]["intake_attempt_summary"]["attempts"])
            calls_before = session_counter
            second = _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"})
            self.assertEqual(receipt, second); self.assertEqual(calls_before, session_counter)
            composed_path = root / "pangea-data/runs" / run_id / "internal/composed-receipt.json"
            composed_bytes = composed_path.read_bytes()

            def assert_resealed_receipt_rejected(mutator) -> None:
                changed = json.loads(composed_bytes)
                mutator(changed)
                changed["sha256"] = composer._hash({key: value for key, value in changed.items()
                                                     if key != "sha256"})
                composed_path.chmod(0o600)
                composed_path.write_text(json.dumps(changed, sort_keys=True, indent=2) + "\n")
                composed_path.chmod(0o400)
                try:
                    with self.assertRaises(pangea_execution.PangeaExecutionError):
                        _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"})
                    self.assertEqual(calls_before, session_counter)
                finally:
                    composed_path.chmod(0o600)
                    composed_path.write_bytes(composed_bytes)
                    composed_path.chmod(0o400)

            assert_resealed_receipt_rejected(lambda value: value.__setitem__("final_output_sha256", "0" * 64))
            assert_resealed_receipt_rejected(lambda value: value.pop("final_output_sha256"))
            assert_resealed_receipt_rejected(lambda value: value.__setitem__("unknown", True))
            assert_resealed_receipt_rejected(
                lambda value: value["evaluator_execution"]["phases"][0].__setitem__("unknown", True))
            assert_resealed_receipt_rejected(
                lambda value: value["evaluator_execution"]["aggregate_telemetry"].pop("tool_calls"))
            assert_resealed_receipt_rejected(
                lambda value: value["evaluator_execution"].__setitem__(
                    "analysis_worker_parallelism", 1,
                ))
            assert_resealed_receipt_rejected(
                lambda value: value["evaluator_execution"].pop("analysis_worker_parallelism"))

            # A receipt produced by the pre-parallel evaluator has no scheduler
            # marker and its wall lower bound is the serial sum.  Preserve that
            # exact historical replay contract while requiring 1.1 receipts to
            # bind the frozen width above.
            legacy = json.loads(composed_bytes)
            legacy["schema_version"] = "1.0"
            legacy["evaluator_execution"].pop("analysis_worker_parallelism")
            legacy["evaluator_execution"]["aggregate_telemetry"]["wall_seconds"] = sum(
                row["telemetry"]["duration_seconds"]
                for row in legacy["evaluator_execution"]["phases"]
            )
            legacy["sha256"] = composer._hash({
                key: value for key, value in legacy.items() if key != "sha256"
            })
            composed_path.chmod(0o600)
            composed_path.write_text(json.dumps(legacy, sort_keys=True, indent=2) + "\n")
            composed_path.chmod(0o400)
            try:
                self.assertEqual(
                    legacy,
                    _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"}),
                )
                self.assertEqual(calls_before, session_counter)
            finally:
                composed_path.chmod(0o600)
                composed_path.write_bytes(composed_bytes)
                composed_path.chmod(0o400)
            assert_resealed_receipt_rejected(
                lambda value: value["evaluator_execution"]["phases"][0]["telemetry"].__setitem__(
                    "pre_request_budget_blocked", True))
            assert_resealed_receipt_rejected(
                lambda value: value["evaluator_execution"]["phases"][0]["telemetry"][
                    "intake_attempt_summary"]["status_sequence"].append("completed_exact"))

            def mutate_intake_bindings(value, mutator) -> None:
                bindings = value["evaluator_execution"]["phases"][0]["input_bindings"]
                mutator(bindings)

            assert_resealed_receipt_rejected(
                lambda value: mutate_intake_bindings(value, lambda rows: rows.pop(1)))
            assert_resealed_receipt_rejected(
                lambda value: mutate_intake_bindings(
                    value, lambda rows: rows.append({"name": "unknown", "sha256": "0" * 64})))
            assert_resealed_receipt_rejected(
                lambda value: mutate_intake_bindings(
                    value, lambda rows: rows.__setitem__(slice(1, 3), reversed(rows[1:3]))))
            assert_resealed_receipt_rejected(
                lambda value: mutate_intake_bindings(
                    value, lambda rows: next(
                        row for row in rows if row["name"] == "confirmed_contract_record"
                    ).__setitem__("sha256", "0" * 64)))

            durable_confirmed = (
                root / "pangea-data/runs" / run_id
                / runctl.EVALUATOR_CONFIRMED_CONTRACT_RELATIVE
            )
            durable_confirmed_bytes = durable_confirmed.read_bytes()

            def restore_durable_confirmed() -> None:
                if durable_confirmed.exists() or durable_confirmed.is_symlink():
                    if not durable_confirmed.is_symlink():
                        durable_confirmed.chmod(0o600)
                    durable_confirmed.unlink()
                durable_confirmed.write_bytes(durable_confirmed_bytes)
                durable_confirmed.chmod(0o400)

            def assert_durable_confirmed_rejected(mutator, cleanup=lambda: None) -> None:
                calls_before_mutation = session_counter
                try:
                    mutator()
                    with self.assertRaises(pangea_execution.PangeaExecutionError):
                        _execute_pangea_test_harness(
                            spec, root, run=runner, environ={"PATH": "/bin"},
                        )
                    self.assertEqual(calls_before_mutation, session_counter)
                finally:
                    cleanup()
                    restore_durable_confirmed()

            assert_durable_confirmed_rejected(durable_confirmed.unlink)
            evaluator_extra = durable_confirmed.with_name("evaluator-unexpected.json")
            assert_durable_confirmed_rejected(
                lambda: (evaluator_extra.write_text("{}"), evaluator_extra.chmod(0o400)),
                lambda: evaluator_extra.unlink(missing_ok=True),
            )
            assert_durable_confirmed_rejected(
                lambda: (durable_confirmed.unlink(), durable_confirmed.write_text("{}"),
                         durable_confirmed.chmod(0o400)))
            assert_durable_confirmed_rejected(
                lambda: (durable_confirmed.unlink(), durable_confirmed.symlink_to(
                    Path(runctl.EVALUATOR_INTAKE_RUN_SPEC_RELATIVE).name)))
            assert_durable_confirmed_rejected(lambda: durable_confirmed.chmod(0o600))
            confirmed_link = durable_confirmed.with_name("confirmed-record-link.json")
            assert_durable_confirmed_rejected(
                lambda: os.link(durable_confirmed, confirmed_link),
                lambda: confirmed_link.unlink(missing_ok=True),
            )

            def drift_confirmed_hash() -> None:
                changed = json.loads(durable_confirmed_bytes)
                changed["unexpected"] = True
                durable_confirmed.chmod(0o600)
                durable_confirmed.write_text(
                    json.dumps(changed, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                )
                durable_confirmed.chmod(0o400)

            assert_durable_confirmed_rejected(drift_confirmed_hash)

            durable_spec = root / "pangea-data/runs" / run_id / runctl.EVALUATOR_INTAKE_RUN_SPEC_RELATIVE
            durable_binding = root / "pangea-data/runs" / run_id / runctl.EVALUATOR_INTAKE_BINDING_RELATIVE
            managed_intake_receipt = (
                root / "pangea-data/runs" / run_id / "internal/primary-receipts/intake.json"
            )
            durable_originals = {
                path: path.read_bytes() for path in
                (durable_spec, durable_binding, managed_intake_receipt, composed_path)
            }

            def write_read_only_json(path: Path, value, *, sorted_keys: bool = False) -> None:
                path.chmod(0o600)
                path.write_text(json.dumps(
                    value, ensure_ascii=False, sort_keys=sorted_keys, indent=2,
                ) + "\n")
                path.chmod(0o400)

            def assert_resigned_durable_spec_rejected(mutator) -> None:
                calls_before_mutation = session_counter
                try:
                    changed_spec = json.loads(durable_originals[durable_spec])
                    mutator(changed_spec)
                    write_read_only_json(durable_spec, changed_spec)
                    spec_hash = sha256(durable_spec.read_bytes()).hexdigest()
                    changed_binding = json.loads(durable_originals[durable_binding])
                    changed_binding["spec_sha256"] = spec_hash
                    write_read_only_json(durable_binding, changed_binding)
                    changed_primary = json.loads(durable_originals[managed_intake_receipt])
                    next(row for row in changed_primary["input_bindings"]
                         if row["name"] == "evaluator_intake_spec")["sha256"] = spec_hash
                    write_read_only_json(managed_intake_receipt, changed_primary, sorted_keys=True)
                    primary_hash = sha256(managed_intake_receipt.read_bytes()).hexdigest()
                    changed_composed = json.loads(durable_originals[composed_path])
                    phase = next(row for row in changed_composed["evaluator_execution"]["phases"]
                                 if row["phase"] == "intake" and row["role"] == "primary")
                    next(row for row in phase["input_bindings"]
                         if row["name"] == "evaluator_intake_spec")["sha256"] = spec_hash
                    changed_composed["evaluator_execution"]["primary_receipt_sha256s"]["intake"] = primary_hash
                    changed_composed["primary_intake_sha256"] = primary_hash
                    current_hashes = {
                        durable_spec.relative_to(root / "pangea-data/runs" / run_id).as_posix(): spec_hash,
                        durable_binding.relative_to(root / "pangea-data/runs" / run_id).as_posix(): sha256(durable_binding.read_bytes()).hexdigest(),
                        managed_intake_receipt.relative_to(root / "pangea-data/runs" / run_id).as_posix(): primary_hash,
                    }
                    for row in changed_composed["run_file_bindings"]:
                        if row["path"] in current_hashes:
                            row["sha256"] = current_hashes[row["path"]]
                    changed_composed["sha256"] = composer._hash({
                        key: value for key, value in changed_composed.items() if key != "sha256"
                    })
                    write_read_only_json(composed_path, changed_composed, sorted_keys=True)
                    with self.assertRaises(pangea_execution.PangeaExecutionError):
                        _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"})
                    self.assertEqual(calls_before_mutation, session_counter)
                finally:
                    for path, payload in durable_originals.items():
                        path.chmod(0o600); path.write_bytes(payload); path.chmod(0o400)

            assert_resigned_durable_spec_rejected(lambda value: value.__setitem__("unknown", True))
            assert_resigned_durable_spec_rejected(
                lambda value: value["repository"].__setitem__("unknown", True))
            assert_resigned_durable_spec_rejected(lambda value: value["contract"].pop("revision"))

            def zero_first_phase(value, *, zero_limit: bool) -> None:
                telemetry = value["evaluator_execution"]["phases"][0]["telemetry"]
                telemetry["model_calls"] = 0
                telemetry["model_calls_completed"] = 0
                telemetry["model_requests_admitted"] = 0
                if zero_limit:
                    telemetry["model_call_limit"] = 0

            assert_resealed_receipt_rejected(lambda value: zero_first_phase(value, zero_limit=False))
            assert_resealed_receipt_rejected(lambda value: zero_first_phase(value, zero_limit=True))

            immutable_mutations = [
                (task, "changed completed task\n"),
                (root / "repositories/spdk/lib/nvmf/tcp.c", "int changed_fixture(void) { return 1; }\n"),
                (root / "opencode.json", '{"plugin":["candidate-extra-plugin"]}\n'),
                (root / "immutable-extra.txt", "extra\n"),
            ]
            for immutable_path, changed_text in immutable_mutations:
                existed = immutable_path.exists()
                original = immutable_path.read_bytes() if existed else None
                immutable_path.write_text(changed_text)
                try:
                    with self.assertRaisesRegex(pangea_execution.PangeaExecutionError,
                                                "immutable public bundle binding mismatch"):
                        _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"})
                    self.assertEqual(calls_before, session_counter)
                finally:
                    if existed:
                        immutable_path.write_bytes(original)
                    else:
                        immutable_path.unlink()
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "rejects a test-only"):
                execute_pangea_as_shipped(spec, root, environ={"PATH": "/bin"})
            self.assertEqual(calls_before, session_counter)
            with self.assertRaises(TypeError):
                execute_pangea_as_shipped(spec, root, run=runner)  # type: ignore[call-arg]
            self.assertEqual(calls_before, session_counter)
            deadline_clock = Mock(side_effect=[0.0, 1800.01])
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "wall-clock"):
                _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"},
                                          monotonic=deadline_clock)
            self.assertEqual(calls_before, session_counter)
            analysis_path = root / "pangea-data/runs" / run_id / "internal/analysis-model.json"
            original_analysis = analysis_path.read_bytes()
            analysis_path.chmod(0o600); analysis_path.write_text("{}\n"); analysis_path.chmod(0o400)
            with self.assertRaises(pangea_execution.PangeaExecutionError):
                _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"})
            analysis_path.chmod(0o600); analysis_path.write_bytes(original_analysis); analysis_path.chmod(0o400)
            report_extra = root / "pangea-data/reports" / run_id / "extra.txt"; report_extra.write_text("extra\n")
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "formal output directory member closure"):
                _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"})
            report_extra.unlink()
            extra = root / "pangea-data/runs" / run_id / "internal/unexpected.json"; extra.write_text("{}\n")
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "file closure"):
                _execute_pangea_test_harness(spec, root, run=runner, environ={"PATH": "/bin"})
            self.assertEqual(calls_before, session_counter)

    def test_first_finalize_rejects_extra_formal_directory_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); run = root / "pangea-data/runs/run-1"; run.mkdir(parents=True)
            reports = root / "pangea-data/reports/run-1"; reports.mkdir(parents=True)
            (reports / "report.md").write_text("# report\n")
            (reports / "report.html").write_text("<p>report</p>\n")
            (reports / "extra.json").write_text("{}\n")
            (run / "manifest.json").write_text(json.dumps({
                "deliverables": {"report_md": "reports/run-1/report.md",
                                 "report_html": "reports/run-1/report.html"},
            }))
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "member closure"):
                pangea_execution._formal_outputs(root, run)

    def test_public_entry_rejects_extra_member_created_by_first_finalize(self) -> None:
        self._extra_on_first_finalize = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._extra_on_first_finalize

    def test_public_entry_converges_after_two_permission_denied_intake_attempts(self) -> None:
        self._intake_permission_prefix = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._intake_permission_prefix

    def test_extra_resolved_plugin_fails_before_run_with_durable_primary_receipt(self) -> None:
        self._extra_plugin_on_preflight = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._extra_plugin_on_preflight

    def test_immutable_candidate_change_after_intake_fails_before_leaf_or_resume(self) -> None:
        self._mutate_candidate_after_intake = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._mutate_candidate_after_intake

    def test_immutable_managed_repository_change_after_prepare_fails_before_primary(self) -> None:
        self._immutable_drift_before_primary = "managed-repository"
        try:
            with self.assertRaisesRegex(
                pangea_execution.PangeaExecutionError, "immutable public bundle changed",
            ):
                self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._immutable_drift_before_primary

    def test_immutable_root_entry_change_after_prepare_fails_before_primary(self) -> None:
        self._immutable_drift_before_primary = "root-entry"
        self._root_drift_model_runner_calls = 0
        try:
            with self.assertRaisesRegex(
                benchmark.BenchmarkContractError,
                "bound public bundle integrity failed: out_of_scope_bundle_file_added",
            ):
                self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
            self.assertEqual(0, self._root_drift_model_runner_calls)
        finally:
            del self._immutable_drift_before_primary
            del self._root_drift_model_runner_calls

    def test_case_change_after_intake_fails_before_leaf_or_resume(self) -> None:
        self._mutate_case_after_intake = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._mutate_case_after_intake

    def test_stage_receipt_change_after_intake_fails_before_leaf_or_resume(self) -> None:
        self._mutate_stage_receipt_after_intake = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._mutate_stage_receipt_after_intake

    def test_writable_confirmed_contract_is_rejected_before_activation(self) -> None:
        self._writable_confirmed_on_intake = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._writable_confirmed_on_intake

    def test_each_evaluator_intake_durable_write_failure_rolls_back_activation(self) -> None:
        for write_index in (1, 2, 3, 4):
            with self.subTest(write_index=write_index):
                self._intake_write_failure_index = write_index
                try:
                    self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
                finally:
                    del self._intake_write_failure_index

    def test_root_project_plugin_added_after_intake_fails_before_leaf_or_resume(self) -> None:
        self._add_project_plugin_after_intake = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._add_project_plugin_after_intake

    def test_sealing_closures_crossing_deadline_leave_no_composed_receipt(self) -> None:
        self._timeout_during_seal = True
        try:
            self.test_public_entry_completes_real_managed_run_with_mock_subprocess()
        finally:
            del self._timeout_during_seal

    def test_public_entry_is_reachable_and_rejects_root_drift_before_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp); bundle = base / "bundle"; bundle.mkdir()
            task, case_id, case_hash = bind_canonical_case_fixture(bundle, framing="local task")
            policy = base / "policy.json"
            policy.write_text(json.dumps(benchmark._track(benchmark.load_frozen_config(), "as-shipped", "pangea")))
            spec = benchmark.RunSpec(
                "pangea", "as-shipped", bundle, task, policy, case_id, "CASE.json", case_hash,
            )
            runner = Mock()
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "public bundle root"):
                _execute_pangea_test_harness(spec, base, run=runner)
            runner.assert_not_called()

    def test_typed_primary_receipts_aggregate_across_phases(self) -> None:
        runtime = {"max_model_calls": 2, "context_window": 200000,
                   "max_output_tokens": 4096, "max_wall_clock_seconds": 1800}
        budget = pangea_execution._AggregateBudget(runtime, 120)
        budget.add_primary("intake", _receipt("intake-session", "intake", outputs=3000, limit=2))
        budget.add_primary("resume", _receipt("resume-session", "resume", outputs=1500, limit=1))
        self.assertEqual(0, budget.remaining_model_calls())
        self.assertEqual(2, budget.model_calls)
        self.assertEqual(4500, budget.output_tokens)

    def test_primary_receipt_is_durable_before_primary_failure_and_rejects_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            evaluator = Path(temp)
            failed = _receipt("intake-session", "intake")
            digest = pangea_execution._persist_primary_receipt(evaluator, "intake", failed)
            path = evaluator / "primary-receipts/intake.json"
            stored = json.loads(path.read_text())
            self.assertEqual(failed.failures, stored["failures"])
            self.assertEqual(digest, benchmark.sha256(path.read_bytes()).hexdigest())
            self.assertNotIn("command", stored)
            self.assertNotIn("stdout", stored)
            self.assertNotIn("stderr", stored)
            self.assertNotIn("environment", stored)
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "replacement"):
                pangea_execution._persist_primary_receipt(evaluator, "intake", failed)
            (evaluator / "primary-receipts/unexpected.json").write_text("{}\n")
            with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "closure"):
                pangea_execution._persist_primary_receipt(evaluator, "resume", _receipt("resume-session", "resume"))

    def test_stale_or_reused_session_fails_closed(self) -> None:
        runtime = {"max_model_calls": 40, "context_window": 200000,
                   "max_output_tokens": 4096, "max_wall_clock_seconds": 1800}
        budget = pangea_execution._AggregateBudget(runtime, 120)
        budget.add_primary("intake", _receipt("same-session", "intake"))
        with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "reused"):
            budget.add_primary("resume", _receipt("same-session", "resume"))

    def test_blocked_primary_phase_is_rejected_without_aggregate_side_effects(self) -> None:
        runtime = {"max_model_calls": 40, "context_window": 200000,
                   "max_output_tokens": 4096, "max_wall_clock_seconds": 1800}
        budget = pangea_execution._AggregateBudget(runtime, 120)
        receipt = _receipt("blocked-intake", "intake")
        receipt.telemetry["pre_request_budget_blocked"] = True
        before = (budget.model_calls, budget.model_requests_admitted, budget.tool_calls,
                  budget.input_tokens, budget.output_tokens, set(budget.sessions), list(budget.phases))
        with self.assertRaisesRegex(pangea_execution.PangeaExecutionError,
                                    "invalid pre-request model telemetry"):
            budget.add_primary("intake", receipt)
        after = (budget.model_calls, budget.model_requests_admitted, budget.tool_calls,
                 budget.input_tokens, budget.output_tokens, set(budget.sessions), list(budget.phases))
        self.assertEqual(before, after)

    def test_end_to_end_wall_clock_includes_preflight_and_composition(self) -> None:
        runtime = {"max_model_calls": 40, "context_window": 200000,
                   "max_output_tokens": 4096, "max_wall_clock_seconds": 50}
        now = [0.0]
        budget = pangea_execution._AggregateBudget(runtime, 120, 0.0, lambda: now[0])
        now[0] = 12.0
        budget.add_primary("intake", _receipt("wall-intake", "intake"), duration=12.0)
        self.assertEqual(12.0, budget.phases[0]["telemetry"]["duration_seconds"])
        # No provider phase runs while the deterministic composer advances,
        # but the sealing snapshot must still include that elapsed time.
        now[0] = 45.0
        self.assertEqual(45.0, budget.snapshot()["aggregate_telemetry"]["wall_seconds"])
        now[0] = 50.01
        with self.assertRaisesRegex(pangea_execution.PangeaExecutionError, "wall-clock"):
            budget.check_wall()

    def test_run_receipt_adapter_does_not_accept_mapping_substitutes(self) -> None:
        with self.assertRaises(benchmark.BenchmarkContractError):
            benchmark.run_receipt_payload({"passed": False})  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
