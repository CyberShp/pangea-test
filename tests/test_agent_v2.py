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

DFX_TO_CAPABILITY = {
    "dfx-function-state": "functional-state",
    "dfx-resource-spec": "resource-specification",
    "dfx-performance-pressure": "performance-pressure",
    "dfx-concurrency-exception": "concurrency-exception",
    "dfx-upgrade-compatibility": "upgrade-compatibility",
    "dfx-reliability-consistency": "reliability-consistency",
}
FORMAL_COMMANDS = {
    "initial",
    "setup-tools",
    "mr-regression",
    "module-analysis",
    "resume-run",
}
INTERNAL_PRIMARY_TASKS = set(DFX_TO_CAPABILITY) | {
    "mr-reader",
    "code-excavator",
    "auditor",
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

    def test_exactly_six_hidden_read_only_dfx_subagents(self) -> None:
        dfx_agents = sorted(AGENTS.glob("dfx-*.md"))
        self.assertEqual(set(DFX_TO_CAPABILITY), {path.stem for path in dfx_agents})
        for path in dfx_agents:
            metadata = frontmatter(path)
            self.assertEqual("subagent", metadata.get("mode"), path.name)
            self.assertEqual("true", metadata.get("hidden"), path.name)
            permission = metadata.get("permission")
            self.assertIsInstance(permission, dict, path.name)
            self.assertEqual({"edit": "deny", "bash": "deny", "task": "deny"}, permission, path.name)
            self.assertIn("风险卡", path.read_text(encoding="utf-8"), path.name)

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


    def test_lifecycle_runs_require_fixed_evidence_provenance(self) -> None:
        combined = "\n".join((AGENTS / "pangea-test.md").read_text(encoding="utf-8") for _ in range(1))
        combined += "\n" + (COMMANDS / "module-analysis.md").read_text(encoding="utf-8")
        combined += "\n" + (COMMANDS / "mr-regression.md").read_text(encoding="utf-8")
        for term in ("stage-evidence-v2", "evidence-provenance.json", "file_sha256", "excerpt_sha256", "mr_facts"):
            self.assertIn(term, combined)


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
            "每批子 Agent 汇总后",
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
            "并行读取",
            "逐条串行执行",
            "禁止并发写 catalog",
            "library classify --source-path",
        ):
            self.assertIn(evidence, text)
        self.assertIn("两者都为 `0` 时不得读取全部 Markdown 或重分类", text)

    def test_dfx_agents_have_one_to_one_capability_matrix_and_risk_card_contract(self) -> None:
        registry = json.loads((ROOT / "registry" / "capabilities.json").read_text(encoding="utf-8"))
        packages = {item["id"]: item for item in registry["dfx_packages"]}
        self.assertEqual(set(DFX_TO_CAPABILITY.values()), set(packages))
        self.assertEqual("risk_card", registry["output_contract"]["artifact_type"])
        contract = ROOT / registry["output_contract"]["path"]
        self.assertTrue(contract.is_file())
        for agent_name, package_id in DFX_TO_CAPABILITY.items():
            package = packages[package_id]
            package_path = ROOT / package["path"]
            self.assertTrue(package_path.is_file(), package_id)
            self.assertTrue(package["always_for_module_analysis"], package_id)
            agent_text = (AGENTS / f"{agent_name}.md").read_text(encoding="utf-8")
            self.assertIn("skills/risk-card/SKILL.md", agent_text)
            self.assertIn(package["path"], agent_text)

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


if __name__ == "__main__":
    unittest.main()
