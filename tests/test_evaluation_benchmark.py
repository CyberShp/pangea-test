from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

from benchmarks import stage as public_stage
from evaluation import benchmark
from runtime import fragment_runtime


def bind_canonical_case_fixture(
    bundle: Path, *, framing: str = "", candidate: str = "pangea",
    candidate_manifest_sha256: str | None = None, staged_repositories: bool = False,
) -> tuple[Path, str, str]:
    """Test-only helper that builds the mandatory production case bindings."""
    case = public_stage.load_manifest()["cases"][0]
    case_payload = public_stage.canonical_case_payload(case)
    case_hash = sha256(case_payload).hexdigest()
    task_text = f"{framing.rstrip()}\n{case['agent_input']}" if framing.strip() else case["agent_input"]
    task = bundle / "TASK.md"
    task.write_text(task_text, encoding="utf-8")
    case_path = bundle / "CASE.json"
    case_path.write_bytes(case_payload)
    contract_hash = sha256(json.dumps(
        case["contract"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    receipt = {
        "schema_version": "1.0",
        "candidate": candidate,
        "candidate_files": ({"codetalks-evaluator-manifest.json": candidate_manifest_sha256}
                            if candidate == "fuse" else {}),
        "candidate_directories": [],
        "candidate_tree_sha256": "",
        "candidate_manifest_sha256": candidate_manifest_sha256,
        "task_hash": sha256(task_text.encode("utf-8")).hexdigest(),
        "task_binding_version": "canonical-agent-input-line-v1",
        "agent_input_sha256": sha256(case["agent_input"].encode("utf-8")).hexdigest(),
        "case_path": "CASE.json",
        "case_id": case["id"],
        "case_sha256": case_hash,
        "contract_projection_sha256": contract_hash,
    }
    candidate_identity = [
        f"F\0{path}\0{digest}\n" for path, digest in receipt["candidate_files"].items()
    ]
    receipt["candidate_tree_sha256"] = sha256(
        "".join(sorted(candidate_identity)).encode("utf-8")
    ).hexdigest()
    corpus = {row["id"]: row for row in benchmark.load_corpus_manifest()["repositories"]}
    receipt["repositories"] = [{
        "id": repository_id, "commit": corpus[repository_id]["commit"],
        "git_tree": corpus[repository_id]["tree"], "materialization_version": "git-object-v1",
        "materialization_sha256": sha256(repository_id.encode()).hexdigest(),
        "entry_count": 0, "entry_counts": {"regular": 0, "executable": 0, "symlink": 0, "gitlink": 0},
        "materialized_symlinks": [], "materialized_gitlinks": [], "executable_files": [],
    } for repository_id in ("spdk", "nvme-cli")]
    if staged_repositories:
        for repository_id in ("spdk", "nvme-cli"):
            repository_root = bundle / "repositories" / repository_id
            repository_root.mkdir(parents=True, exist_ok=True)
            paths = (case["source_scope"]["paths"] if repository_id == case["repository_id"]
                     else ["README.evaluator-fixture"])
            for relative in paths:
                path = repository_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"int {repository_id.replace('-', '_')}_fixture(void) {{ return 0; }}\n")
            row = next(row for row in receipt["repositories"] if row["id"] == repository_id)
            file_count = sum(1 for path in repository_root.rglob("*") if path.is_file())
            row["entry_count"] = file_count
            row["entry_counts"]["regular"] = file_count
    stage_receipt_path = bundle / "stage-receipt.json"
    stage_receipt_path.write_text(
        json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    stage_receipt_path.chmod(0o444)
    case_path.chmod(0o444)
    benchmark.write_public_bundle_manifest(bundle)
    return task, case["id"], case_hash


def _native_stream(
    text: str = '{"risks": []}',
    *,
    reason: str = "stop",
    input_tokens: int = 100,
    output_tokens: int = 20,
    tool: str | None = None,
    tool_input: dict[str, object] | None = None,
) -> str:
    base = {"timestamp": 1, "sessionID": "ses_test"}
    events = [
        {**base, "type": "step_start", "part": {"type": "step-start"}},
    ]
    if tool:
        events.append({
            **base,
            "type": "tool_use",
            "part": {"type": "tool", "tool": tool, "state": {"status": "completed", "input": tool_input if tool_input is not None else {"path": "TASK.md"}}},
        })
    events.extend([
        {**base, "type": "text", "part": {"type": "text", "text": text}},
        {
            **base,
            "type": "step_finish",
            "part": {
                "type": "step-finish",
                "reason": reason,
                "cost": 0,
                "tokens": {
                    "input": input_tokens,
                    "output": output_tokens,
                    "reasoning": 0,
                    "cache": {"read": 0, "write": 0},
                },
            },
        },
    ])
    return "".join(json.dumps(event) + "\n" for event in events)


def _intake_convergence_stream(bundle: Path, attempts: list[tuple[str, str, dict[str, object], str | None]]) -> str:
    base = {"timestamp": 1, "sessionID": "ses_test"}
    events = [{**base, "type": "step_start", "part": {"type": "step-start"}}]
    permission_error = benchmark._OPENCODE_PERMISSION_ERROR_PREFIX + " " + json.dumps(
        benchmark._canonical_intake_permission_rules(), separators=(",", ":"))
    for tool, status, tool_input, error in attempts:
        state: dict[str, object] = {"status": status, "input": tool_input, "time": {"start": 1, "end": 2}}
        if status == "error":
            state["error"] = permission_error if error in {"permission","permission_with_output"} else (error or "local tool error")
            if error == "permission_with_output": state.update({"metadata": {}, "output": "unexpected"})
        else: state.update({"metadata": {}, "output": "", "title": "completed"})
        events.append({**base, "type": "tool_use", "part": {"type": "tool", "tool": tool, "state": state}})
    events.extend([
        {**base, "type": "text", "part": {"type": "text", "text": "external handoff ready"}},
        {**base, "type": "step_finish", "part": {"type": "step-finish", "reason": "stop",
            "tokens": {"input": 100, "output": 20, "reasoning": 0, "cache": {"read": 0, "write": 0}}}},
    ])
    return "".join(json.dumps(event) + "\n" for event in events)


def _debug_config(*enabled: str, name: str = "pangea-test", mode: str = "primary", safe_overlay: bool = False,
                  primary_task_enabled: bool = True, primary_phase: str | None = None,
                  prompt: str = "hash-only in receipt", tool_free: bool = False) -> str:
    known = benchmark.EQUAL_TOOLS | benchmark.AS_SHIPPED_SAFE_TOOLS | benchmark.FORBIDDEN_TOOLS
    permissions: list[dict[str, str]] = []
    if tool_free:
        permissions.append({"permission":"*","pattern":"*","action":"deny"})
    elif safe_overlay:
        overlay = benchmark._as_shipped_safety_overlay(
            "pangea-test", ["analysis-worker", "auditor"],
            primary_task_enabled=primary_task_enabled,
            primary_phase=primary_phase,
        )
        scoped=overlay["agent"].get(name,overlay["agent"]["pangea-test"])
        for permission, rule in scoped["permission"].items():
            if isinstance(rule, str):
                permissions.append({"permission": permission, "pattern": "*", "action": rule})
            else:
                permissions.extend({"permission": permission, "pattern": pattern, "action": action} for pattern, action in rule.items())
    else:
        permissions.append({"permission": "external_directory", "pattern": "*", "action": "deny"})
    return json.dumps({
        "name": name,
        "mode": mode,
        "prompt": prompt,
        "permission": permissions,
        "tools": {name: name in set(enabled) for name in sorted(known)},
    })


def _resolved_plugin_config_result(kwargs: dict[str, object], mutate=None) -> Mock:
    overlay = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
    plugins = list(overlay["plugin"])
    if mutate is not None:
        plugins = mutate(plugins)
    return Mock(returncode=0, stdout=json.dumps({"plugin": plugins}), stderr="")


def _sequence_runner(responses, *, plugin_mutation=None) -> Mock:
    remaining = iter(responses)

    def side_effect(*args, **kwargs):
        command = args[0]
        if command[:3] == ["opencode", "debug", "config"]:
            return _resolved_plugin_config_result(kwargs, plugin_mutation)
        response = next(remaining)
        if isinstance(response, BaseException):
            raise response
        return response

    return Mock(side_effect=side_effect)


class EvaluationBenchmarkTests(unittest.TestCase):
    def test_real_opencode_1184_resolved_permission_projection_is_closed(self) -> None:
        executable = shutil.which("opencode")
        if executable is None:
            self.skipTest("OpenCode is not installed")
        version = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=False, timeout=30,
        )
        if version.returncode != 0 or version.stdout.strip() != "1.18.4":
            self.skipTest("test is frozen to OpenCode 1.18.4")
        debug_timeout = benchmark.load_frozen_config()["runtime"]["opencode_debug_timeout_seconds"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "bundle"
            (bundle / ".opencode" / "agents").mkdir(parents=True)
            for name in ("pangea-test", "analysis-worker", "auditor"):
                shutil.copyfile(
                    benchmark.ROOT / ".opencode" / "agents" / f"{name}.md",
                    bundle / ".opencode" / "agents" / f"{name}.md",
                )
            for skill in benchmark.AS_SHIPPED_SKILLS:
                target = bundle / ".opencode" / "skills" / skill
                shutil.copytree(
                    benchmark.ROOT / ".opencode" / "skills" / skill,
                    target,
                )
            (bundle / "TASK.md").write_text("local debug-only metadata preflight\n")
            benchmark.write_public_bundle_manifest(bundle)
            isolated = root / "opencode-env"
            for name in ("home", "config", "data", "cache", "tool-output"):
                (isolated / name).mkdir(parents=True)
            workers = ["analysis-worker", "auditor"]
            safety = benchmark._as_shipped_safety_overlay("pangea-test", workers)
            overlay = benchmark._frozen_deepseek_provider_overlay()
            overlay.update(safety)
            hook = benchmark._install_model_budget_hook(overlay, isolated, 40)
            env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": str(isolated / "home"),
                "XDG_CONFIG_HOME": str(isolated / "config"),
                "XDG_DATA_HOME": str(isolated / "data"),
                "XDG_CACHE_HOME": str(isolated / "cache"),
                "TMPDIR": str(isolated / "tool-output"),
                "TMP": str(isolated / "tool-output"),
                "TEMP": str(isolated / "tool-output"),
                "DEEPSEEK_API_KEY": "fake-debug-only-key",
                "OPENCODE_CONFIG_CONTENT": json.dumps(overlay, sort_keys=True, separators=(",", ":")),
            }
            config_debug = subprocess.run(
                [executable, "debug", "config"], cwd=bundle, env=env,
                capture_output=True, text=True, check=False, timeout=debug_timeout,
            )
            self.assertEqual(0, config_debug.returncode, config_debug.stderr)
            closure, closure_failures = benchmark._resolved_plugin_closure(
                config_debug.stdout, hook, isolated,
            )
            self.assertEqual([], closure_failures)
            self.assertTrue(closure["exact"])
            self.assertNotIn(str(root), repr(closure))
            expected = benchmark._track(benchmark.load_frozen_config(), "as-shipped")
            observed: dict[str, tuple[dict[str, object], str]] = {}
            for name, worker in (("pangea-test", False), ("analysis-worker", True)):
                debug = subprocess.run(
                    [executable, "debug", "agent", name], cwd=bundle, env=env,
                    capture_output=True, text=True, check=False, timeout=debug_timeout,
                )
                self.assertEqual(0, debug.returncode)
                receipt, failures = benchmark._resolved_agent_receipt(
                    debug.stdout, name, "as-shipped", expected, worker=worker,
                    isolated_root=isolated, public_bundle=bundle,
                    global_permission=safety["permission"],
                )
                self.assertEqual([], failures)
                self.assertNotIn(str(root), repr(receipt))
                observed[name] = (json.loads(debug.stdout), debug.stdout)

            main, _ = observed["pangea-test"]
            mutations = []
            extra_allow = deepcopy(main)
            extra_allow["permission"].append({"permission": "read", "pattern": "**", "action": "allow"})
            mutations.append(extra_allow)
            wider_allow = deepcopy(main)
            wider_index = next(i for i, rule in enumerate(wider_allow["permission"])
                               if rule == {"permission": "external_directory", "pattern": "*", "action": "ask"})
            wider_allow["permission"][wider_index]["action"] = "allow"
            mutations.append(wider_allow)
            order_override = deepcopy(main)
            ask_index = next(i for i, rule in enumerate(order_override["permission"])
                             if rule == {"permission": "external_directory", "pattern": "*", "action": "ask"})
            order_override["permission"].append(order_override["permission"].pop(ask_index))
            mutations.append(order_override)
            read_override = deepcopy(main)
            read_allows = [rule for rule in read_override["permission"]
                           if rule == {"permission": "read", "pattern": "*", "action": "allow"}]
            read_override["permission"] = [
                rule for rule in read_override["permission"]
                if rule != {"permission": "read", "pattern": "*", "action": "allow"}
            ]
            read_index = next(i for i, rule in enumerate(read_override["permission"])
                              if rule["permission"] == "read")
            read_override["permission"][read_index:read_index] = read_allows
            self.assertEqual(
                "ask", benchmark._permission_decision(read_override["permission"], "read", "secret.env"),
            )
            mutations.append(read_override)
            host_path = deepcopy(main)
            path_index = next(i for i, rule in enumerate(host_path["permission"])
                              if rule["permission"] == "external_directory"
                              and rule["action"] == "allow" and Path(rule["pattern"]).is_absolute())
            host_path["permission"][path_index]["pattern"] = "/Users/host/.local/share/opencode/tool-output/*"
            mutations.append(host_path)
            for mutated in mutations:
                _, failures = benchmark._resolved_agent_receipt(
                    json.dumps(mutated), "pangea-test", "as-shipped", expected,
                    isolated_root=isolated, public_bundle=bundle,
                    global_permission=safety["permission"],
                )
                self.assertIn("resolved_overlay_permission_violation", failures)

            unknown_tool = deepcopy(main)
            unknown_tool["tools"]["unknown-tool"] = True
            _, failures = benchmark._resolved_agent_receipt(
                json.dumps(unknown_tool), "pangea-test", "as-shipped", expected,
                isolated_root=isolated, public_bundle=bundle,
                global_permission=safety["permission"],
            )
            self.assertIn("resolved_tool_policy_violation", failures)

            intake_safety = benchmark._as_shipped_safety_overlay(
                "pangea-test", workers, primary_task_enabled=False, primary_phase="intake",
            )
            intake_policy = root / "intake-policy.json"
            intake_policy.write_text(json.dumps(expected))
            intake_spec = benchmark.RunSpec(
                "pangea", "as-shipped", bundle, bundle / "TASK.md", intake_policy,
                "debug-only", "CASE.json", "0" * 64,
            )
            intake_root = root / "intake-evaluator"
            intake_env, intake_environment_receipt, available, intake_hook = (
                benchmark._execution_environment(
                    intake_spec, expected, "pangea-test",
                    {"PATH": os.environ.get("PATH", ""),
                     "DEEPSEEK_API_KEY": "fake-debug-only-key",
                     "OPENCODE_DISABLE_MODELS_FETCH": "0"},
                    intake_root, primary_task_enabled=False,
                    primary_phase="intake", model_call_limit=4,
                )
            )
            self.assertTrue(available)
            self.assertEqual("1", intake_env["OPENCODE_DISABLE_MODELS_FETCH"])
            self.assertNotIn("OPENCODE_MODELS_PATH", intake_env)
            self.assertNotIn("OPENCODE_MODELS_URL", intake_env)
            self.assertTrue(intake_environment_receipt["models_metadata_fetch_disabled"])
            self.assertIn("OPENCODE_DISABLE_MODELS_FETCH",
                          intake_environment_receipt["environment_keys"])
            intake_isolated = intake_root / "opencode-env"
            intake_config_debug = subprocess.run(
                [executable, "debug", "config"], cwd=bundle, env=intake_env,
                capture_output=True, text=True, check=False, timeout=debug_timeout,
            )
            self.assertEqual(0, intake_config_debug.returncode, intake_config_debug.stderr)
            intake_config = json.loads(intake_config_debug.stdout)
            self.assertEqual(benchmark.DEEPSEEK_MODEL, intake_config["model"])
            deepseek = intake_config["provider"]["deepseek"]
            self.assertEqual("@ai-sdk/openai-compatible", deepseek["npm"])
            self.assertEqual(benchmark.DEEPSEEK_OFFICIAL_BASE_URL,
                             deepseek["options"]["baseURL"])
            self.assertEqual(200000, deepseek["models"]["deepseek-v4-flash"]["limit"]["context"])
            self.assertEqual(4096, deepseek["models"]["deepseek-v4-flash"]["limit"]["output"])
            _, intake_plugin_failures = benchmark._resolved_plugin_closure(
                intake_config_debug.stdout, intake_hook, intake_isolated,
            )
            self.assertEqual([], intake_plugin_failures)
            intake_debug = subprocess.run(
                [executable, "debug", "agent", "pangea-test"],
                cwd=bundle, env=intake_env, capture_output=True, text=True,
                check=False, timeout=debug_timeout,
            )
            intake_receipt, intake_failures = benchmark._resolved_agent_receipt(
                intake_debug.stdout, "pangea-test", "as-shipped", expected,
                primary_task_enabled=False, isolated_root=intake_isolated,
                primary_phase="intake", public_bundle=bundle,
                global_permission=intake_safety["permission"],
            )
            self.assertEqual(0, intake_debug.returncode)
            self.assertEqual([], intake_failures)
            self.assertEqual(["bash"], intake_receipt["enabled_tools"])
            resolved_intake = json.loads(intake_debug.stdout)
            self.assertTrue(resolved_intake["tools"]["task"])
            for target in sorted(benchmark.AS_SHIPPED_TASKS):
                self.assertEqual(
                    "deny", benchmark._permission_decision(
                        resolved_intake["permission"], "task", target,
                    ),
                )

                missing_deny = deepcopy(resolved_intake)
                deny_index = next(
                    index for index in range(len(missing_deny["permission"]) - 1, -1, -1)
                    if missing_deny["permission"][index]
                    == {"permission": "task", "pattern": target, "action": "deny"}
                )
                missing_deny["permission"].pop(deny_index)
                _, missing_failures = benchmark._resolved_agent_receipt(
                    json.dumps(missing_deny), "pangea-test", "as-shipped", expected,
                    primary_task_enabled=False, primary_phase="intake",
                    isolated_root=intake_isolated, public_bundle=bundle,
                    global_permission=intake_safety["permission"],
                )
                self.assertIn("resolved_overlay_permission_violation", missing_failures)

                changed_allow = deepcopy(resolved_intake)
                changed_allow["permission"][deny_index]["action"] = "allow"
                _, changed_failures = benchmark._resolved_agent_receipt(
                    json.dumps(changed_allow), "pangea-test", "as-shipped", expected,
                    primary_task_enabled=False, primary_phase="intake",
                    isolated_root=intake_isolated, public_bundle=bundle,
                    global_permission=intake_safety["permission"],
                )
                self.assertIn("resolved_overlay_permission_violation", changed_failures)

            order_override = deepcopy(resolved_intake)
            order_override["permission"].append({
                "permission": "task", "pattern": "analysis-worker", "action": "allow",
            })
            self.assertEqual(
                "allow", benchmark._permission_decision(
                    order_override["permission"], "task", "analysis-worker",
                ),
            )
            order_receipt, order_failures = benchmark._resolved_agent_receipt(
                json.dumps(order_override), "pangea-test", "as-shipped", expected,
                primary_task_enabled=False, primary_phase="intake",
                isolated_root=intake_isolated, public_bundle=bundle,
                global_permission=intake_safety["permission"],
            )
            self.assertIn("resolved_overlay_permission_violation", order_failures)
            self.assertIn("resolved_tool_policy_violation", order_failures)
            self.assertIn("task", order_receipt["enabled_tools"])
            self.assertNotEqual(["bash"], order_receipt["enabled_tools"])

            leaf_root = root / "leaf-env"
            leaf_cwd = root / "leaf-cwd"
            leaf_cwd.mkdir()
            leaf_env, _, available, _, execution_agent = benchmark._role_environment(
                "analysis-worker", leaf_root,
                {"PATH": os.environ.get("PATH", ""), "DEEPSEEK_API_KEY": "fake-debug-only-key"},
                leaf_cwd,
            )
            self.assertEqual("analysis-worker", execution_agent)
            self.assertTrue(available)
            leaf_debug = subprocess.run(
                [executable, "debug", "agent", "analysis-worker"], cwd=leaf_cwd,
                env=leaf_env, capture_output=True, text=True, check=False,
                timeout=debug_timeout,
            )
            leaf_receipt, leaf_failures = benchmark._resolved_agent_receipt(
                leaf_debug.stdout, "analysis-worker", "as-shipped", expected,
                worker=True, isolated_root=leaf_root,
            )
            self.assertEqual(0, leaf_debug.returncode)
            self.assertEqual([], leaf_failures)
            self.assertNotIn(str(root), repr(leaf_receipt))

    def test_primary_phase_denies_task_before_model_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            primary_tools = {"bash"}
            responses = [
                Mock(returncode=0, stdout="1.18.4\n", stderr=""),
                Mock(returncode=0, stdout=_debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS), stderr=""),
                Mock(returncode=0, stdout=_debug_config("read", "glob", "grep", name="analysis-worker", mode="subagent"), stderr=""),
                Mock(returncode=0, stdout=_debug_config("read", name="auditor", mode="subagent"), stderr=""),
                Mock(returncode=0, stdout=_debug_config(*primary_tools, safe_overlay=True,
                                                       primary_task_enabled=False, primary_phase="intake"), stderr=""),
                Mock(returncode=0, stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["analysis-worker"], name="analysis-worker", mode="subagent", safe_overlay=True), stderr=""),
                Mock(returncode=0, stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["auditor"], name="auditor", mode="subagent", safe_overlay=True), stderr=""),
                Mock(returncode=0, stdout=_native_stream(
                    text="external handoff ready", tool="bash",
                    tool_input={"command": benchmark.EVALUATOR_INTAKE_COMMAND},
                ), stderr=""),
            ]
            runner = _sequence_runner(responses)
            evaluator = Path(temp) / "evaluator"
            receipt = benchmark.execute_pangea_primary_phase(
                spec, "intake", "initialize and stop", evaluator, run=runner,
                environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "test-provider-value"},
            )
            self.assertEqual(["external_role_execution_required"], receipt.failures)
            self.assertFalse(receipt.passed)
            overlay = json.loads(runner.call_args_list[-1].kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
            self.assertFalse(overlay["agent"]["pangea-test"]["tools"]["task"])
            self.assertEqual({"bash"}, {name for name, enabled in
                                        overlay["agent"]["pangea-test"]["tools"].items() if enabled})
            self.assertEqual(
                {
                    "*": "deny", "analysis-worker": "deny",
                    "auditor": "deny", "mr-reader": "deny",
                },
                overlay["agent"]["pangea-test"]["permission"]["task"],
            )
            self.assertFalse(any(call.args[0][:2] == ["opencode", "run"] for call in runner.call_args_list[:-1]))
            self.assertEqual("public-bundle", receipt.policy_receipt["primary_subprocess_cwd"])
            self.assertEqual("implicit-public-bundle", receipt.telemetry["tool_actions"][0]["target"])
            self.assertEqual(120, receipt.policy_receipt["opencode_debug_timeout_seconds"])
            for call in runner.call_args_list:
                command = call.args[0]
                if command[:2] == ["opencode", "--version"]:
                    self.assertEqual(30, call.kwargs["timeout"])
                elif command[:2] == ["opencode", "debug"]:
                    self.assertEqual(120, call.kwargs["timeout"])
                elif command[:2] == ["opencode", "run"]:
                    self.assertEqual(1800, call.kwargs["timeout"])

    def test_debug_preflight_timeout_uses_frozen_limit_and_never_starts_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            main_commands: list[list[str]] = []

            def main_runner(command, **kwargs):
                main_commands.append(command)
                if command[:2] == ["opencode", "--version"]:
                    self.assertEqual(30, kwargs["timeout"])
                    return Mock(returncode=0, stdout="1.18.4\n", stderr="")
                if command[:3] == ["opencode", "debug", "config"]:
                    self.assertEqual(120, kwargs["timeout"])
                    raise subprocess.TimeoutExpired(command, 120)
                self.fail(f"unexpected command after debug timeout: {command[:2]}")

            main_receipt = benchmark.execute_pangea_primary_phase(
                spec, "intake", "initialize and stop", Path(temp) / "evaluator",
                run=main_runner,
                environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "test-provider-value"},
            )
            self.assertEqual(["plugin_closure_preflight_launch_error"], main_receipt.failures)
            self.assertFalse(any(command[:2] == ["opencode", "run"] for command in main_commands))

            leaf_commands: list[list[str]] = []

            def leaf_runner(command, **kwargs):
                leaf_commands.append(command)
                if command[:2] == ["opencode", "--version"]:
                    self.assertEqual(30, kwargs["timeout"])
                    return Mock(returncode=0, stdout="1.18.4\n", stderr="")
                if command[:3] == ["opencode", "debug", "config"]:
                    self.assertEqual(120, kwargs["timeout"])
                    raise subprocess.TimeoutExpired(command, 120)
                self.fail(f"unexpected command after debug timeout: {command[:2]}")

            leaf_execution = benchmark.execute_isolated_role(
                "analysis-worker", {"CONTEXT.json": {"candidate": "bounded"}},
                run=leaf_runner,
                environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "test-provider-value"},
                scratch_parent=Path(temp),
            )
            self.assertEqual(["plugin_closure_preflight_failed"], leaf_execution.receipt["failures"])
            self.assertFalse(any(command[:2] == ["opencode", "run"] for command in leaf_commands))

    def test_intake_tool_input_exact_closure_accepts_only_implicit_or_dot_cwd(self) -> None:
        track = benchmark._track(benchmark.load_frozen_config(), "as-shipped", "pangea")
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            valid = [
                ({"command": benchmark.EVALUATOR_INTAKE_COMMAND}, "implicit-public-bundle"),
                ({"command": benchmark.EVALUATOR_INTAKE_COMMAND, "workdir": "."}, "explicit-dot"),
                ({"command": benchmark.EVALUATOR_INTAKE_COMMAND, "workdir": str(bundle)}, "explicit-public-bundle"),
            ]
            for tool_input, target in valid:
                telemetry = benchmark.parse_jsonl_telemetry(
                    _native_stream(tool="bash", tool_input=tool_input).splitlines(True),
                    public_bundle=bundle, track=track, primary_phase="intake",
                )
                self.assertEqual([], telemetry["tool_policy_violations"])
                self.assertEqual(1, telemetry["tool_calls"])
                self.assertEqual(["bash"], telemetry["tool_names"])
                self.assertEqual("allow:evaluator-intake-v2", telemetry["tool_actions"][0]["decision"])
                self.assertEqual(target, telemetry["tool_actions"][0]["target"])

            invalid = [
                {"command": benchmark.EVALUATOR_INTAKE_COMMAND, "workdir": ""},
                {"command": benchmark.EVALUATOR_INTAKE_COMMAND, "workdir": None},
                {"command": benchmark.EVALUATOR_INTAKE_COMMAND, "workdir": "subdir"},
                {"command": benchmark.EVALUATOR_INTAKE_COMMAND, "workdir": ".", "extra": True},
                {"command": " " + benchmark.EVALUATOR_INTAKE_COMMAND},
                {"command": benchmark.EVALUATOR_INTAKE_COMMAND + " "},
                {"command": benchmark.EVALUATOR_INTAKE_COMMAND + "\n"},
                {"command": "/usr/bin/python3 runtime/runctl.py evaluator-intake-v2"},
                {"command": "python3 /runtime/runctl.py evaluator-intake-v2"},
                {"command": benchmark.EVALUATOR_INTAKE_COMMAND + " extra"},
            ]
            for tool_input in invalid:
                telemetry = benchmark.parse_jsonl_telemetry(
                    _native_stream(tool="bash", tool_input=tool_input).splitlines(True),
                    public_bundle=bundle, track=track, primary_phase="intake",
                )
                self.assertEqual(1, len(telemetry["tool_policy_violations"]), tool_input)
                self.assertEqual(
                    {"managed_runtime_contract": 1},
                    telemetry["tool_input_policy_violation_summary"]["category_counts"],
                )
                self.assertEqual("deny:intake-runtime-input", telemetry["tool_actions"][0]["decision"])

    def test_intake_permission_denial_converges_only_to_one_final_exact_execution(self) -> None:
        track = benchmark._track(benchmark.load_frozen_config(), "as-shipped", "pangea")
        with tempfile.TemporaryDirectory() as temp:
            bundle=Path(temp)
            denied_one={"command":"ls", "workdir":str(bundle)}
            denied_two={"command":"ls && pwd", "workdir":str(bundle)}
            exact={"command":benchmark.EVALUATOR_INTAKE_COMMAND,"workdir":str(bundle)}
            valid=_intake_convergence_stream(bundle,[
                ("bash","error",denied_one,"permission"),
                ("bash","error",denied_two,"permission"),
                ("bash","completed",exact,None),
            ])
            telemetry=benchmark.parse_jsonl_telemetry(valid.splitlines(True),public_bundle=bundle,
                track=track,primary_phase="intake")
            self.assertEqual([],telemetry["tool_policy_violations"])
            self.assertTrue(all(set(action)=={"tool","action","target","decision","status",
                "permission_error","metadata_present","output_present"} for action in telemetry["tool_actions"]))
            self.assertTrue(all({"input","error","command","workdir","path"}.isdisjoint(action)
                                for action in telemetry["tool_actions"]))
            sanitized_actions=repr(telemetry["tool_actions"])
            for raw in ("ls && pwd",benchmark.EVALUATOR_INTAKE_COMMAND,
                        benchmark._OPENCODE_PERMISSION_ERROR_PREFIX):
                self.assertNotIn(raw,sanitized_actions)
            self.assertEqual({"schema_version":"1.0","attempts":3,"denied_before_success":2,
                "completed_exact":1,"status_sequence":["permission_denied","permission_denied","completed_exact"]},
                telemetry["intake_attempt_summary"])
            self.assertTrue(benchmark.valid_intake_attempt_summary(telemetry["intake_attempt_summary"],converged=True))
            canonical=benchmark._canonical_intake_permission_rules()
            encode=lambda rules,prefix=benchmark._OPENCODE_PERMISSION_ERROR_PREFIX: prefix+" "+json.dumps(
                rules,separators=(",",":"))
            self.assertTrue(benchmark._opencode_permission_error(encode(canonical),bundle))
            mutations=[]
            mutations.extend((canonical[:1],canonical[:-1],[*canonical,dict(canonical[-1])],
                              [canonical[1],canonical[0],*canonical[2:]]))
            for field,bad in (("action","ask"),("pattern","python3 runtime/runctl.py evaluator-intake-v3"),
                              ("permission","read")):
                changed=deepcopy(canonical);changed[2][field]=bad;mutations.append(changed)
            extra=deepcopy(canonical);extra[0]["extra"]="x";mutations.append(extra)
            other_root=deepcopy(canonical);other_root[2]["pattern"]=str(bundle.parent/"other/runtime/runctl.py")
            mutations.append(other_root)
            for index,rules in enumerate(mutations):
                with self.subTest(permission_rules=index):
                    self.assertFalse(benchmark._opencode_permission_error(encode(rules),bundle))
            self.assertFalse(benchmark._opencode_permission_error(
                encode(canonical,prefix=benchmark._OPENCODE_PERMISSION_ERROR_PREFIX+" changed"),bundle))
            canonical_json=json.dumps(canonical,separators=(",",":"))
            duplicate_rule_json=(
                '[{"permission":"*","permission":"bash","pattern":"*","action":"allow"},'
                + ",".join(json.dumps(rule,separators=(",",":")) for rule in canonical[1:])
                + "]"
            )
            lexical_mutations=(
                benchmark._OPENCODE_PERMISSION_ERROR_PREFIX+" \t"+canonical_json,
                encode(canonical)+" ",
                "\n"+encode(canonical),
                encode(canonical)+"\n",
                encode(canonical)+"{}",
                benchmark._OPENCODE_PERMISSION_ERROR_PREFIX+" "+duplicate_rule_json,
            )
            for index,error in enumerate(lexical_mutations):
                with self.subTest(permission_error_lexical=index):
                    self.assertFalse(benchmark._opencode_permission_error(error,bundle))
            cases={
                "completed-invalid":[("bash","completed",denied_one,None),("bash","completed",exact,None)],
                "nonpermission-error":[("bash","error",denied_one,"other failure"),("bash","completed",exact,None)],
                "too-many-prefix":[*([("bash","error",denied_one,"permission")]*3),("bash","completed",exact,None)],
                "after-success":[("bash","completed",exact,None),("bash","error",denied_one,"permission")],
                "multiple-success":[("bash","completed",exact,None),("bash","completed",exact,None)],
                "wrong-tool":[("read","error",denied_one,"permission"),("bash","completed",exact,None)],
                "error-output":[("bash","error",denied_one,"permission_with_output"),("bash","completed",exact,None)],
            }
            for name,attempts in cases.items():
                with self.subTest(name=name):
                    value=benchmark.parse_jsonl_telemetry(_intake_convergence_stream(bundle,attempts).splitlines(True),
                        public_bundle=bundle,track=track,primary_phase="intake")
                    self.assertFalse(benchmark.valid_intake_attempt_summary(value["intake_attempt_summary"],converged=True))
            execution_cases={"valid":[
                ("bash","error",denied_one,"permission"),("bash","error",denied_two,"permission"),
                ("bash","completed",exact,None)],**cases}
        for name,attempts in execution_cases.items():
            with self.subTest(execution=name), tempfile.TemporaryDirectory() as temp:
                spec,_=self._spec(temp,"as-shipped");bundle=spec.public_bundle
                rebound=[]
                for tool,status,tool_input,error in attempts:
                    current=dict(tool_input)
                    if current.get("workdir") is not None: current["workdir"]=str(bundle)
                    rebound.append((tool,status,current,error))
                stream=_intake_convergence_stream(bundle,rebound)
                debug=_debug_config("bash",safe_overlay=True,primary_task_enabled=False,primary_phase="intake")
                receipt=benchmark.execute_pangea_primary_phase(spec,"intake","initialize and stop",Path(temp)/"evaluator",
                    run=self._runner(debug,stream,as_shipped=True),
                    environ={"PATH":"/bin","DEEPSEEK_API_KEY":"test-provider-value"})
                if name=="valid":
                    self.assertEqual(["external_role_execution_required"],receipt.failures)
                    self.assertNotIn("tool_input_policy_violation",receipt.failures)
                    self.assertNotIn("intake_one_shot_violation",receipt.failures)
                else:
                    self.assertIn("intake_one_shot_violation",receipt.failures)

    def test_official_intake_permission_errors_replay_as_one_tokenized_projection(self) -> None:
        database=Path("/private/var/folders/hl/xc6pp3817wj8vg6bzb8gpsfh0000gn/T/"
                      "pangea-official-intake-diag-fnd9qcyw/evaluator/intake/opencode-env/data/opencode/opencode.db")
        if not database.is_file(): self.skipTest("official local intake diagnostic is unavailable")
        connection=sqlite3.connect(f"file:{database}?mode=ro",uri=True)
        try: rows=[json.loads(raw) for (raw,) in connection.execute("select data from part")]
        finally: connection.close()
        tools=[row for row in rows if isinstance(row,dict) and row.get("type")=="tool" and row.get("tool")=="bash"]
        bundle=Path(tools[-1]["state"]["input"]["workdir"]);errors=[row["state"]["error"] for row in tools
            if row.get("state",{}).get("status")=="error"]
        self.assertEqual(2,len(errors));self.assertEqual(errors[0],errors[1])
        self.assertTrue(all(benchmark._opencode_permission_error(error,bundle) for error in errors))

    def test_report_auditor_is_separate_artifact_only_process_with_signed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); run_dir = root / "pangea-data/runs/run-1"
            (run_dir / "internal").mkdir(parents=True)
            report = {"title": "fixed report"}
            (run_dir / "internal/report-model.json").write_text(json.dumps(report))
            artifacts = {
                "TASK_CONTRACT.json": {"contract_id": "r2"},
                "ANALYSIS_MODEL.json": {"artifact": "analysis"},
                "COVERAGE_JUDGE.json": {"verdict": "PASS"},
                "RISK_LEDGER.json": {"risks": []},
                "REPORT_MODEL.json": report,
            }
            passed_check = {"verdict": "PASS", "violations": [], "gaps": []}
            opinion = {
                "artifact_type": "audit_opinion", "schema_version": "2.0",
                "audited_artifact": "internal/report-model.json",
                "audited_sha256": sha256((run_dir / "internal/report-model.json").read_bytes()).hexdigest(),
                "verdict": "PASS",
                "checks": {name: dict(passed_check) for name in
                           ("traceability", "blackbox_executability", "coverage", "format_compliance")},
                "required_actions": [],
            }
            runner = _sequence_runner([
                Mock(returncode=0, stdout="1.18.4\n", stderr=""),
                Mock(returncode=0, stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["auditor"], name="auditor", mode="subagent", safe_overlay=True), stderr=""),
                Mock(returncode=0, stdout=_native_stream(json.dumps(opinion)), stderr=""),
            ])
            execution = benchmark.execute_isolated_role(
                "auditor", artifacts, run=runner,
                environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "test-provider-value"},
                scratch_parent=root,
            )
            opinion_path = benchmark.write_native_report_audit(run_dir, artifacts, execution)
            self.assertEqual(opinion, json.loads(opinion_path.read_text()))
            receipts = list((run_dir / "internal/final-audit-execution-receipts").glob("*.json"))
            self.assertEqual(1, len(receipts))
            receipt_hash, _ = fragment_runtime.verify_execution_attestation(
                json.loads(receipts[0].read_text()), "auditor",
            )
            self.assertEqual(receipts[0].stem, receipt_hash)

    def test_codetalks_run_guard_audit_uses_subcommand_schema_and_scoped_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            allowed = (
                "python3 .opencode/skills/codetalks-source-driven-blackbox-v2/scripts/run_guard.py init "
                "--skill-root .opencode/skills/codetalks-source-driven-blackbox-v2 --workspace codetalks-data "
                "--source-raw repositories/spdk --source-verified repositories/nvme-cli --output codetalks-data/out "
                "--scenario module --mode depth"
            )
            self.assertEqual("allow:codetalks-run-guard", benchmark._audit_bash({"command": allowed}, bundle)[2])
            for command in (
                allowed.replace("--output codetalks-data/out", "--output ../outside"),
                allowed.replace("--mode depth", "--mode quick"),
                allowed + " --extra no",
                "python3 .opencode/skills/codetalks-source-driven-blackbox-v2/scripts/run_guard.py start-step --workspace codetalks-data --step 10",
            ):
                self.assertTrue(benchmark._audit_bash({"command": command}, bundle)[2].startswith("deny:"))

    def _spec(self, temp: str, track: str = "equal-tools") -> tuple[benchmark.RunSpec, Path]:
        root = Path(temp)
        bundle = root / "bundle"
        bundle.mkdir()
        task, case_id, case_hash = bind_canonical_case_fixture(
            bundle, framing="analyse the public corpus",
        )
        policy = root / "policy.json"
        selected = benchmark._track(benchmark.load_frozen_config(), track, "pangea")
        policy.write_text(json.dumps(selected), encoding="utf-8")
        return benchmark.RunSpec(
            "pangea", track, bundle, task, policy, case_id, "CASE.json", case_hash,
        ), policy

    def _runner(self, debug: str, stream: str | BaseException, *, as_shipped: bool = False, mutate=None,
                unsafe_worker: str | None = None, plugin_mutation=None) -> Mock:
        responses: list[object] = [Mock(returncode=0, stdout="1.18.4\n", stderr="")]
        if as_shipped:
            responses.extend([
                Mock(returncode=0, stdout=_debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, name="pangea-test", mode="primary"), stderr=""),
                Mock(returncode=0, stdout=_debug_config("read", name="analysis-worker", mode="subagent"), stderr=""),
                Mock(returncode=0, stdout=_debug_config("read", name="auditor", mode="subagent"), stderr=""),
                Mock(returncode=0, stdout=debug, stderr=""),
                Mock(returncode=0, stdout=_debug_config(*(unsafe_worker.split(",") if unsafe_worker else benchmark.AS_SHIPPED_ROLE_TOOLS["analysis-worker"]), name="analysis-worker", mode="subagent", safe_overlay=True), stderr=""),
                Mock(returncode=0, stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["auditor"], name="auditor", mode="subagent", safe_overlay=True), stderr=""),
            ])
        else:
            responses.append(Mock(returncode=0, stdout=debug, stderr=""))
        final = stream if isinstance(stream, BaseException) else Mock(returncode=0, stdout=stream, stderr="")
        responses.append(final)
        index = 0
        def side_effect(*args, **kwargs):
            nonlocal index
            if args[0][:3] == ["opencode", "debug", "config"]:
                return _resolved_plugin_config_result(kwargs, plugin_mutation)
            response = responses[index]
            index += 1
            if index == len(responses) and mutate is not None:
                mutate()
            if isinstance(response, BaseException):
                raise response
            return response
        return Mock(side_effect=side_effect)

    def test_frozen_conditions_corpus_and_runnable_comparator_adapter(self) -> None:
        frozen = benchmark.load_frozen_config()
        reference = frozen["reference"]
        self.assertEqual("codetalks-fused-v2.4-zh.zip", reference["archive"])
        self.assertEqual("7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325", reference["sha256"])
        preset = reference["verified_preset"]
        self.assertEqual("skill_version_build_cd5236626f824050a1598a845d2b5eba", preset["skill_version"])
        self.assertEqual("sha256:8217e197c006884f845a141b967d498c0a3fa716ccb4dd924fbad11377b0fbfc", preset["content_digest"])
        self.assertEqual("runnable-minimal-adapter", reference["runtime_status"])
        self.assertEqual("codetalks-fused-v2.4", reference["runtime_agent"])
        self.assertEqual(64, len(reference["adapter"]["sha256"]))
        self.assertEqual("deepseek/deepseek-v4-flash", frozen["runtime"]["model"])
        self.assertEqual(200000, frozen["runtime"]["context_window"])
        self.assertEqual(4096, frozen["runtime"]["max_output_tokens"])
        self.assertEqual(120, frozen["runtime"]["opencode_debug_timeout_seconds"])
        shipped = next(item for item in frozen["fair_tracks"] if item["id"] == "as-shipped")
        self.assertTrue({
            "storage-spdk", "storage-nvme-cli", "storage-nvmeof", "storage-iscsi",
            "storage-resource-recovery", "storage-destructive-cli",
        }.issubset(shipped["candidate_policies"]["pangea"]["skill_allowlist"]))
        corpus = benchmark.load_corpus_manifest()
        self.assertTrue(all(item["read_only"] for item in corpus["repositories"]))
        self.assertEqual({"spdk", "nvme-cli"}, {item["id"] for item in corpus["repositories"]})

    def test_fuse_comparator_builds_frozen_adapter_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = root / "TASK.md"
            task.write_text("safe", encoding="utf-8")
            benchmark.write_public_bundle_manifest(root)
            command = benchmark.build_opencode_command(task, root, "fuse", "equal-tools")
            self.assertIn("codetalks-fused-v2.4", command)

    def test_public_bundle_rejects_oracles_private_fields_and_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / "case.json").write_text('{"id": "safe"}', encoding="utf-8")
            benchmark.write_public_bundle_manifest(bundle)
            self.assertEqual([], benchmark.validate_public_bundle(bundle))
            hidden = bundle / "oracles"
            hidden.mkdir()
            (hidden / "case.json").write_text('{"scoring": []}', encoding="utf-8")
            self.assertTrue(any("private path exposed" in error for error in benchmark.validate_public_bundle(bundle)))
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "bundle"
            bundle.mkdir()
            target = Path(temp) / "target"
            target.write_text("safe", encoding="utf-8")
            (bundle / "TASK.md").symlink_to(target)
            with self.assertRaises(benchmark.BenchmarkContractError):
                benchmark.write_public_bundle_manifest(bundle)
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            plugin = bundle / ".opencode/plugins/candidate.js"
            plugin.parent.mkdir(parents=True)
            plugin.write_text("export default async () => ({})\n", encoding="utf-8")
            benchmark.write_public_bundle_manifest(bundle)
            self.assertTrue(any("OpenCode project plugin entry exposed" in error
                                for error in benchmark.validate_public_bundle(bundle)))

    def test_public_bundle_rejects_all_opencode_1184_project_plugin_entries(self) -> None:
        entries = (
            ("opencode.json", '{"plugin":["./candidate-plugin.mjs"]}'),
            ("nested/opencode.jsonc", '{"plugin":["../candidate-plugin.mjs"]}'),
            (".opencode/opencode.json", '{"plugin":["./plugins/candidate.js"]}'),
            ("nested/.opencode/opencode.jsonc", '{"plugin":[]}'),
            (".opencode/plugin/candidate.ts", "export default async () => ({})\n"),
            ("nested/.opencode/plugins/candidate.js", "export default async () => ({})\n"),
            ("nested/.opencode/plugins/candidate/package.json", '{"main":"index.mjs"}'),
        )
        for relative, content in entries:
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as temp:
                bundle = Path(temp)
                path = bundle / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
                if relative.endswith(("opencode.json", "opencode.jsonc")):
                    (bundle / "candidate-plugin.mjs").write_text(
                        "export default async () => ({})\n", encoding="utf-8",
                    )
                benchmark.write_public_bundle_manifest(bundle)
                errors = benchmark.validate_public_bundle(bundle)
                self.assertTrue(any("OpenCode project plugin entry exposed" in error for error in errors), errors)

    def test_opencode_command_is_native_json_and_never_auto(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = root / "TASK.md"
            task.write_text("analyse the public corpus", encoding="utf-8")
            benchmark.write_public_bundle_manifest(root)
            command = benchmark.build_opencode_command(task, root, "pangea", "equal-tools")
            self.assertEqual(["opencode", "run"], command[:2])
            self.assertEqual(str(root), command[command.index("--dir") + 1])
            self.assertEqual("pangea-test", command[command.index("--agent") + 1])
            self.assertEqual("deepseek/deepseek-v4-flash", command[command.index("--model") + 1])
            self.assertNotIn("--pure", command)
            self.assertIn("--format", command)
            self.assertIn("json", command)
            self.assertNotIn("--auto", command)

    def test_candidate_project_plugin_config_is_rejected_before_any_opencode_process(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            (spec.public_bundle / "candidate-plugin.mjs").write_text(
                "export default async () => ({})\n", encoding="utf-8",
            )
            (spec.public_bundle / "opencode.json").write_text(
                '{"plugin":["./candidate-plugin.mjs"]}\n', encoding="utf-8",
            )
            benchmark.write_public_bundle_manifest(spec.public_bundle)
            runner = Mock()
            with self.assertRaisesRegex(benchmark.BenchmarkContractError, "OpenCode project plugin entry"):
                benchmark.execute_opencode(
                    spec, run=runner,
                    environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
                )
            runner.assert_not_called()

    def test_runspec_requires_explicit_case_path_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); bundle = root / "bundle"; bundle.mkdir()
            task = bundle / "TASK.md"; policy = root / "policy.json"
            with self.assertRaises(TypeError):
                benchmark.RunSpec("pangea", "equal-tools", bundle, task, policy, "case")

    def test_runspec_case_binding_rejects_wrong_replacement_symlink_and_mode(self) -> None:
        mutations = ("wrong-hash", "replacement", "symlink", "writable")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                spec, _ = self._spec(temp)
                binding = benchmark._capture_validated_public_bundle_binding(spec.public_bundle)
                case_path = spec.public_bundle / "CASE.json"
                if mutation == "wrong-hash":
                    spec = replace(spec, public_case_sha256="0" * 64)
                elif mutation == "replacement":
                    case_path.chmod(0o644)
                    case_path.write_text('{"id":"replacement"}\n', encoding="utf-8")
                    case_path.chmod(0o444)
                elif mutation == "symlink":
                    case_path.unlink()
                    case_path.symlink_to("TASK.md")
                else:
                    case_path.chmod(0o644)
                with self.assertRaises(benchmark.BenchmarkContractError):
                    benchmark._validate_runspec_case_binding(spec, binding)

    def test_direct_runner_fully_validates_before_constructing_case_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            (spec.public_bundle / "EXTRA").write_text("unexpected\n", encoding="utf-8")
            runner = Mock()
            with self.assertRaisesRegex(benchmark.BenchmarkContractError, "allowlist"):
                benchmark.execute_opencode(spec, run=runner)
            runner.assert_not_called()

    def test_runspec_case_binding_rejects_receipt_task_and_bundle_manifest_drift(self) -> None:
        mutations = ("receipt", "receipt-unknown", "duplicate-agent-input", "bundle-manifest")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                spec, _ = self._spec(temp)
                binding = benchmark._capture_validated_public_bundle_binding(spec.public_bundle)
                if mutation in {"receipt", "receipt-unknown"}:
                    receipt_path = spec.public_bundle / "stage-receipt.json"
                    receipt_path.chmod(0o644)
                    receipt = json.loads(receipt_path.read_text())
                    if mutation == "receipt":
                        receipt["contract_projection_sha256"] = "0" * 64
                    else:
                        receipt["unknown"] = True
                    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
                    benchmark.write_public_bundle_manifest(spec.public_bundle)
                elif mutation == "duplicate-agent-input":
                    case = json.loads((spec.public_bundle / "CASE.json").read_text())
                    spec.task.write_text(case["agent_input"] + "\n" + case["agent_input"], encoding="utf-8")
                    benchmark.write_public_bundle_manifest(spec.public_bundle)
                else:
                    manifest_path = spec.public_bundle / "public-bundle-manifest.json"
                    manifest = json.loads(manifest_path.read_text())
                    manifest["files"]["CASE.json"] = "0" * 64
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(benchmark.BenchmarkContractError):
                    benchmark._validate_runspec_case_binding(spec, binding)

    def test_native_jsonl_positive_receipt_and_environment_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            runner = self._runner(_debug_config("read", "glob", "grep"), _native_stream(tool="read"))
            inherited = {
                "PATH": "/safe/bin",
                "HOME": "/safe/home",
                "DEEPSEEK_API_KEY": "super-secret",
                "OPENCODE_API_KEY": "unrelated-provider-secret",
                "UNRELATED_SECRET": "must-not-pass",
            }
            receipt = benchmark.execute_opencode(spec, run=runner, environ=inherited)
            self.assertTrue(receipt.passed,receipt.failures)
            self.assertEqual(["glob", "grep", "read"], receipt.preflight["enabled_tools"])
            self.assertEqual(1, receipt.telemetry["model_calls"])
            self.assertEqual(1, receipt.telemetry["model_calls_completed"])
            self.assertEqual(1, receipt.telemetry["model_requests_admitted"])
            self.assertFalse(receipt.telemetry["pre_request_budget_enforced"])
            self.assertTrue(receipt.telemetry["injected_test_runner"])
            self.assertEqual(1, receipt.telemetry["tool_calls"])
            self.assertEqual('{"risks": []}', receipt.telemetry["final_text"])
            execution_env = runner.call_args_list[2].kwargs["env"]
            self.assertEqual("/safe/bin", execution_env["PATH"])
            isolated_home = Path(execution_env["HOME"])
            self.assertNotEqual(spec.public_bundle, isolated_home)
            self.assertNotIn(str(spec.public_bundle), str(isolated_home))
            self.assertFalse(isolated_home.exists())
            self.assertFalse(Path(execution_env["XDG_CONFIG_HOME"]).exists())
            self.assertEqual("super-secret", execution_env["DEEPSEEK_API_KEY"])
            self.assertNotIn("OPENCODE_API_KEY", execution_env)
            self.assertNotIn("UNRELATED_SECRET", execution_env)
            self.assertNotIn("super-secret", repr(receipt))
            self.assertIn("DEEPSEEK_API_KEY", receipt.environment_keys)

    def test_normal_resolved_config_requires_exact_tokenized_hook_closure(self) -> None:
        mutations = {
            "extra": lambda plugins: ["file:///candidate-plugin.mjs", *plugins],
            "duplicate": lambda plugins: [*plugins, *plugins],
            "order": lambda plugins: [*plugins, "file:///candidate-plugin.mjs"],
            "unknown": lambda plugins: ["candidate-package"],
        }
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            runner = self._runner(_debug_config("read", "glob", "grep"), _native_stream())
            receipt = benchmark.execute_opencode(
                spec, run=runner,
                environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
            )
            self.assertTrue(receipt.passed, receipt.failures)
            closure = receipt.preflight["plugin_closure"]
            self.assertTrue(closure["exact"])
            self.assertEqual(1, closure["plugin_count"])
            self.assertEqual("file://{ISOLATED_EVALUATOR_ROOT}/model-budget-hook/pre-request-budget.js",
                             closure["plugin_uri"])
            evaluator_root = Path(runner.call_args_list[1].kwargs["env"]["HOME"]).parents[1]
            self.assertNotIn(str(evaluator_root), repr(receipt))
            self.assertNotIn("local-test-provider", repr(receipt))
        for name, mutation in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                spec, _ = self._spec(temp)
                runner = self._runner(
                    _debug_config("read", "glob", "grep"), _native_stream(), plugin_mutation=mutation,
                )
                receipt = benchmark.execute_opencode(
                    spec, run=runner,
                    environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
                )
                self.assertFalse(receipt.passed)
                self.assertEqual(["resolved_plugin_closure_violation"], receipt.failures)
                self.assertFalse(any(call.args[0][:2] == ["opencode", "run"] for call in runner.call_args_list))
                self.assertNotIn("candidate-plugin", repr(receipt))

    def test_main_runner_projects_official_auth_key_only_and_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            home = Path(temp) / "caller-home"
            source = home / ".local/share/opencode/auth.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({
                "deepseek": {"type": "api", "key": "auth-file-secret"},
                "opencode": {"type": "api", "key": "zen-source-secret"},
                "other-provider": {"type": "api", "key": "other-source-secret"},
            }), encoding="utf-8")
            source.chmod(0o600)
            source_before = source.read_bytes()
            delegate = self._runner(_debug_config("read", "glob", "grep"), _native_stream())
            observed: dict[str, object] = {}

            def inspect_projection(*args, **kwargs):
                env = kwargs["env"]
                observed.update(
                    home=Path(env["HOME"]),
                    key=env["DEEPSEEK_API_KEY"],
                    auth_exists=(Path(env["XDG_DATA_HOME"]) / "opencode/auth.json").exists(),
                )
                return delegate(*args, **kwargs)

            receipt = benchmark.execute_opencode(
                spec, run=Mock(side_effect=inspect_projection), environ={"PATH": "/bin", "HOME": str(home)},
            )
            self.assertTrue(receipt.passed, receipt.failures)
            self.assertEqual("auth-file-secret", observed["key"])
            self.assertFalse(observed["auth_exists"])
            self.assertEqual(source_before, source.read_bytes())
            self.assertFalse(Path(observed["home"]).exists())
            self.assertEqual([], list(spec.public_bundle.rglob("auth.json")))
            for secret in ("auth-file-secret", "zen-source-secret", "other-source-secret"):
                self.assertNotIn(secret, repr(receipt))

    def test_main_runner_rejects_zen_only_auth_without_calling_runner(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            data = Path(temp) / "caller-data"
            auth = data / "opencode/auth.json"
            auth.parent.mkdir(parents=True)
            auth.write_text(json.dumps({"opencode": {"type": "api", "key": "zen-only-secret"}}), encoding="utf-8")
            runner = Mock()
            receipt = benchmark.execute_opencode(
                spec, run=runner, environ={"PATH": "/bin", "XDG_DATA_HOME": str(data)},
            )
            runner.assert_not_called()
            self.assertFalse(receipt.passed)
            self.assertIn("provider_unavailable", receipt.failures)
            self.assertNotIn("zen-only-secret", repr(receipt))
            auth.write_text(json.dumps({
                "deepseek": {"type": "api", "key": "deepseek-secret", "refresh": "unexpected-field"},
            }), encoding="utf-8")
            extra_field_runner = Mock()
            extra_field_receipt = benchmark.execute_opencode(
                spec, run=extra_field_runner, environ={"PATH": "/bin", "XDG_DATA_HOME": str(data)},
            )
            extra_field_runner.assert_not_called()
            self.assertIn("provider_unavailable", extra_field_receipt.failures)
            self.assertNotIn("deepseek-secret", repr(extra_field_receipt))

    def test_approved_local_config_projects_key_only_and_rejects_mixed_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            source = Path(temp) / "caller-home/.config/opencode/opencode.json"
            source.parent.mkdir(parents=True)
            config = {
                "$schema": "https://opencode.ai/config.json",
                "model": benchmark.DEEPSEEK_MODEL,
                "provider": {"deepseek": {
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": "{env:DEEPSEEK_BASE_URL}", "apiKey": "local-config-secret"},
                    "models": {"deepseek-v4-flash": {"name": "DeepSeek V4 Flash",
                               "limit": {"context": 200000, "output": 4096}}},
                }},
            }
            source.write_text(json.dumps(config), encoding="utf-8")
            source.chmod(0o600)
            before = source.read_bytes()
            delegate = self._runner(_debug_config("read", "glob", "grep"), _native_stream())
            observed: dict[str, object] = {}
            def inspect(*args, **kwargs):
                env = kwargs["env"]
                overlay = json.loads(env["OPENCODE_CONFIG_CONTENT"])
                observed.update(key=env["DEEPSEEK_API_KEY"], overlay=overlay,
                                auth_exists=(Path(env["XDG_DATA_HOME"]) / "opencode/auth.json").exists(),
                                home=Path(env["HOME"]))
                return delegate(*args, **kwargs)
            receipt = benchmark.execute_opencode(spec, run=Mock(side_effect=inspect),
                                                 environ={"PATH": "/bin", "HOME": str(Path(temp) / "caller-home")})
            self.assertTrue(receipt.passed, receipt.failures)
            self.assertEqual("local-config-secret", observed["key"])
            self.assertFalse(observed["auth_exists"])
            overlay = observed["overlay"]
            self.assertEqual(benchmark.DEEPSEEK_MODEL, overlay["model"])
            self.assertEqual("https://api.deepseek.com", overlay["provider"]["deepseek"]["options"]["baseURL"])
            self.assertEqual("{env:DEEPSEEK_API_KEY}", overlay["provider"]["deepseek"]["options"]["apiKey"])
            self.assertNotIn("local-config-secret", repr(overlay))
            self.assertNotIn("local-config-secret", repr(receipt))
            self.assertEqual(before, source.read_bytes())
            self.assertFalse(Path(observed["home"]).exists())

            config["provider"]["other"] = {"options": {"apiKey": "mixed-secret"}}
            source.write_text(json.dumps(config), encoding="utf-8")
            rejected = Mock()
            failed = benchmark.execute_opencode(spec, run=rejected,
                                                environ={"PATH": "/bin", "HOME": str(Path(temp) / "caller-home")})
            rejected.assert_not_called()
            self.assertFalse(failed.passed)
            self.assertIn("provider_unavailable", failed.failures)
            self.assertNotIn("local-config-secret", repr(failed))

    def test_real_approved_local_config_readiness_projects_no_secret_to_overlay(self) -> None:
        source = Path("/Users/shepard/.config/opencode/opencode.json")
        if not source.is_file() or source.is_symlink():
            self.skipTest("approved local OpenCode config is unavailable")
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            source_before = source.read_bytes()
            # The real config intentionally uses the approved env placeholder;
            # use a test-only value so the runner path remains credential-ready
            # without ever reading or printing a real credential.
            inherited = {"PATH": "/bin", benchmark.DEEPSEEK_CONFIG_SOURCE_ENV: str(source),
                         "DEEPSEEK_API_KEY": "readiness-test-key",
                         "OPENCODE_DISABLE_MODELS_FETCH": "0"}
            self.assertTrue(benchmark.deepseek_local_config_ready(inherited, spec.public_bundle))
            delegate = self._runner(_debug_config("read", "glob", "grep"), _native_stream())
            observed: dict[str, object] = {}
            def inspect(*args, **kwargs):
                env = kwargs["env"]
                overlay = json.loads(env["OPENCODE_CONFIG_CONTENT"])
                observed.update(key_present=bool(env.get("DEEPSEEK_API_KEY")), overlay=overlay,
                                base_url=env["DEEPSEEK_BASE_URL"], home=Path(env["HOME"]),
                                models_fetch=env["OPENCODE_DISABLE_MODELS_FETCH"])
                return delegate(*args, **kwargs)
            receipt = benchmark.execute_opencode(spec, run=Mock(side_effect=inspect), environ=inherited)
            self.assertTrue(receipt.passed, receipt.failures)
            self.assertTrue(observed["key_present"])
            self.assertEqual(benchmark.DEEPSEEK_MODEL, observed["overlay"]["model"])
            self.assertEqual("https://api.deepseek.com", observed["base_url"])
            self.assertEqual("1", observed["models_fetch"])
            self.assertTrue(receipt.policy_receipt["models_metadata_fetch_disabled"])
            self.assertEqual("https://api.deepseek.com", observed["overlay"]["provider"]["deepseek"]["options"]["baseURL"])
            self.assertEqual("{env:DEEPSEEK_API_KEY}", observed["overlay"]["provider"]["deepseek"]["options"]["apiKey"])
            self.assertNotIn("apiKey", repr(receipt))
            self.assertTrue(source.read_bytes() == source_before)
            self.assertFalse(Path(observed["home"]).exists())

    def test_main_runner_cleans_evaluator_environment_on_unexpected_exception(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            observed: dict[str, Path] = {}

            def fail(*args, **kwargs):
                observed["home"] = Path(kwargs["env"]["HOME"])
                raise RuntimeError("local runner failure")

            with self.assertRaisesRegex(RuntimeError, "local runner failure"):
                benchmark.execute_opencode(
                    spec, run=Mock(side_effect=fail),
                    environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "local-test-provider"},
                )
            self.assertFalse(observed["home"].exists())

    def test_main_runner_cleans_evaluator_environment_on_early_return(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            observed: dict[str, Path] = {}

            def wrong_version(*args, **kwargs):
                observed["home"] = Path(kwargs["env"]["HOME"])
                return Mock(returncode=0, stdout="0.0.0\n", stderr="")

            receipt = benchmark.execute_opencode(
                spec, run=Mock(side_effect=wrong_version),
                environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "local-test-provider"},
            )
            self.assertIn("opencode_version_mismatch", receipt.failures)
            self.assertFalse(observed["home"].exists())

    def test_main_runner_rejects_non_regular_auth_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            data = Path(temp) / "caller-data"
            auth = data / "opencode/auth.json"
            auth.parent.mkdir(parents=True)
            target = Path(temp) / "auth-target.json"
            target.write_text(json.dumps({"deepseek": {"type": "api", "key": "linked-secret"}}), encoding="utf-8")
            auth.symlink_to(target)
            runner = Mock()
            receipt = benchmark.execute_opencode(
                spec, run=runner, environ={"PATH": "/bin", "XDG_DATA_HOME": str(data)},
            )
            runner.assert_not_called()
            self.assertIn("provider_unavailable", receipt.failures)
            self.assertNotIn("linked-secret", repr(receipt))

    def test_evaluator_writes_only_native_bound_runner_telemetry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            run=Path(temp)/"pangea-data/runs/run"; (run/"internal/fragments").mkdir(parents=True); (run/"internal/context-packs/frag-1").mkdir(parents=True); (run/"tmp").mkdir()
            pack={"run_id":"run","fragment_id":"frag-1","obligation_ids":["OBL-"+"1"*16],"skill_receipts":[]}
            fragment={"artifact_type":"analysis_fragment","schema_version":"2.0","worker_instance":"analysis-worker",
                      "run_id":"run","fragment_id":"frag-1","context_pack_sha256":benchmark._canonical_hash(pack),
                      "obligation_ids":pack["obligation_ids"],"skill_receipt_ids":[]}
            path=run/"internal/fragments/frag-1.json"; path.write_text(json.dumps({"payload":fragment}))
            candidate={"context_pack":pack}; context={"payload":{"candidate":candidate,"candidate_sha256":benchmark._canonical_hash(candidate)}}
            context_path=run/"internal/context-packs/frag-1/CONTEXT.json"; context_path.write_text(json.dumps(context))
            stream=_native_stream(text=json.dumps(fragment)); runner=_sequence_runner([Mock(returncode=0,stdout="1.18.4\n",stderr=""),
                Mock(returncode=0,stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["analysis-worker"],name="analysis-worker",mode="subagent",safe_overlay=True),stderr=""),
                Mock(returncode=0,stdout=stream,stderr="")])
            execution=benchmark.execute_isolated_role("analysis-worker",{"CONTEXT.json":context},run=runner,
                environ={"PATH":"/bin","DEEPSEEK_API_KEY":"local-test-provider"},scratch_parent=Path(temp))
            written=benchmark.write_native_runner_telemetry(run,path,context_path,execution)
            receipt=json.loads(written.read_text()); self.assertEqual("opencode-runner",receipt["captured_by"])
            self.assertEqual(100,receipt["input_tokens"]); self.assertEqual(20,receipt["output_tokens"]); self.assertEqual("ses_test",receipt["session_id"])
            with self.assertRaisesRegex(benchmark.BenchmarkContractError,"trusted analysis-worker"):
                benchmark.write_native_runner_telemetry(_native_stream(),run,path,"a"*64)

    def test_as_shipped_preserves_capability_under_bound_safety_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            runner = self._runner(
                _debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, safe_overlay=True), _native_stream(), as_shipped=True,
            )
            receipt = benchmark.execute_opencode(spec, run=runner, environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertFalse(receipt.passed); self.assertIn("external_role_execution_required",receipt.failures)
            self.assertEqual("candidate-aware-safety-overlay", receipt.policy_receipt["policy_mode"])
            self.assertIsNotNone(receipt.policy_receipt["config_override_sha256"])
            self.assertEqual({"analysis-worker", "auditor"}, set(receipt.preflight["workers"]))
            self.assertIn("OPENCODE_CONFIG_CONTENT", runner.call_args_list[-1].kwargs["env"])

    def test_as_shipped_roles_have_distinct_non_recursive_leaf_overlays(self) -> None:
        overlay=benchmark._as_shipped_safety_overlay("pangea-test",["analysis-worker","auditor","mr-reader"])
        agents=overlay["agent"]
        self.assertEqual("deny", overlay["permission"]["external_directory"])
        self.assertEqual(benchmark.AS_SHIPPED_SAFE_TOOLS,{name for name,value in agents["pangea-test"]["tools"].items() if value})
        for role in ("analysis-worker","auditor"):
            enabled={name for name,value in agents[role]["tools"].items() if value}
            self.assertEqual(set(benchmark.AS_SHIPPED_ROLE_TOOLS[role]),enabled)
            for permission in ("task","bash","skill"):
                rule=agents[role]["permission"][permission]
                self.assertEqual("deny",rule if isinstance(rule,str) else rule["*"])
            self.assertEqual("deny", agents[role]["permission"]["external_directory"])
        self.assertEqual({"read"},{name for name,value in agents["auditor"]["tools"].items() if value})
        self.assertEqual("allow",agents["mr-reader"]["permission"]["bash"]["git status*"])
        self.assertEqual("deny",agents["mr-reader"]["permission"]["bash"]["*"])

    def test_isolated_auditor_execution_receipt_binds_output_session_cwd_and_artifacts(self) -> None:
        claim={"contribution_id":"C-"+"1"*16,"fact_keys":[["OBL-"+"2"*16,"INV-"+"3"*16,1,1]],"summary":"claim"}
        facts=[{"obligation_id":"OBL-"+"2"*16,"inventory_id":"INV-"+"3"*16,"path":"x.c","line_start":1,"line_count":1,"excerpt_sha256":"4"*64,"evidence":"source"}]
        stream=_native_stream(text=json.dumps({"supported":True,"reason":"exact fact supports this claim"}))
        with tempfile.TemporaryDirectory() as temp:
            runner=_sequence_runner([Mock(returncode=0,stdout="1.18.4\n",stderr=""),
                Mock(returncode=0,stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["auditor"],name="auditor",mode="subagent",safe_overlay=True),stderr=""),
                Mock(returncode=0,stdout=stream,stderr="")])
            execution=benchmark.execute_isolated_role("auditor",{"CLAIM.json":claim,"FACTS.json":facts},run=runner,
                environ={"PATH":"/bin","DEEPSEEK_API_KEY":"local-test-provider"},scratch_parent=Path(temp))
            receipt=dict(execution.receipt); self.assertTrue(receipt["passed"],receipt["failures"])
            self.assertEqual("auditor",receipt["agent"]); self.assertEqual("ses_test",receipt["session_id"])
            self.assertEqual(sha256(stream.encode()).hexdigest(),receipt["stdout_sha256"])
            cwd=runner.call_args_list[-1].kwargs["cwd"]
            self.assertFalse(cwd.exists()); self.assertEqual({"CLAIM.json","FACTS.json"},{row["name"] for row in receipt["artifact_bindings"]})
            self.assertEqual([],list(Path(temp).glob("pangea-role-*")))
            env=runner.call_args_list[-1].kwargs["env"]
            self.assertNotEqual(str(cwd),env["HOME"]); self.assertNotEqual(env["HOME"],env["XDG_CONFIG_HOME"])
            run_dir=Path(temp)/"run"; (run_dir/"internal").mkdir(parents=True)
            written=benchmark.write_native_semantic_assessment(run_dir,claim,facts,execution)
            semantic=json.loads(written.read_text()); self.assertEqual("ses_test",semantic["auditor_telemetry"]["session_id"])
            self.assertRegex(semantic["auditor_telemetry"]["execution_receipt_sha256"],r"^[a-f0-9]{64}$")

    def test_analysis_worker_uses_independent_process_and_context_only_cwd(self) -> None:
        stream=_native_stream(text=json.dumps({"fragment":"complete"}))
        with tempfile.TemporaryDirectory() as temp:
            runner=_sequence_runner([Mock(returncode=0,stdout="1.18.4\n",stderr=""),
                Mock(returncode=0,stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["analysis-worker"],name="analysis-worker",mode="subagent",safe_overlay=True),stderr=""),
                Mock(returncode=0,stdout=stream,stderr="")])
            execution=benchmark.execute_isolated_role("analysis-worker",{"CONTEXT.json":{"candidate":"bounded"}},run=runner,
                environ={"PATH":"/bin","DEEPSEEK_API_KEY":"local-test-provider"},scratch_parent=Path(temp))
            self.assertTrue(execution.receipt["passed"],execution.receipt["failures"])
            self.assertEqual("analysis-worker",execution.receipt["agent"])
            cwd=runner.call_args_list[-1].kwargs["cwd"]; self.assertFalse(cwd.exists()); self.assertEqual(["CONTEXT.json"],[row["name"] for row in execution.receipt["artifact_bindings"]])
            self.assertEqual([],list(Path(temp).glob("pangea-role-*")))
            command=runner.call_args_list[-1].args[0]; self.assertEqual("analysis-worker",command[command.index("--agent")+1])
            for call in runner.call_args_list:
                command = call.args[0]
                if command[:2] == ["opencode", "--version"]:
                    self.assertEqual(30, call.kwargs["timeout"])
                elif command[:2] == ["opencode", "debug"]:
                    self.assertEqual(120, call.kwargs["timeout"])
                elif command[:2] == ["opencode", "run"]:
                    self.assertEqual(1800, call.kwargs["timeout"])

    def test_compact_leaf_uses_primary_execution_alias_without_default_fallback(self) -> None:
        native={"v":1,"i":[[0,"anchored evidence"]],"a":[[0,"A","action result"]],"c":[]}
        stream=_native_stream(text=json.dumps(native,separators=(",",":")))
        with tempfile.TemporaryDirectory() as temp:
            runner=_sequence_runner([
                Mock(returncode=0,stdout="1.18.4\n",stderr=""),
                Mock(returncode=0,stdout=_debug_config(name="analysis-leaf",mode="primary",tool_free=True),stderr=""),
                Mock(returncode=0,stdout=stream,stderr=""),
            ])
            execution=benchmark.execute_isolated_role(
                "analysis-worker",{"COMPACT_CONTEXT.json":{"v":1,"f":"frag","s":[],"k":[],"i":[],"q":{}}},
                run=runner,environ={"PATH":"/bin","DEEPSEEK_API_KEY":"local-test-provider"},
                scratch_parent=Path(temp),model_call_limit=1,
            )
            receipt=dict(execution.receipt)
            self.assertTrue(receipt["passed"],receipt["failures"])
            self.assertEqual("analysis-worker",receipt["logical_role"])
            self.assertEqual("analysis-leaf",receipt["execution_agent"])
            run_call=next(call for call in runner.call_args_list if call.args[0][:2]==["opencode","run"])
            command=run_call.args[0]
            self.assertEqual("analysis-leaf",command[command.index("--agent")+1])
            overlay=json.loads(run_call.kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
            self.assertEqual({"analysis-leaf"},set(overlay["agent"]))
            self.assertEqual("primary",overlay["agent"]["analysis-leaf"]["mode"])
            self.assertFalse(any(overlay["agent"]["analysis-leaf"]["tools"].values()))

    def test_compact_auditor_batch_signed_projection_and_replay_mutation(self) -> None:
        from evaluation import composer
        fact={"obligation_id":"OBL-"+"2"*16,"inventory_id":"INV-"+"3"*16,"path":"x.c",
              "line_start":1,"line_count":1,"excerpt_sha256":"4"*64,"evidence":"anchored evidence"}
        claim={"contribution_id":"C-"+"1"*16,"priority":"P0","obligation_id":fact["obligation_id"],
               "fact_keys":[[fact["obligation_id"],fact["inventory_id"],1,1]],"summary":"semantic claim",
               "controls":["bounded control"],"oracles":["bounded oracle"]}
        batch={"v":1,"claims":[{"ordinal":0,"claim":claim,"facts":[fact]}]}
        native={"v":1,"a":[[0,True,"exact fact supports claim"]]}
        stream=_native_stream(text=json.dumps(native,separators=(",",":")))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);run_dir=root/"run";(run_dir/"internal").mkdir(parents=True)
            runner=_sequence_runner([
                Mock(returncode=0,stdout="1.18.4\n",stderr=""),
                Mock(returncode=0,stdout=_debug_config(name="audit-leaf",mode="primary",tool_free=True),stderr=""),
                Mock(returncode=0,stdout=stream,stderr=""),
            ])
            execution=benchmark.execute_isolated_role(
                "auditor",{"SEMANTIC_BATCH.json":batch},run=runner,
                environ={"PATH":"/bin","DEEPSEEK_API_KEY":"local-test-provider"},
                scratch_parent=root,model_call_limit=1,
            )
            self.assertTrue(execution.receipt["passed"],execution.receipt["failures"])
            self.assertEqual("audit-leaf",execution.receipt["execution_agent"])
            paths=benchmark.write_native_semantic_assessment_batch(run_dir,batch,execution)
            claims={claim["contribution_id"]:(claim,[fact])}
            self.assertEqual(1,len(composer._semantic_closure(run_dir,claims)))
            assessment=json.loads(paths[0].read_text());paths[0].chmod(0o600)
            assessment["reason"]="different supported reason";paths[0].write_text(json.dumps(assessment))
            paths[0].chmod(0o400)
            with self.assertRaisesRegex(composer.ComposerError,"batch replay"):
                composer._semantic_closure(run_dir,claims)

    def test_isolated_role_projects_official_auth_key_only_and_cleans_it(self) -> None:
        stream = _native_stream(text=json.dumps({"fragment": "complete"}))
        with tempfile.TemporaryDirectory() as temp:
            parent = Path(temp)
            data = parent / "caller-data"
            source = data / "opencode/auth.json"
            source.parent.mkdir(parents=True)
            source.write_text(json.dumps({
                "deepseek": {"type": "api", "key": "leaf-auth-secret"},
                "opencode": {"type": "api", "key": "leaf-zen-secret"},
            }), encoding="utf-8")
            source.chmod(0o600)
            source_before = source.read_bytes()
            responses = iter([
                Mock(returncode=0, stdout="1.18.4\n", stderr=""),
                Mock(returncode=0, stdout=_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["analysis-worker"],
                                                       name="analysis-worker", mode="subagent", safe_overlay=True), stderr=""),
                Mock(returncode=0, stdout=stream, stderr=""),
            ])
            observed: dict[str, object] = {}

            def inspect_projection(*args, **kwargs):
                cwd = Path(kwargs["cwd"])
                env = kwargs["env"]
                if args[0][:3] == ["opencode", "debug", "config"]:
                    return _resolved_plugin_config_result(kwargs)
                observed.update(cwd=cwd, key=env["DEEPSEEK_API_KEY"],
                                auth_exists=(Path(env["XDG_DATA_HOME"]) / "opencode/auth.json").exists(),
                                overlay=json.loads(env["OPENCODE_CONFIG_CONTENT"]),
                                base_url=env["DEEPSEEK_BASE_URL"],
                                models_fetch=env["OPENCODE_DISABLE_MODELS_FETCH"])
                return next(responses)

            execution = benchmark.execute_isolated_role(
                "analysis-worker", {"CONTEXT.json": {"candidate": "bounded"}},
                run=Mock(side_effect=inspect_projection),
                environ={"PATH": "/bin", "XDG_DATA_HOME": str(data),
                         "OPENCODE_DISABLE_MODELS_FETCH": "0"}, scratch_parent=parent,
            )
            self.assertTrue(execution.receipt["passed"], execution.receipt["failures"])
            self.assertEqual("deepseek/deepseek-v4-flash", execution.receipt["model"])
            self.assertEqual("leaf-auth-secret", observed["key"])
            self.assertFalse(observed["auth_exists"])
            self.assertEqual(benchmark.DEEPSEEK_MODEL, observed["overlay"]["model"])
            self.assertEqual("https://api.deepseek.com", observed["overlay"]["provider"]["deepseek"]["options"]["baseURL"])
            self.assertEqual("{env:DEEPSEEK_API_KEY}", observed["overlay"]["provider"]["deepseek"]["options"]["apiKey"])
            self.assertNotIn("leaf-auth-secret", repr(observed["overlay"]))
            self.assertEqual("https://api.deepseek.com", observed["base_url"])
            self.assertEqual("1", observed["models_fetch"])
            self.assertEqual(source_before, source.read_bytes())
            self.assertFalse(Path(observed["cwd"]).exists())
            for secret in ("leaf-auth-secret", "leaf-zen-secret"):
                self.assertNotIn(secret, repr(execution.receipt))

    def test_isolated_role_provider_and_semantic_receipt_fail_closed(self) -> None:
        claim={"contribution_id":"C-"+"1"*16,"fact_keys":[["OBL-"+"2"*16,"INV-"+"3"*16,1,1]],"summary":"claim"}
        facts=[{"obligation_id":"OBL-"+"2"*16,"inventory_id":"INV-"+"3"*16,"path":"x.c","line_start":1,"line_count":1,"excerpt_sha256":"4"*64,"evidence":"source"}]
        with tempfile.TemporaryDirectory() as temp:
            runner=Mock(); failed=benchmark.execute_isolated_role("auditor",{"CLAIM.json":claim,"FACTS.json":facts},run=runner,environ={"PATH":"/bin"},scratch_parent=Path(temp))
            self.assertFalse(failed.receipt["passed"]); self.assertIn("provider_unavailable",failed.receipt["failures"]); runner.assert_not_called()
            self.assertEqual([],list(Path(temp).glob("pangea-role-*")))
            with self.assertRaisesRegex(benchmark.BenchmarkContractError,"not trustworthy"):
                benchmark.write_native_semantic_assessment(Path(temp),claim,facts,failed)
        with self.assertRaisesRegex(benchmark.BenchmarkContractError,"trusted auditor"):
            benchmark.write_native_semantic_assessment(Path("."),claim,facts,{})

    def test_isolated_role_rejects_extra_resolved_permission_and_cleans_exception_paths(self) -> None:
        claim={"contribution_id":"C-"+"1"*16,"fact_keys":[["OBL-"+"2"*16,"INV-"+"3"*16,1,1]],"summary":"claim"}
        facts=[{"obligation_id":"OBL-"+"2"*16,"inventory_id":"INV-"+"3"*16,"path":"x.c","line_start":1,"line_count":1,"excerpt_sha256":"4"*64,"evidence":"source"}]
        debug=json.loads(_debug_config(*benchmark.AS_SHIPPED_ROLE_TOOLS["auditor"],name="auditor",mode="subagent",safe_overlay=True))
        debug["permission"].append({"permission":"external_directory","pattern":"*","action":"allow"})
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp); runner=_sequence_runner([Mock(returncode=0,stdout="1.18.4\n",stderr=""),Mock(returncode=0,stdout=json.dumps(debug),stderr="")])
            execution=benchmark.execute_isolated_role("auditor",{"CLAIM.json":claim,"FACTS.json":facts},run=runner,
                environ={"PATH":"/bin","DEEPSEEK_API_KEY":"local-test-provider"},scratch_parent=parent)
            self.assertFalse(execution.receipt["passed"]); self.assertIn("agent_preflight_failed",execution.receipt["failures"])
            self.assertEqual([],list(parent.glob("pangea-role-*")))
        with tempfile.TemporaryDirectory() as temp:
            parent=Path(temp)
            with self.assertRaises(TypeError):
                benchmark.execute_isolated_role("auditor",{"CLAIM.json":{"bad":{1}},"FACTS.json":facts},scratch_parent=parent)
            self.assertEqual([],list(parent.glob("pangea-role-*")))
            observed: dict[str, Path] = {}
            def boom(*args, **kwargs):
                observed["home"] = Path(kwargs["env"]["HOME"])
                raise RuntimeError("boom")
            with self.assertRaises(RuntimeError):
                benchmark.execute_isolated_role("auditor",{"CLAIM.json":claim,"FACTS.json":facts},run=Mock(side_effect=boom),
                    environ={"PATH":"/bin","DEEPSEEK_API_KEY":"local-test-provider"},scratch_parent=parent)
            self.assertEqual([],list(parent.glob("pangea-role-*")))
            self.assertFalse(observed["home"].exists())

    def test_equal_and_as_shipped_policies_and_receipts_are_distinct(self) -> None:
        tracks = benchmark.load_frozen_config()["fair_tracks"]
        self.assertNotEqual(tracks[0]["policy_mode"], tracks[1]["policy_mode"])
        self.assertNotEqual(set(tracks[0]), set(tracks[1]))
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            equal, _ = self._spec(first)
            shipped, _ = self._spec(second, "as-shipped")
            a = benchmark.execute_opencode(equal, run=self._runner(_debug_config("read", "glob", "grep"), _native_stream()), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            b = benchmark.execute_opencode(
                shipped,
                run=self._runner(_debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, safe_overlay=True), _native_stream(), as_shipped=True),
                environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
            )
            self.assertNotEqual(a.policy_receipt["policy_sha256"], b.policy_receipt["policy_sha256"])
            self.assertNotEqual(a.policy_receipt["enabled_tools"], b.policy_receipt["enabled_tools"])

    def test_as_shipped_allows_managed_runctl_and_frozen_task_without_leaking_input(self) -> None:
        safe_debug = _debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, safe_overlay=True)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            secret = "provider-secret-must-not-appear"
            stream = _native_stream(tool="bash", tool_input={"command": f"python3 runtime/runctl.py status --token {secret}", "workdir": "."})
            receipt = benchmark.execute_opencode(spec, run=self._runner(safe_debug, stream, as_shipped=True), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertFalse(receipt.passed); self.assertIn("external_role_execution_required",receipt.failures)
            self.assertNotIn("same_process_leaf_task_forbidden",receipt.failures)
            self.assertEqual("python-runctl", receipt.telemetry["tool_actions"][0]["action"])
            self.assertNotIn(secret, repr(receipt))
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            stream = _native_stream(tool="task", tool_input={"subagent_type": "analysis-worker", "prompt": "inspect"})
            receipt = benchmark.execute_opencode(spec, run=self._runner(safe_debug, stream, as_shipped=True), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertFalse(receipt.passed); self.assertIn("external_role_execution_required",receipt.failures)
            self.assertIn("same_process_leaf_task_forbidden",receipt.failures)
            self.assertEqual("analysis-worker", receipt.telemetry["tool_actions"][0]["target"])

    def test_as_shipped_tool_inputs_fail_closed_for_dangerous_or_unknown_actions(self) -> None:
        track = benchmark.load_frozen_config()["fair_tracks"][1]
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp)
            (bundle / "TASK.md").write_text("safe", encoding="utf-8")
            cases = [
                ("bash", {"command": "curl https://example.test", "workdir": "."}),
                ("bash", {"command": "git reset --hard", "workdir": "."}),
                ("bash", {"command": "rg needle . > result", "workdir": "."}),
                ("bash", {"command": "rg $SECRET .", "workdir": "."}),
                ("bash", {"command": "rg needle ~/src", "workdir": "."}),
                ("bash", {"command": "python3 -c 'print(1)'", "workdir": "."}),
                ("bash", {"command": "rg needle .", "workdir": "../escape"}),
                ("task", {"subagent_type": "unknown-worker", "prompt": "x"}),
                ("read", {"filePath": "../secret"}),
                ("skill", {"name": "unknown-skill"}),
                ("webfetch", {"url": "https://example.test"}),
                ("task", None),
            ]
            for tool, tool_input in cases:
                telemetry = benchmark.parse_jsonl_telemetry(
                    _native_stream(tool=tool, tool_input=tool_input).splitlines(True), public_bundle=bundle, track=track,
                )
                self.assertTrue(telemetry["tool_policy_violations"], (tool, tool_input))

    def test_overlay_permission_prevents_shell_composition_before_event_audit(self) -> None:
        rules: list[dict[str, str]] = []
        for permission, value in benchmark._as_shipped_safety_overlay("pangea-test", ["analysis-worker", "auditor"])["permission"].items():
            if isinstance(value, str):
                rules.append({"permission": permission, "pattern": "*", "action": value})
            else:
                rules.extend({"permission": permission, "pattern": pattern, "action": action} for pattern, action in value.items())
        self.assertEqual("allow", benchmark._permission_decision(rules, "bash", "python3 runtime/runctl.py status"))
        dangerous = [
            "rg x .; rm x", "rg x . && curl bad", "rg x . || wget bad", "rg x . | nc host 1",
            "rg x . > out", "rg x . < in", "rg `evil` .", "rg $SECRET .", "rg x ~/src",
            "rg x ../src", "python3 runtime/runctl.py --root=/outside", "ssh host",
        ]
        for command in dangerous:
            self.assertEqual("deny", benchmark._permission_decision(rules, "bash", command), command)

    def test_worker_overlay_mismatch_fails_even_when_primary_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            runner = self._runner(
                _debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, safe_overlay=True),
                _native_stream(), as_shipped=True, unsafe_worker="read,glob,grep,skill,task,bash,webfetch",
            )
            receipt = benchmark.execute_opencode(spec, run=runner, environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertFalse(receipt.passed)
            self.assertIn("resolved_tool_policy_violation", receipt.failures)

    def test_current_topology_accepts_the_generic_analysis_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            receipt = benchmark.execute_opencode(
                spec,
                run=self._runner(_debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, safe_overlay=True),
                                 _native_stream(), as_shipped=True),
                environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
            )
            self.assertFalse(receipt.passed); self.assertIn("external_role_execution_required",receipt.failures)
            self.assertEqual({"analysis-worker", "auditor"}, set(receipt.preflight["workers"]))

    def test_bundle_integrity_allows_managed_writes_and_rejects_other_mutations(self) -> None:
        safe_debug = _debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, safe_overlay=True)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            def managed_write():
                managed = spec.public_bundle / "pangea-data"
                managed.mkdir()
                (managed / "result.json").write_text("{}", encoding="utf-8")
            receipt = benchmark.execute_opencode(spec, run=self._runner(safe_debug, _native_stream(), as_shipped=True, mutate=managed_write), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertFalse(receipt.passed); self.assertIn("external_role_execution_required",receipt.failures)
            self.assertEqual(1, receipt.policy_receipt["bundle_integrity"]["managed_file_delta_count"])
        mutations = (
            ("protected_bundle_file_changed_or_deleted", lambda spec: spec.task.write_text("changed", encoding="utf-8")),
            ("out_of_scope_bundle_file_added", lambda spec: (spec.public_bundle / "escape.txt").write_text("x", encoding="utf-8")),
            ("bundle_symlink_or_special_file", lambda spec: (spec.public_bundle / "pangea-data-link").symlink_to(spec.task)),
        )
        for expected_failure, operation in mutations:
            with tempfile.TemporaryDirectory() as temp:
                spec, _ = self._spec(temp, "as-shipped")
                receipt = benchmark.execute_opencode(
                    spec,
                    run=self._runner(safe_debug, _native_stream(), as_shipped=True, mutate=lambda spec=spec, operation=operation: operation(spec)),
                    environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
                )
                self.assertIn(expected_failure, receipt.failures)

    def test_process_oserror_returns_failure_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            runner = self._runner(_debug_config("read", "glob", "grep"), OSError("secret path must not be copied"))
            receipt = benchmark.execute_opencode(spec, run=runner, environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertFalse(receipt.passed)
            self.assertIn("process_launch_error", receipt.failures)
            self.assertNotIn("secret path", repr(receipt))

    def test_resolved_tool_pollution_and_network_event_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            receipt = benchmark.execute_opencode(
                spec,
                run=self._runner(_debug_config("read", "glob", "grep", "bash"), _native_stream()),
                environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
            )
            self.assertIn("resolved_tool_policy_violation", receipt.failures)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            polluted = json.loads(_debug_config("read", "glob", "grep"))
            polluted["permission"].append({"permission": "external_directory", "pattern": "*", "action": "allow"})
            receipt = benchmark.execute_opencode(
                spec, run=self._runner(json.dumps(polluted), _native_stream()),
                environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
            )
            self.assertIn("resolved_overlay_permission_violation", receipt.failures)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            receipt = benchmark.execute_opencode(
                spec,
                run=self._runner(_debug_config("read", "glob", "grep"), _native_stream(tool="webfetch")),
                environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"},
            )
            self.assertIn("tool_or_network_violation", receipt.failures)

    def test_native_unknown_malformed_length_and_budget_fail_closed(self) -> None:
        unknown = json.dumps({"type": "evaluation_summary", "timestamp": 1, "sessionID": "s"}) + "\n"
        telemetry = benchmark.parse_jsonl_telemetry(unknown.splitlines(True))
        self.assertTrue(telemetry["schema_errors"])
        malformed = benchmark.parse_jsonl_telemetry(['not-json\n', json.dumps({"type": "text", "timestamp": 1, "sessionID": "s"}) + "\n"])
        self.assertTrue(malformed["parse_errors"])
        self.assertTrue(malformed["schema_errors"])
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            length = benchmark.execute_opencode(spec, run=self._runner(_debug_config("read", "glob", "grep"), _native_stream(reason="length")), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertIn("truncated", length.failures)
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            excess = benchmark.execute_opencode(spec, run=self._runner(_debug_config("read", "glob", "grep"), _native_stream(output_tokens=4097)), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertIn("budget_exceeded", excess.failures)

    def test_chat_params_hook_rejects_41st_request_before_mock_provider(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is required to execute the evaluator hook fixture")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            overlay: dict[str, object] = {}
            hook = benchmark._install_model_budget_hook(overlay, root, 40)
            hook_source = hook["plugin_path"].read_text()
            for forbidden in ("apiKey", "prompt", "requestBody", "messages"):
                self.assertNotIn(forbidden, hook_source)
            driver = """
const plugin = (await import(process.argv[1])).default;
const hooks = await plugin({});
let providerCalls = 0;
let blocked = false;
for (let index = 0; index < 41; index += 1) {
  try {
    await hooks["chat.params"]({}, {});
    providerCalls += 1;
  } catch (error) {
    blocked = error.message === "PANGEA_EVALUATOR_MODEL_BUDGET_BLOCKED";
  }
}
process.stdout.write(JSON.stringify({ providerCalls, blocked }));
"""
            result = subprocess.run(
                [node, "--input-type=module", "-e", driver, overlay["plugin"][0]],
                cwd=root, capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual({"providerCalls": 40, "blocked": True}, json.loads(result.stdout))
            state = json.loads(hook["state_path"].read_text())
            self.assertEqual(40, state["model_requests_admitted"])
            self.assertTrue(state["pre_request_budget_blocked"])
            observation = benchmark._model_budget_observation(
                hook, {"model_calls": 40}, injected_runner=False,
            )
            self.assertTrue(observation["pre_request_budget_enforced"])
            self.assertTrue(observation["pre_request_budget_blocked"])
            self.assertEqual(40, observation["model_calls_completed"])

    def test_zero_remaining_budget_does_not_start_runner_and_injected_runner_is_posthoc(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            runner = Mock()
            blocked = benchmark.execute_opencode(
                spec, run=runner, environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "fake-test-key"},
                model_call_limit=0,
            )
            runner.assert_not_called()
            self.assertEqual(["budget_exceeded"], blocked.failures)
            self.assertTrue(blocked.telemetry["pre_request_budget_blocked"])
            self.assertEqual(0, blocked.telemetry["model_calls_completed"])
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            two_calls = _native_stream(text="first") + _native_stream(text="second")
            receipt = benchmark.execute_opencode(
                spec, run=self._runner(_debug_config("read", "glob", "grep"), two_calls),
                environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "fake-test-key"},
                model_call_limit=1,
            )
            self.assertIn("budget_exceeded", receipt.failures)
            self.assertFalse(receipt.telemetry["pre_request_budget_enforced"])
            self.assertTrue(receipt.telemetry["injected_test_runner"])
            self.assertEqual(2, receipt.telemetry["model_calls_completed"])
        with tempfile.TemporaryDirectory() as temp:
            runner = Mock()
            execution = benchmark.execute_isolated_role(
                "analysis-worker", {"CONTEXT.json": {"candidate": "bounded"}},
                run=runner, environ={"PATH": "/bin", "DEEPSEEK_API_KEY": "fake-test-key"},
                scratch_parent=Path(temp), model_call_limit=0,
            )
            runner.assert_not_called()
            self.assertEqual(["budget_exceeded"], execution.receipt["failures"])
            self.assertTrue(execution.receipt["pre_request_budget_blocked"])

    def test_primary_receipt_persists_only_finite_tool_violation_categories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp, "as-shipped")
            sensitive = "private-command-argument-must-not-appear"
            stream = _native_stream(
                tool="bash",
                tool_input={"command": f"rg needle /outside/{sensitive}", "workdir": "."},
            )
            receipt = benchmark.execute_opencode(
                spec,
                run=self._runner(
                    _debug_config(*benchmark.AS_SHIPPED_SAFE_TOOLS, safe_overlay=True),
                    stream,
                    as_shipped=True,
                ),
                environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "fake-test-key"},
            )
            summary = receipt.telemetry["tool_input_policy_violation_summary"]
            self.assertEqual(1, summary["total"])
            self.assertEqual({"path_scope": 1}, summary["category_counts"])
            self.assertLessEqual(set(summary["category_counts"]), benchmark.TOOL_INPUT_POLICY_CATEGORIES)
            self.assertEqual([], receipt.telemetry["tool_actions"])
            self.assertNotIn(sensitive, repr(receipt.telemetry))
            self.assertNotIn("command", repr(summary))
            self.assertNotIn("sha", repr(summary))

    def test_missing_native_token_finish_and_provider_error_fail(self) -> None:
        text_only = json.dumps({"type": "text", "timestamp": 1, "sessionID": "s", "part": {"type": "text", "text": "done"}}) + "\n"
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            receipt = benchmark.execute_opencode(spec, run=self._runner(_debug_config("read", "glob", "grep"), text_only), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertIn("missing_budget_telemetry", receipt.failures)
        error = json.dumps({"type": "error", "timestamp": 1, "sessionID": "s", "error": {"name": "APIError"}}) + "\n"
        with tempfile.TemporaryDirectory() as temp:
            spec, _ = self._spec(temp)
            receipt = benchmark.execute_opencode(spec, run=self._runner(_debug_config("read", "glob", "grep"), error), environ={"PATH": "/bin", "HOME": "/home", "DEEPSEEK_API_KEY": "local-test-provider"})
            self.assertIn("native_error_event", receipt.failures)

    def test_pangea_jsonl_adapter_reads_native_final_text(self) -> None:
        sealed = benchmark.seal_candidate_output(
            _native_stream('{"risks":[{"id":"R1","title":"leak","severity":"High"}]}'),
            "pangea", "equal-tools", "case", format="jsonl",
        )
        self.assertEqual("json", sealed["adapter"])
        self.assertEqual("R1", sealed["neutral"]["risks"][0]["id"])
        with self.assertRaisesRegex(benchmark.BenchmarkContractError, "truncated"):
            benchmark.seal_candidate_output(_native_stream(reason="length"), "pangea", "equal-tools", "case", format="jsonl")

    def test_fuse_markdown_adapter_extracts_real_neutral_structures(self) -> None:
        markdown = """# Analysis
## 源码证据
- lib/nvme/nvme.c:120-135 validates the state.
## 关键流程
1. CLI request enters reset flow.
## 状态转换
- READY -> RESETTING -> READY
## 资源生命周期
- allocate request then release it.
## 异常传播链
- timeout -> abort -> reconnect.
## 风险与 SFMEA
| ID | 风险 | 严重度 | 证据 |
|---|---|---|---|
| R1 | reset hangs | High | lib/nvme/nvme.c:120-135 |
## 反证检查
- Normal invalid input rejection is not a defect.
## 测试场景
- Reset while reconnecting.
## 黑盒测试用例
- Run nvme reset and observe bounded completion.
## 不适用
- Upgrade migration: N/A.
## 覆盖处置
- FLOW-1 analyzed.
"""
        sealed = benchmark.seal_candidate_output(markdown, "fuse", "equal-tools", "case", format="markdown")
        neutral = sealed["neutral"]
        for name in ("evidence", "flow_chains", "state_chains", "resource_chains", "error_chains", "risks", "disconfirming_checks", "scenarios", "cases", "na", "dispositions"):
            self.assertTrue(neutral[name], name)
        self.assertFalse(neutral["evaluator_review_required"])

    def test_markdown_adapter_marks_unmapped_sections_for_review(self) -> None:
        sealed = benchmark.seal_candidate_output("# Report\n## Mysterious material\nUnclassifiable content.", "fuse", "equal-tools", "case", format="markdown")
        self.assertTrue(sealed["neutral"]["evaluator_review_required"])
        self.assertFalse(sealed["score_eligible"])
        self.assertIn("Mysterious material", sealed["neutral"]["unparsed_sections"])

    def test_review_required_output_cannot_score_until_bound_external_resolution(self) -> None:
        sealed = benchmark.seal_candidate_output("# Report\n## Mysterious material\nUnclassifiable content.", "fuse", "equal-tools", "case", format="markdown")
        dimensions = {name: 1 for name in benchmark.load_frozen_config()["scorecard"]["weights"]}
        with tempfile.TemporaryDirectory() as oracle_temp:
            oracle_path = Path(oracle_temp) / "oracle.json"
            oracle_path.write_text('{"criteria":["x"]}', encoding="utf-8")
            oracle = benchmark.load_sealed_oracle(oracle_path)
            with self.assertRaisesRegex(benchmark.BenchmarkContractError, "not scoreable"):
                benchmark.score_dimensions(oracle, dimensions, candidate_output=sealed)
            review = {
                "schema_version": "1.0",
                "review_kind": "neutral-adapter-resolution",
                "candidate": "fuse",
                "track": "equal-tools",
                "case_id": "case",
                "raw_sha256": sealed["raw_sha256"],
                "reviewer": "independent-evaluator-1",
                "verdict": "resolved",
                "resolved_sections": ["Mysterious material"],
                "resolution_note": "Reviewed and retained as a claim.",
                "resolved_neutral": {
                    "claims": ["Unclassifiable content."],
                    "risks": [],
                    "evaluator_review_required": False,
                    "unparsed_sections": [],
                },
            }
            review_path = Path(oracle_temp) / "review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            resolved = benchmark.apply_adapter_review(sealed, review_path)
            self.assertTrue(resolved["score_eligible"])
            scored = benchmark.score_dimensions(oracle, dimensions, candidate_output=resolved)
            self.assertEqual(100, scored["score"])
            tampered = deepcopy(resolved)
            tampered["neutral"]["claims"].append("unbound")
            with self.assertRaisesRegex(benchmark.BenchmarkContractError, "digest mismatch"):
                benchmark.score_dimensions(oracle, dimensions, candidate_output=tampered)

    def test_adapter_review_rejects_wrong_binding_and_in_workspace_receipt(self) -> None:
        sealed = benchmark.seal_candidate_output("## Unknown\nbody", "fuse", "equal-tools", "case", format="markdown")
        review = {
            "schema_version": "1.0", "review_kind": "neutral-adapter-resolution",
            "candidate": "fuse", "track": "equal-tools", "case_id": "other",
            "raw_sha256": sealed["raw_sha256"], "reviewer": "reviewer", "verdict": "resolved",
            "resolved_sections": ["Unknown"], "resolution_note": "resolved",
            "resolved_neutral": {"claims": ["body"], "risks": [], "evaluator_review_required": False, "unparsed_sections": []},
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "review.json"
            path.write_text(json.dumps(review), encoding="utf-8")
            with self.assertRaisesRegex(benchmark.BenchmarkContractError, "not bound"):
                benchmark.apply_adapter_review(sealed, path)
        workspace_review = benchmark.ROOT / "benchmarks" / "evaluation" / "review-fixture.json"
        workspace_review.write_text(json.dumps(review), encoding="utf-8")
        try:
            with self.assertRaisesRegex(benchmark.BenchmarkContractError, "outside"):
                benchmark.apply_adapter_review(sealed, workspace_review)
        finally:
            workspace_review.unlink()

    def test_neutral_json_shape_and_private_scoring_are_separate(self) -> None:
        output = benchmark.normalize_candidate_output({
            "claims": [{"text": "claim"}], "evidence": ["src"], "flows": ["flow"],
            "state_changes": ["state"], "resource_lifecycle": ["resource"],
            "error_propagation": ["error"], "risks": [{"id": "R"}],
            "counterexamples": ["check"], "scenario_candidates": ["scenario"],
            "test_cases": ["case"], "not_applicable": ["N/A"], "dispositions": ["retained"],
        })
        required = {"claims", "evidence", "flow_chains", "state_chains", "resource_chains", "error_chains", "risks", "disconfirming_checks", "scenarios", "cases", "na", "dispositions"}
        self.assertTrue(required.issubset(output))
        self.assertEqual("R", output["risks"][0]["id"])
        with tempfile.TemporaryDirectory() as temp:
            oracle_path = Path(temp) / "oracle.json"
            oracle_path.write_text('{"criteria":["x"]}', encoding="utf-8")
            oracle = benchmark.load_sealed_oracle(oracle_path)
            sealed = benchmark.seal_candidate_output('{"risks":[]}', "pangea", "equal-tools", "case", format="json")
            dimensions = {name: 1 for name in benchmark.load_frozen_config()["scorecard"]["weights"]}
            self.assertEqual(100, benchmark.score_dimensions(oracle, dimensions, candidate_output=sealed)["score"])
            dimensions["recall"] = math.nan
            with self.assertRaises(benchmark.BenchmarkContractError):
                benchmark.score_dimensions(oracle, dimensions, candidate_output=sealed)
        with self.assertRaises(benchmark.BenchmarkContractError):
            benchmark.load_sealed_oracle(Path("benchmarks/oracles/spdk-tcp-error-pdu-accounting.json"))

    def test_all_frozen_gate_boundaries_and_invalid_metrics(self) -> None:
        config = benchmark.load_frozen_config()["scorecard"]["thresholds"]["absolute"]
        metrics: dict[str, object] = {**config["minimum"], **config["maximum"]}
        metrics.update({name: False for name in config["must_be_false"]})
        result = benchmark.evaluate_gates(metrics, fuse_score=90, paired_ci_lower=-2, core_win_rate=70)
        self.assertTrue(result["absolute"])
        self.assertTrue(result["at_least_fuse"])
        self.assertFalse(result["exceeds_fuse"])
        superior = dict(metrics)
        superior["score"] = 93
        self.assertTrue(benchmark.evaluate_gates(superior, 90, 0.0001, 70)["exceeds_fuse"])
        below = dict(metrics)
        below["score"] = 89.999
        self.assertFalse(benchmark.evaluate_gates(below, 89, -2, 70)["absolute"])
        regression = dict(metrics)
        regression["hard_gate_regression"] = 1
        self.assertFalse(benchmark.evaluate_gates(regression, 90, -2, 70)["absolute"])
        with self.assertRaises(benchmark.BenchmarkContractError):
            benchmark.evaluate_gates({}, 50, 0, 0)
        invalid = dict(metrics)
        invalid["score"] = math.nan
        with self.assertRaises(benchmark.BenchmarkContractError):
            benchmark.evaluate_gates(invalid, 90, 0, 70)
        with self.assertRaises(benchmark.BenchmarkContractError):
            benchmark.evaluate_gates(metrics, 101, 0, 70)

    def test_invalid_config_and_public_oracle_markers_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task = root / "TASK.md"
            task.write_text("safe", encoding="utf-8")
            benchmark.write_public_bundle_manifest(root)
            changed = deepcopy(benchmark.load_frozen_config())
            changed["runtime"]["opencode_version"] = "broken"
            with self.assertRaises(benchmark.BenchmarkContractError):
                benchmark.build_opencode_command(task, root, "pangea", "equal-tools", changed)
            changed_timeout = deepcopy(benchmark.load_frozen_config())
            changed_timeout["runtime"]["opencode_debug_timeout_seconds"] = 119
            with self.assertRaises(benchmark.BenchmarkContractError):
                benchmark.build_opencode_command(
                    task, root, "pangea", "equal-tools", changed_timeout,
                )
            task.write_text("read hidden oracle", encoding="utf-8")
            with self.assertRaises(benchmark.BenchmarkContractError):
                benchmark.build_opencode_command(task, root, "pangea", "equal-tools")


if __name__ == "__main__":
    unittest.main()
