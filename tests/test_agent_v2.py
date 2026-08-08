from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from runtime import runctl


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".opencode" / "agents"
COMMANDS = ROOT / ".opencode" / "commands"
CAPABILITIES = ROOT / "core" / "capabilities"

CAPABILITY_PACKS = {
    "functional-state",
    "resource-specification",
    "performance-pressure",
    "concurrency-exception",
    "upgrade-compatibility",
    "reliability-consistency",
}
FORMAL_COMMANDS = {
    "initial",
    "setup-tools",
    "mr-regression",
    "module-analysis",
    "resume-run",
}
INTERNAL_PRIMARY_TASKS = {
    "analysis-worker",
    "mr-reader",
    "auditor",
}
RESTORED_SCENARIOS = {
    "FST逃逸复盘.md",
    "MR问题单分析.md",
    "专项风险分析.md",
    "共性问题排查.md",
    "原理讲解.md",
    "可测试性分析.md",
    "模块全量分析.md",
    "测试策略.md",
    "缺陷单撰写.md",
}


def frontmatter(path: Path) -> dict[str, object]:
    """Parse the small, intentionally flat OpenCode YAML front matter subset."""
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", text, re.DOTALL)
    if not match:
        raise AssertionError(f"missing front matter: {path.relative_to(ROOT)}")

    result: dict[str, object] = {}
    containers: dict[int, dict[str, object]] = {0: result}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = re.match(r'^([ \t]*)(?:"([^"]+)"|([\w*-]+)):\s*(.*?)\s*$', line)
        if not item:
            raise AssertionError(f"unsupported front matter line in {path}: {line}")
        indentation, quoted_key, bare_key, value = item.groups()
        key = quoted_key or bare_key
        depth = len(indentation)
        parent = result if depth == 0 else containers[max(level for level in containers if level < depth)]
        for level in [level for level in containers if level > depth]:
            del containers[level]
        if not value:
            child: dict[str, object] = {}
            parent[key] = child
            containers[depth] = child
        else:
            parent[key] = value
    return result


class AgentV2StructureTests(unittest.TestCase):
    def test_mr_command_stage_sequence_matches_registry_and_runctl_plan(self) -> None:
        registry = json.loads((ROOT / "registry" / "scenarios.json").read_text(encoding="utf-8"))
        expected = registry["scenarios"]["mr-regression"]["stages"]
        command = (COMMANDS / "mr-regression.md").read_text(encoding="utf-8")
        rendered = "、".join(f"`{stage}`" for stage in expected)
        self.assertIn(f"MR 的 workflow 阶段依次为 {rendered}", command)
        self.assertEqual(expected, runctl.v2_plan({"mode": "mr_regression", "signals": []})["stages"])

    def test_report_model_template_is_canonical_and_renderable(self) -> None:
        from runtime.reporting import render

        template = json.loads((ROOT / "core" / "templates" / "report-v2" / "report-model.json").read_text(encoding="utf-8"))
        self.assertNotIn("待填写", json.dumps(template, ensure_ascii=False))
        self.assertEqual("module_analysis", template["task_contract"]["mode"])
        self.assertIn(runctl.validate(template["task_contract"], "task-contract.schema.json"), {"stdlib", "jsonschema"})
        self.assertEqual(template["task_contract"], runctl._assert_formal_task_contract(template["task_contract"]))
        for risk in template["risks"]:
            self.assertIn(runctl.validate(risk, "risk-card.schema.json"), {"stdlib", "jsonschema"})
        markdown, page = render(template)
        self.assertIn("R-RESOURCE-001", markdown)
        self.assertIn("TC-RESOURCE-001", page)

    def test_unique_primary_and_retired_family_agents(self) -> None:
        agent_files = sorted(AGENTS.glob("*.md"))
        primary = [path.stem for path in agent_files if frontmatter(path).get("mode") == "primary"]
        self.assertEqual(["pangea-test"], primary)
        for retired in ("dev-expert", "troubleshooter", "test-designer"):
            self.assertFalse((AGENTS / f"{retired}.md").exists(), retired)

    def test_runtime_roles_are_four_files_with_generic_worker_protocol(self) -> None:
        self.assertEqual(
            {"pangea-test.md", "analysis-worker.md", "auditor.md", "mr-reader.md"},
            {path.name for path in AGENTS.glob("*.md")},
        )
        worker = AGENTS / "analysis-worker.md"
        metadata = frontmatter(worker)
        self.assertEqual("subagent", metadata.get("mode"))
        self.assertEqual("true", metadata.get("hidden"))
        permission = metadata.get("permission")
        self.assertIsInstance(permission, dict)
        for name in ("edit", "bash", "task", "webfetch", "skill", "todowrite", "external_directory"):
            self.assertEqual("deny", permission.get(name), name)
        tools = metadata.get("tools")
        self.assertIsInstance(tools, dict)
        for name in ("invalid", "webfetch", "skill", "todowrite", "task", "bash", "edit"):
            self.assertEqual("false", tools.get(name), name)
        text = worker.read_text(encoding="utf-8")
        for token in ("immutable", "context_pack_sha256", "obligation", "analysis_fragment", "strict JSON", "4096", "N-A", "need_verify", "receipt"):
            self.assertIn(token, text)
        self.assertIn("不得自派 task", text)

    def test_mr_reader_is_strictly_mr_conditional(self) -> None:
        text = (AGENTS / "mr-reader.md").read_text(encoding="utf-8")
        self.assertIn("MR", text)
        primary = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")
        self.assertIn("`mr-reader` 仅在 MR", primary)
        self.assertNotIn("mr-reader", (COMMANDS / "module-analysis.md").read_text(encoding="utf-8"))

    def test_storage_skills_are_not_runtime_agents(self) -> None:
        skills = ROOT / ".opencode" / "skills"
        storage = {path.name for path in skills.glob("storage-*")}
        self.assertGreaterEqual(len(storage), 6)
        self.assertTrue(storage.isdisjoint({path.stem for path in AGENTS.glob("*.md")}))

    def test_no_legacy_agents_or_role_dispatch_text(self) -> None:
        legacy = re.compile(
            r"code-excavator|dfx-(function|resource|performance|concurrency|upgrade|reliability)"
            r"|fan-out|log-miner|pcap-analyzer|族\s*agent|归属族",
            re.IGNORECASE,
        )
        paths = [ROOT / "README.md", ROOT / "docs" / "architecture.md", ROOT / "docs" / "requirements.md"]
        paths += list(AGENTS.glob("*.md")) + list(COMMANDS.glob("*.md"))
        paths += list((ROOT / ".opencode" / "skills").rglob("*.md")) + list((ROOT / "core").rglob("*.md"))
        offenders = [str(path.relative_to(ROOT)) for path in paths if legacy.search(path.read_text(encoding="utf-8"))]
        self.assertEqual([], offenders)

    def test_agent_frontmatter_denies_ambient_tools_and_records_host_path_blocker(self) -> None:
        for path in AGENTS.glob("*.md"):
            metadata = frontmatter(path)
            tools = metadata.get("tools")
            permission = metadata.get("permission")
            self.assertIsInstance(tools, dict, path.name)
            self.assertIsInstance(permission, dict, path.name)
            for name in ("invalid", "webfetch", "skill", "todowrite"):
                self.assertEqual("false", tools.get(name), f"{path.name}:{name}")
            for name in ("edit", "bash", "webfetch", "skill", "todowrite", "external_directory"):
                self.assertEqual("deny", permission.get(name), f"{path.name}:{name}")

        combined = "\n".join((AGENTS / name).read_text(encoding="utf-8") for name in (
            "pangea-test.md", "analysis-worker.md", "auditor.md"))
        for blocker in (
            "$HOME/.local/share/opencode/tool-output/*",
            "HOME",
            "XDG_*",
            "pack-only",
            "artifact-only",
            "不证明完整路径沙箱",
        ):
            self.assertIn(blocker, combined)

    def test_capability_packs_are_six_and_bound_to_files(self) -> None:
        registry = json.loads((ROOT / "registry" / "capabilities.json").read_text(encoding="utf-8"))
        self.assertNotIn("dfx_packages", registry)
        packages = {item["id"]: item for item in registry["capability_packs"]}
        self.assertEqual(CAPABILITY_PACKS, set(packages))
        for package in packages.values():
            self.assertTrue((ROOT / package["path"]).is_file(), package)

    def test_eight_questions_are_executable_not_skeleton(self) -> None:
        text = (ROOT / "core" / "shared" / "八问纲领.md").read_text(encoding="utf-8")
        self.assertNotIn("骨架", text)
        self.assertNotIn("待迁移", text)
        for term in ("inventory", "obligation", "analysis_fragment", "责任", "注册", "超时", "并发", "control", "oracle", "N-A", "未决"):
            self.assertIn(term, text)

    def test_retired_dfx_files_do_not_exist(self) -> None:
        for retired in (
            "code-excavator", "dfx-function-state", "dfx-resource-spec", "dfx-performance-pressure",
            "dfx-concurrency-exception", "dfx-upgrade-compatibility", "dfx-reliability-consistency",
        ):
            self.assertFalse((AGENTS / f"{retired}.md").exists(), retired)

    def test_commands_are_exactly_the_formal_pangea_test_entrypoints(self) -> None:
        command_files = sorted(COMMANDS.glob("*.md"))
        self.assertEqual(FORMAL_COMMANDS, {path.stem for path in command_files})
        for path in command_files:
            self.assertEqual("pangea-test", frontmatter(path).get("agent"), path.name)

    def test_formal_commands_use_portable_preflight_and_never_compose_shell_commands(self) -> None:
        combined = "\n".join((COMMANDS / f"{name}.md").read_text(encoding="utf-8") for name in FORMAL_COMMANDS)
        self.assertIn("tooling.pangea_cli preflight", combined)
        self.assertIn("禁止 `cd`", combined)
        self.assertIn("不得使用 `&&`", combined)
        self.assertNotIn("python3 runtime/runctl.py", combined)
        primary = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")
        for rule in ("workspace_unresolved", "禁止扫描盘符", "python_executable", "一次工具调用只启动一个进程"):
            self.assertIn(rule, primary)


    def test_formal_analysis_commands_require_contract_lifecycle(self) -> None:
        module = (COMMANDS / "module-analysis.md").read_text(encoding="utf-8")
        mr = (COMMANDS / "mr-regression.md").read_text(encoding="utf-8")
        for text in (module, mr):
            for command in ("draft-contract-v2", "revise-contract-v2", "confirm-contract-v2", "activate-contract-v2"):
                self.assertIn(command, text)
            self.assertNotIn("runctl.py create-v2", text)
        self.assertIn("confirmation_required: true", (AGENTS / "pangea-test.md").read_text(encoding="utf-8"))


    def test_primary_can_dispatch_only_internal_capabilities(self) -> None:
        metadata = frontmatter(AGENTS / "pangea-test.md")
        self.assertEqual("primary", metadata.get("mode"))
        permission = metadata.get("permission")
        self.assertIsInstance(permission, dict)
        self.assertEqual("deny", permission.get("edit"))
        task = permission.get("task")
        self.assertIsInstance(task, dict)
        self.assertEqual("deny", task.get("*"))
        allowed = {name for name, value in task.items() if name != "*" and value == "allow"}
        self.assertEqual(INTERNAL_PRIMARY_TASKS, allowed)
        self.assertEqual({"*"} | INTERNAL_PRIMARY_TASKS, set(task))

    def test_primary_owns_session_prepare_and_incremental_document_discovery(self) -> None:
        text = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")
        for obligation in (
            "每个 new session",
            "data session-prepare",
            "tool probe",
            "index all",
            "同一 session 不得",
            "首次接触用户新放入",
            "增量扫描与转换",
            "同一路径、同一 SHA-256",
        ):
            self.assertIn(obligation, text)
        self.assertIn("已有分类或同哈希继承分类不得重做", text)

    def test_primary_uses_a_persisted_context_ledger_before_any_compression(self) -> None:
        text = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")
        for obligation in (
            "每个阶段完成后",
            "每批 worker fragment 校验合并后",
            "开始审计整改前",
            "预计发生上下文压缩前",
            "checkpoint 和风险账本",
            "恢复 Run 时只读这些账本和工件",
            "任务契约、具体数字、版本和组网、源码位置",
            "事实/推断/待确认边界、因果链",
            "全部风险（尤其 High 和 Critical）",
            "场景与用例覆盖、已作决策和未闭环项",
            "重复叙述、工具原始噪声、无证据探索和已推翻猜测",
            "不得把模型原生自动压缩当作主策略",
            "checkpoint 与风险账本均已成功落盘后",
            "最后的降级路径",
        ):
            self.assertIn(obligation, text)

    def test_restored_scenarios_keep_full_workflow_density_and_gates(self) -> None:
        scenario_dir = ROOT / "core" / "scenarios"
        for name in RESTORED_SCENARIOS:
            text = (scenario_dir / name).read_text(encoding="utf-8")
            self.assertGreaterEqual(len(text.splitlines()), 50, name)
            for required in ("## 1. 场景定位", "## 2. 输入", "## 3.", "收尾", "primary", "黑盒"):
                self.assertIn(required, text, f"{name}:{required}")
            self.assertRegex(text, r"(流程|编排|链路)", name)

        deep_gate_scenarios = RESTORED_SCENARIOS - {"原理讲解.md", "缺陷单撰写.md"}
        for name in deep_gate_scenarios:
            text = (scenario_dir / name).read_text(encoding="utf-8")
            for gate in ("auditor", "PASS", "FAIL"):
                self.assertIn(gate, text, f"{name}:{gate}")

        combined = "\n".join((scenario_dir / name).read_text(encoding="utf-8") for name in RESTORED_SCENARIOS)
        for retained_semantic in (
            "位置", "速度型", "深度型", "剧本", "透镜", "方法", "证据", "事实", "推测",
            "SFMEA", "pangea-data/runs/", "恢复", "CONCERNS",
        ):
            self.assertIn(retained_semantic, combined, retained_semantic)

        # Do not let a shared boilerplate paragraph mask a scenario that lost
        # its own decision rule during role convergence.
        distinct_anchors = {
            "FST逃逸复盘.md": "FST", "MR问题单分析.md": "MR", "专项风险分析.md": "专项",
            "共性问题排查.md": "共性", "原理讲解.md": "原理", "可测试性分析.md": "可测试性",
            "模块全量分析.md": "模块", "测试策略.md": "测试策略", "缺陷单撰写.md": "缺陷单",
        }
        for name, anchor in distinct_anchors.items():
            self.assertIn(anchor, (scenario_dir / name).read_text(encoding="utf-8"), name)

    def test_scenarios_dispatch_only_fixed_roles_and_diagnostics_use_frozen_evidence(self) -> None:
        scenario_dir = ROOT / "core" / "scenarios"
        combined = "\n".join(path.read_text(encoding="utf-8") for path in scenario_dir.glob("*.md"))
        targets = set(re.findall(r"Task\s*派\s*`([^`]+)`", combined))
        self.assertTrue(targets)
        self.assertTrue(targets.issubset(INTERNAL_PRIMARY_TASKS), targets)

        for name in ("日志定位.md", "抓包辅助定位.md", "失败用例三分类.md"):
            text = (scenario_dir / name).read_text(encoding="utf-8")
            self.assertNotRegex(text, r"log-miner|pcap-analyzer")
            for required in ("primary", "冻结", "obligation", "analysis-worker"):
                self.assertIn(required, text, f"{name}:{required}")
        triage = (scenario_dir / "失败用例三分类.md").read_text(encoding="utf-8")
        self.assertIn("初始分类不得派发其他 Task target", triage)

    def test_initial_requires_bounded_inferred_classification_and_serial_writes(self) -> None:
        text = (COMMANDS / "initial.md").read_text(encoding="utf-8")
        for evidence in (
            "inbox.added",
            "inbox.changed",
            "catalog",
            "markdown_path",
            "必要锚点",
            "semantic_classification",
            "classification_sha256",
            "同哈希继承分类",
            '"source_backed": false',
            '"provenance": "model_inference"',
            "资料整理推断，不是材料事实",
            "immutable context pack",
            "不得临时增加角色",
            "逐条串行执行",
            "禁止并发写 catalog",
            "library classify --source-path",
        ):
            self.assertIn(evidence, text)
        self.assertIn("两者都为 `0` 时不得读取全部 Markdown 或重分类", text)

    def test_risk_translation_contract_separates_severity_confidence_and_instrumentation(self) -> None:
        risk_skill = (ROOT / ".opencode" / "skills" / "risk-card" / "SKILL.md").read_text(encoding="utf-8")
        risk_contract = (CAPABILITIES / "risk-card-contract.md").read_text(encoding="utf-8")
        translator = (CAPABILITIES / "test-semantic-translation.md").read_text(encoding="utf-8")
        combined = "\n".join((risk_skill, risk_contract, translator))
        self.assertRegex(combined, r"severity:.*Critical.*High.*Medium.*Low")
        self.assertRegex(combined, r"confidence:.*(high|高).*(medium|中).*(low|低)")
        self.assertIn("严重度不等于可信度", combined)
        for status in ("Blackbox-ready", "Graybox-ready", "Developer-confirm"):
            self.assertIn(status, combined)
        self.assertIn("插桩需求", combined)
        self.assertIn("不生成插桩代码", combined)

    def test_report_contract_requires_matching_markdown_and_offline_html(self) -> None:
        report = (ROOT / ".opencode" / "skills" / "report-contract" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("report.md", report)
        self.assertIn("report.html", report)
        self.assertIn("内容一致", report)
        self.assertIn("离线单文件", report)
        self.assertIn("模块代码地图", report)
        self.assertIn("异常分支", report)
        self.assertIn("风险与用例覆盖映射", report)

    def test_audit_protocol_is_v2_bound_to_the_fixed_run_report_model(self) -> None:
        auditor = (AGENTS / "auditor.md").read_text(encoding="utf-8")
        primary = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")
        report = (ROOT / ".opencode" / "skills" / "report-contract" / "SKILL.md").read_text(encoding="utf-8")
        architecture = (ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
        commands = "\n".join((COMMANDS / name).read_text(encoding="utf-8") for name in (
            "mr-regression.md", "module-analysis.md", "resume-run.md"))
        combined = "\n".join((auditor, primary, report, architecture, commands))

        for evidence in (
            "internal/report-model.json",
            "audit_opinion",
            'schema_version": "2.0"',
            "audited_artifact",
            "audited_sha256",
            "traceability",
            "blackbox_executability",
            "format_compliance",
            "required_actions",
            "record-rework-v2",
            "finalize-v2",
            "action_index",
            "hashlib.sha256",
        ):
            self.assertIn(evidence, combined)
        self.assertIn("不得输出顶层 `findings`", auditor)
        self.assertIn("不得自行计算、猜测或替换哈希", auditor)
        self.assertNotIn("`findings[]`", auditor)
        self.assertNotIn("`coverage_gaps[]`", auditor)

        template_match = re.search(r"```json\s*(\{.*?\})\s*```", auditor, re.DOTALL)
        self.assertIsNotNone(template_match)
        template = json.loads(template_match.group(1))
        template["audited_sha256"] = "0" * 64
        template["verdict"] = "PASS"
        for check in template["checks"].values():
            check["verdict"] = "PASS"
        self.assertIn(runctl.validate(template, "audit-opinion.schema.json"), {"jsonschema", "stdlib"})

        templates = [json.loads(value) for value in re.findall(r"```json\s*(\{.*?\})\s*```", auditor, re.DOTALL)]
        non_pass = [value for value in templates if value.get("verdict") in {"CONCERNS", "FAIL"}]
        self.assertTrue(non_pass, "auditor 必须提供非 PASS 完整模板")
        action_schema = json.loads((ROOT / "schemas" / "audit-opinion.schema.json").read_text(encoding="utf-8"))["properties"]["required_actions"]["items"]
        self.assertEqual({"action_type", "reason", "anchor", "verification"}, set(action_schema["required"]))
        for opinion in non_pass:
            self.assertIn(runctl.validate(opinion, "audit-opinion.schema.json"), {"jsonschema", "stdlib"})
            for action in opinion["required_actions"]:
                self.assertTrue(set(action_schema["required"]).issubset(action))
        for field in action_schema["required"]:
            self.assertIn(f"`{field}`", auditor)

    def test_shared_evidence_document_examples_validate_against_live_schemas(self) -> None:
        document = (ROOT / "core" / "shared" / "证据包schema.md").read_text(encoding="utf-8")
        examples = [json.loads(value) for value in re.findall(r"```json\s*(\{.*?\})\s*```", document, re.DOTALL)]
        by_type = {example["artifact_type"]: example for example in examples}
        self.assertEqual({"code_evidence", "audit_opinion"}, set(by_type))
        self.assertEqual("1.0", by_type["code_evidence"]["schema_version"])
        self.assertIn("artifact_id", by_type["code_evidence"])
        self.assertEqual("2.0", by_type["audit_opinion"]["schema_version"])
        self.assertIn(runctl.validate(by_type["code_evidence"], "code-evidence.schema.json"), {"jsonschema", "stdlib"})
        self.assertIn(runctl.validate(by_type["audit_opinion"], "audit-opinion.schema.json"), {"jsonschema", "stdlib"})
        for action in by_type["audit_opinion"]["required_actions"]:
            self.assertTrue({"action_type", "reason", "anchor", "verification"}.issubset(action))

    def test_mr_snapshots_are_run_scoped_and_resume_does_not_switch_source_repository(self) -> None:
        primary = (AGENTS / "pangea-test.md").read_text(encoding="utf-8")
        mr_command = (COMMANDS / "mr-regression.md").read_text(encoding="utf-8")
        resume_command = (COMMANDS / "resume-run.md").read_text(encoding="utf-8")
        for text in (primary, mr_command):
            self.assertIn("tooling.pangea_cli repo snapshot", text)
            self.assertIn("tmp/snapshots", text)
            self.assertIn("checkout、reset", text)
        self.assertIn("snapshots JSON", primary)
        self.assertIn("snapshot manifest", resume_command)
        self.assertIn("commit_sha", resume_command)
        self.assertIn("不得 checkout、reset、切换", resume_command)

    def test_no_legacy_ban_on_view_based_dfx_agents_remains(self) -> None:
        prohibited = re.compile(r"禁止[^\n]{0,80}(?:按[^\n]{0,20}视角[^\n]{0,20}(?:拆|分).{0,20}Agent|视角[^\n]{0,30}Agent)")
        paths = list((ROOT / ".opencode").rglob("*.md")) + list((ROOT / "core").rglob("*.md"))
        offenders = [str(path.relative_to(ROOT)) for path in paths if prohibited.search(path.read_text(encoding="utf-8"))]
        self.assertEqual([], offenders)

    def test_active_contracts_do_not_restore_retired_personas_or_dfx_agents(self) -> None:
        active = [ROOT / "README.md", ROOT / "docs" / "architecture.md", ROOT / "registry" / "capabilities.json",
                  ROOT / "registry" / "scenarios.json", ROOT / "runtime" / "runctl.py"]
        active += list((ROOT / ".opencode" / "agents").glob("*.md"))
        forbidden = re.compile(r"\b(?:code-excavator|dfx-function-state|dfx-resource-spec|dfx-performance-pressure|dfx-concurrency-exception|dfx-upgrade-compatibility|dfx-reliability-consistency)\b")
        self.assertEqual([], [str(path.relative_to(ROOT)) for path in active if forbidden.search(path.read_text(encoding="utf-8"))])


if __name__ == "__main__":
    unittest.main()
