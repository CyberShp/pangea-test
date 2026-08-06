from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from runtime.reporting import ReportError, SECTION_TITLES, render, write_report


MODEL = {
    "title": "资源回落测试报告",
    "task_contract": {
        "分析模式": "module-analysis", "目标模块": "iSCSI 连接", "代码版本": "abc123",
        "测试重点": "规格越界后的恢复", "排除范围": "不执行真实阵列用例",
    },
    "code_map": [{"title": "连接处理", "test_explanation": "主机连接进入接收准备后才能发送业务报文。", "source_evidence": "connection.c: ready transition"}],
    "flows": [{"title": "连接流程", "test_explanation": "建立连接后进入业务报文接收。", "steps": ["建立连接", "进入接收准备", "发送业务报文"], "mermaid": "flowchart LR; A-->B", "source_evidence": "connection.c:10"}],
    "branches": [{"title": "报文提前到达", "test_explanation": "在接收准备完成前发送 DATA 报文。", "source_evidence": "connection.c:20"}],
    "risks": [{
        "id": "R-1", "title": "额度不恢复", "severity": "Critical", "confidence": "高", "dfx": ["资源与规格"],
        "translation": "Graybox-ready", "test_explanation": "压力解除后新业务应恢复。", "trigger": "超过上限后回落",
        "propagation": "可申请额度持续降低", "impact": "业务归零", "observation": "IOPS 和额度", "recovery": "无需重启",
        "source_evidence": "counter++ path lacks decrement",
    }],
    "scenarios": [{"scenario_id": "SC-1", "title": "规格回落", "risk_ids": ["R-1"], "description": "先超过规格，再回落并观察恢复。", "trigger": "连接压力超过规格上限", "expected": "业务自行恢复"}],
    "test_cases": [{
        "id": "TC-1", "title": "压力回落", "risk_ids": ["R-1"], "preconditions": "存在可控负载",
        "steps": ["通过测试插桩将 iscsi_rsp 置位延迟 2 秒，以扩大接收准备时间窗。", "将并发请求提升到规格上限以上，再逐步降回规格内。"],
        "expected": "业务恢复且可申请额度回到正常水平", "observation": "性能指标和诊断计数", "cleanup": "停止负载",
        "instrumentation": "延迟 iscsi_rsp 置为 TRUE，不生成插桩代码",
    }],
    "unresolved": ["缺少关联微码仓"], "next_steps": ["补充微码仓后复查恢复链"],
}


CANONICAL_RISK = {
    "risk_id": "R-CANON", "title": "接收准备竞态", "dfx": ["并发与异常"], "severity": "High", "confidence": "high",
    "trigger": "握手结束后立即发送 DATA", "propagation": "报文进入未就绪路径", "external_impact": "连接中断",
    "observation": "连接状态和协议日志", "recovery": "重新连接", "translation_status": "Graybox-ready",
    "test_explanation": "在连接准备窗口内提前发送报文，验证业务不会异常中断。",
    "instrumentation_request": {"control": "延迟 iscsi_rsp 置位", "range": "0-5 秒"},
    "evidence": [{"path": "iscsi.c", "line": 42, "fact": "ready flag is set after handshake"}],
}


class ReportingTests(unittest.TestCase):
    def test_fixed_ten_sections_and_bidirectional_links(self) -> None:
        markdown, page = render(MODEL)
        for number, title in enumerate(SECTION_TITLES, 1):
            self.assertIn(f"## {number}. {title}", markdown)
            self.assertIn(f">{number}. {title}<", page)
        self.assertIn("[风险 R-1](#risk-R-1)", markdown)
        self.assertIn('href="#risk-R-1"', page)
        self.assertIn('href="#case-TC-1"', page)
        self.assertIn("严重度：Critical", page)
        self.assertIn("可信度：高", page)
        self.assertIn("Mermaid 未配置离线渲染器", markdown)
        self.assertIn("文字流程图", page)

    def test_canonical_schema_fields_and_legacy_fixture_are_both_supported(self) -> None:
        model = json.loads(json.dumps(MODEL))
        model["risks"].append(CANONICAL_RISK)
        model["scenarios"].append({"scenario_id": "SC-2", "title": "提前报文", "risk_ids": ["R-CANON"], "description": "控制时间窗并发送 DATA", "trigger": "连接建立后立即发送 DATA", "expected": "系统稳定拒绝或正确处理提前报文"})
        markdown, page = render(model)
        self.assertIn("接收准备竞态", markdown)
        self.assertIn("连接中断", markdown)
        self.assertIn("iscsi.c", markdown)
        self.assertIn("延迟 iscsi_rsp 置位", markdown)
        self.assertIn("延迟 iscsi_rsp 置位", page)
        self.assertIn('data-translation="Graybox-ready"', page)

    def test_coverage_gate_and_developer_confirm(self) -> None:
        missing = json.loads(json.dumps(MODEL)); missing["scenarios"] = []; missing["test_cases"] = []
        with self.assertRaisesRegex(ReportError, "coverage gap"):
            render(missing)
        missing["coverage_gaps"] = [{"risk_id": "R-1", "reason": "缺少可控负载环境"}]
        self.assertIn("缺口：缺少可控负载环境", render(missing)[0])

        developer = json.loads(json.dumps(MODEL)); developer["risks"][0]["translation"] = "Developer-confirm"
        with self.assertRaisesRegex(ReportError, "不得生成可执行场景"):
            render(developer)
        developer["scenarios"] = []
        with self.assertRaisesRegex(ReportError, "不得生成可执行用例"):
            render(developer)
        developer["test_cases"] = []
        developer["risks"][0]["coverage_gap"] = "缺少固件状态的外部观测接口"
        markdown, page = render(developer)
        self.assertIn("覆盖缺口：缺少固件状态的外部观测接口", markdown)
        self.assertIn("缺口：缺少固件状态的外部观测接口", markdown)
        self.assertIn("覆盖缺口：</b>缺少固件状态的外部观测接口", page)
        self.assertIn("缺口：缺少固件状态的外部观测接口", page)

    def test_filters_apply_to_risks_scenarios_and_cases_and_output_is_offline(self) -> None:
        _, page = render(MODEL)
        self.assertGreaterEqual(page.count("data-filterable"), 3)
        self.assertIn('id="severity"', page)
        self.assertIn('id="dfx"', page)
        self.assertIn('id="translation"', page)
        self.assertNotIn("https://", page)
        self.assertNotIn("http://", page)
        self.assertNotIn("cdn", page.lower())

    def test_xss_is_escaped_and_svg_is_sanitized(self) -> None:
        model = json.loads(json.dumps(MODEL))
        model["title"] = "<script>alert(1)</script>"
        model["risks"][0]["title"] = "<script>alert(2)</script>"
        model["flows"][0]["diagram_svg"] = '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(3)"><text>安全流程</text></svg>'
        _, page = render(model)
        self.assertIn("&lt;script&gt;alert(1)", page)
        self.assertIn("&lt;script&gt;alert(2)", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertNotIn("onload", page)
        self.assertIn("安全流程", page)
        for step in MODEL["flows"][0]["steps"]:
            self.assertIn(step, page)
        self.assertIn("文字流程图", page)

    def test_svg_external_url_attributes_are_removed_but_internal_fragments_survive(self) -> None:
        model = json.loads(json.dumps(MODEL))
        model["flows"][0]["diagram_svg"] = '''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
          <defs><marker id="safe-marker"><path d="M0,0 L2,1 L0,2" /></marker></defs>
          <rect id="safe-rect" filter="url(https://audit.invalid/filter.svg#f)" fill="URL( HTTPS://audit.invalid/fill.svg#f )"
            stroke="u&#x72;l(https://audit.invalid/stroke.svg#s)" color="u\\72l(https://audit.invalid/css-escape.svg#c)" clip-path="url(https://audit.invalid/clip.svg#c)"
            mask="url(https://audit.invalid/mask.svg#m)" marker-start="url(https://audit.invalid/start.svg#a)"
            marker-mid="url(https://audit.invalid/mid.svg#a)" marker-end="url(https://audit.invalid/end.svg#a)"
            href="https://audit.invalid/href" xlink:href="https://audit.invalid/xlink" src="https://audit.invalid/src"
            style="fill:url(https://audit.invalid/style.svg#f)" />
          <path id="safe-path" marker-end="url(#safe-marker)" fill="url(#safe-fill)" href="#safe-rect" />
        </svg>'''
        _, page = render(model)
        self.assertNotIn("audit.invalid", page)
        self.assertNotIn("filter=", page)
        self.assertNotIn("clip-path=", page)
        self.assertNotIn("marker-start=", page)
        self.assertNotIn("style=", page)
        self.assertIn('marker-end="url(#safe-marker)"', page)
        self.assertIn('fill="url(#safe-fill)"', page)
        self.assertIn('href="#safe-rect"', page)

    def test_quality_gate_allows_graybox_control_but_rejects_symbol_or_code(self) -> None:
        render(MODEL)
        bad = json.loads(json.dumps(MODEL)); bad["test_cases"][0]["steps"] = ["iscsi_rsp_set()"]
        with self.assertRaisesRegex(ReportError, "缺少测试语义"): render(bad)
        bad = json.loads(json.dumps(MODEL)); bad["test_cases"][0]["steps"] = ["void inject_delay() { iscsi_rsp = false; }"]
        with self.assertRaisesRegex(ReportError, "插桩实现代码"): render(bad)
        bad = json.loads(json.dumps(MODEL)); bad["test_cases"][0]["steps"] = ["编写函数级 mock 单元测试验证资源释放"]
        with self.assertRaisesRegex(ReportError, "Mock/Stub"): render(bad)

    def test_quality_gate_rejects_test_doubles_without_unit_test_context(self) -> None:
        fields = (
            ("scenarios", 0, "description", "使用 Mock 协议端返回超时响应"),
            ("test_cases", 0, "steps", ["使用 Stub 控制响应结果"]),
            ("test_cases", 0, "expected", "Fake 后端记录了请求"),
            ("test_cases", 0, "observation", "通过 Spy 观察调用次数"),
            ("test_cases", 0, "cleanup", "移除测试替身"),
            ("test_cases", 0, "instrumentation", "安装模拟对象以拦截请求"),
        )
        for collection, index, field, value in fields:
            with self.subTest(field=field, value=value):
                bad = json.loads(json.dumps(MODEL))
                bad[collection][index][field] = value
                with self.assertRaisesRegex(ReportError, "Mock/Stub/Fake/Spy"):
                    render(bad)

    def test_quality_gate_rejects_whitebox_calls_and_implementation_statements(self) -> None:
        for value, message in (
            ("直接调用内部函数 update_ready 并断言返回值为成功。", "直接调用内部函数/方法"),
            ("直接调用内部方法 reset_state，并断言内部变量 ready 为 false。", "直接调用内部函数/方法"),
            ("ready.store(false);", "插桩实现代码"),
            ("state.exchange(next);", "插桩实现代码"),
            ("counter.fetch_add(1);", "插桩实现代码"),
            ("{ ready.store(false); }", "插桩实现代码"),
            ("ready.store(false)", "插桩实现代码"),
            ("ready = false", "插桩实现代码"),
            ("调用内部函数 update_ready() 并断言返回值为成功。", "直接调用内部函数/方法"),
            ("直接调用内部函数 update_ready() 并断言执行成功。", "直接调用内部函数/方法"),
            ("state = next", "插桩实现代码"),
            ("ready->store(false)", "插桩实现代码"),
            ("state->exchange(next)", "插桩实现代码"),
            ("断言内部函数 update_ready() 返回值为成功。", "直接调用内部函数/方法"),
            ("ready = !enabled", "插桩实现代码"),
            ("state = (next_state)", "插桩实现代码"),
            ("state /*x*/ = next", "插桩实现代码"),
            ("state // x\n = next", "插桩实现代码"),
            ("counter[index] += 1", "插桩实现代码"),
            ("counter[index]++", "插桩实现代码"),
            ("counter[index[i]] += 1", "插桩实现代码"),
            ("counter[index[i]]++", "插桩实现代码"),
            ("++(*context->counters[index[i]]).value", "插桩实现代码"),
            ("(*context->matrix[row[i]][column[j]]).state = next", "插桩实现代码"),
            ("(connection->state) = (next_state)", "插桩实现代码"),
            ("context.session.state += 1", "插桩实现代码"),
            ("(++counter[index])", "插桩实现代码"),
            ("ready += 1", "插桩实现代码"),
            ("++ready", "插桩实现代码"),
            ("retry_count--", "插桩实现代码"),
            ("std::exchange(ready, false)", "插桩实现代码"),
            ("std::atomic_exchange_explicit(&ready, false, order)", "插桩实现代码"),
            ("atomic_store(&ready, false)", "插桩实现代码"),
            ("__atomic_store_n(&ready, false, __ATOMIC_RELEASE)", "插桩实现代码"),
            ("__atomic_store(&ready, &next, __ATOMIC_RELEASE)", "插桩实现代码"),
            ("__atomic_exchange_n(&ready, false, __ATOMIC_ACQ_REL)", "插桩实现代码"),
            ("__atomic_exchange(&ready, &next, &old, __ATOMIC_ACQ_REL)", "插桩实现代码"),
            ("__atomic_compare_exchange_n(&ready, &old, next, false, success, failure)", "插桩实现代码"),
            ("__c11_atomic_store(&ready, false, order)", "插桩实现代码"),
            ("__c11_atomic_exchange(&ready, false, order)", "插桩实现代码"),
            ("__c11_atomic_compare_exchange_strong(&ready, &old, next, success, failure)", "插桩实现代码"),
            ("ready.compare_exchange_weak(old, next)", "插桩实现代码"),
            ("对内部变量 ready 赋值为 false。", "直接调用内部函数/方法"),
            ("执行内部函数 update_ready 并检查返回码。", "直接调用内部函数/方法"),
            ("运行内部函数 update_ready 并核对错误码。", "直接调用内部函数/方法"),
            ("启动私有方法 reset_state 后检查返回值。", "直接调用内部函数/方法"),
            ("验证调用内部函数 update_ready 后的返回码。", "直接调用内部函数/方法"),
            ("执行内部方法 recover 并核验错误码。", "直接调用内部函数/方法"),
            ("发起内部函数 recover 后确认退出码。", "直接调用内部函数/方法"),
            ("比对启动私有方法 reset_state 后的状态码。", "直接调用内部函数/方法"),
        ):
            with self.subTest(value=value):
                bad = json.loads(json.dumps(MODEL))
                bad["test_cases"][0]["steps"] = [value]
                with self.assertRaisesRegex(ReportError, message):
                    render(bad)

    def test_quality_gate_allows_blackbox_simulation_and_control_semantics(self) -> None:
        allowed = json.loads(json.dumps(MODEL))
        allowed["scenarios"][0]["description"] = "模拟故障后观察业务是否自行恢复。"
        allowed["test_cases"][0]["preconditions"] = "准备一台模拟主机和可控负载。"
        allowed["test_cases"][0]["instrumentation"] = "延迟 iscsi_rsp 置为 TRUE 2 秒，以控制接收准备时间窗。"
        allowed["next_steps"] = ["运行外部恢复场景并核对业务日志和连接状态。"]
        render(allowed)

    def test_quality_gate_covers_risks_scenarios_and_case_fields(self) -> None:
        fields = (
            ("risks", 0, "title", "ready = !enabled"),
            ("risks", 0, "test_explanation", "resource_counter"),
            ("scenarios", 0, "title", "std::exchange(ready, false)"),
            ("scenarios", 0, "trigger", "set_ready(true);"),
            ("scenarios", 0, "expected", "使用 mock 单元测试验证连接"),
            ("test_cases", 0, "preconditions", "prepare_env()"),
            ("test_cases", 0, "title", "执行内部函数并检查返回码"),
            ("test_cases", 0, "expected", "iscsi_rsp = true;"),
            ("test_cases", 0, "observation", "stub 函数级返回值"),
            ("test_cases", 0, "cleanup", "connection->state"),
        )
        for collection, index, field, value in fields:
            with self.subTest(field=field, value=value):
                bad = json.loads(json.dumps(MODEL))
                bad[collection][index][field] = value
                with self.assertRaises(ReportError):
                    render(bad)

        bad = json.loads(json.dumps(MODEL))
        bad["risks"][0]["instrumentation"] = {"control": "iscsi_rsp = true;"}
        with self.assertRaisesRegex(ReportError, "插桩实现代码"):
            render(bad)

        graybox = json.loads(json.dumps(MODEL))
        graybox["risks"][0]["test_explanation"] = "将 iscsi_rsp 置位延迟 2 秒，验证提前 DATA 报文不会造成连接异常。"
        graybox["risks"][0]["propagation"] = "异常沿 recover_path() 传播后表现为新业务无法恢复。"
        graybox["test_cases"][0]["instrumentation"] = "将 iscsi_rsp 置位延迟 2 秒，不生成插桩代码。"
        render(graybox)

    def test_quality_gate_covers_remaining_final_visible_text(self) -> None:
        mutations = (
            lambda model: model.__setitem__("title", "state = (next_state)"),
            lambda model: model["task_contract"].__setitem__("测试重点", "counter[index] += 1"),
            lambda model: model["flows"][0]["steps"].__setitem__(0, "counter[index]++"),
            lambda model: model.setdefault("coverage_gaps", []).append({"risk_id": "R-1", "reason": "state = (next_state)"}),
            lambda model: model["unresolved"].__setitem__(0, "counter[index] += 1"),
            lambda model: model["next_steps"].__setitem__(0, "counter[index]++"),
        )
        for mutation in mutations:
            bad = json.loads(json.dumps(MODEL))
            mutation(bad)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ReportError, "插桩实现代码"):
                render(bad)

    def test_quality_gate_covers_visible_mermaid_and_svg_text(self) -> None:
        invalid_diagrams = (
            "flowchart LR; A[state /* hidden */ = next]-->B",
            "flowchart LR; A[counter[index[i]]++]-->B",
            "sequenceDiagram\nA->>B: state = next",
            "sequenceDiagram\nparticipant A as __atomic_store(&state, next)\nA->>B: start request",
            "sequenceDiagram\nNote over A,B: counter[index[i]]++",
            'stateDiagram-v2\nstate "state = next" as Invalid\nReady --> Invalid: enter',
            "stateDiagram-v2\nReady --> Failed: counter[index[i]] += 1",
        )
        for diagram in invalid_diagrams:
            with self.subTest(diagram=diagram):
                bad_mermaid = json.loads(json.dumps(MODEL))
                bad_mermaid["flows"][0]["mermaid"] = diagram
                with self.assertRaisesRegex(ReportError, "插桩实现代码"):
                    render(bad_mermaid)

        valid_mermaid = json.loads(json.dumps(MODEL))
        valid_mermaid["flows"][0]["mermaid"] = """sequenceDiagram
participant H as Host
participant T as Target
H->>T: Send login request
Note over H,T: Observe connection recovery
T-->>H: Login accepted"""
        render(valid_mermaid)

        for tag in ("text", "title", "desc"):
            with self.subTest(tag=tag):
                bad_svg = json.loads(json.dumps(MODEL))
                bad_svg["flows"][0]["diagram_svg"] = f'<svg xmlns="http://www.w3.org/2000/svg"><{tag}>__atomic_store_n(&amp;ready, false, order)</{tag}></svg>'
                with self.assertRaisesRegex(ReportError, "插桩实现代码"):
                    render(bad_svg)

    def test_visible_identifiers_are_gated_and_use_safe_ascii_format(self) -> None:
        for collection, field in (("risks", "id"), ("scenarios", "scenario_id"), ("test_cases", "id")):
            for invalid in ("counter[index]++", "state = next", "__atomic_store(&state, next)", "中文编号"):
                bad = json.loads(json.dumps(MODEL))
                bad[collection][0][field] = invalid
                with self.subTest(collection=collection, invalid=invalid), self.assertRaises(ReportError):
                    render(bad)

        valid = json.loads(json.dumps(MODEL))
        valid["risks"][0]["id"] = "R.safe:1_2"
        valid["scenarios"][0]["scenario_id"] = "SC.safe_1"
        valid["scenarios"][0]["risk_ids"] = ["R.safe:1_2"]
        valid["test_cases"][0]["id"] = "TC.case-1"
        valid["test_cases"][0]["risk_ids"] = ["R.safe:1_2"]
        markdown, page = render(valid)
        self.assertIn("R.safe:1_2", markdown)
        self.assertIn("SC.safe_1", page)
        self.assertIn("TC.case-1", page)

    def test_analysis_test_explanation_uses_quality_gate(self) -> None:
        invalid_values = ("", "check_ready()", "ready = true;", "使用 mock 单元测试验证状态")
        for collection in ("code_map", "flows", "branches"):
            for value in invalid_values:
                with self.subTest(collection=collection, value=value):
                    bad = json.loads(json.dumps(MODEL))
                    bad[collection][0]["test_explanation"] = value
                    with self.assertRaises(ReportError):
                        render(bad)

    def test_every_risk_test_field_uses_quality_gate_including_developer_confirm(self) -> None:
        fields = ("trigger", "propagation", "impact", "observation", "recovery", "test_explanation")
        for field in fields:
            with self.subTest(field=field):
                bad = json.loads(json.dumps(MODEL))
                bad["risks"][0][field] = "check_state()"
                with self.assertRaisesRegex(ReportError, "缺少测试语义"):
                    render(bad)

        developer = json.loads(json.dumps(MODEL))
        developer["risks"][0]["translation"] = "Developer-confirm"
        developer["risks"][0]["test_explanation"] = "check_state()"
        developer["scenarios"] = []
        developer["test_cases"] = []
        with self.assertRaisesRegex(ReportError, "风险 R-1 测试解释"):
            render(developer)

    def test_whitebox_evidence_is_not_subject_to_test_text_gate(self) -> None:
        model = json.loads(json.dumps(MODEL))
        whitebox = "void set_ready(bool value) { state /* evidence */ = next; __atomic_store_n(&ready, value, order); }"
        for collection in ("code_map", "flows", "branches"):
            model[collection][0]["source_evidence"] = whitebox
        model["risks"][0]["source_evidence"] = whitebox
        markdown, page = render(model)
        self.assertIn("__atomic_store_n", markdown)
        self.assertIn("__atomic_store_n", page)

    def test_evidence_appendix_is_collapsed(self) -> None:
        _, page = render(MODEL)
        appendix = page.split("<h2>9. 代码证据附录</h2>", 1)[1].split("<h2>10.", 1)[0]
        self.assertIn("<details><summary>源码证据</summary>", appendix)
        self.assertNotIn("<details open", appendix)

    def test_write_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            markdown, page = write_report(MODEL, tmp)
            self.assertTrue(markdown.exists())
            self.assertTrue(page.exists())
            self.assertEqual([], list(Path(tmp).glob(".report-*")))

    def test_write_report_rejects_symlink_directory_and_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = root / "external"
            external.mkdir()
            marker = external / "report.md"
            marker.write_text("outside\n", encoding="utf-8")

            linked_output = root / "linked-output"
            linked_output.symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ReportError, "符号链接"):
                write_report(MODEL, linked_output)
            self.assertEqual("outside\n", marker.read_text(encoding="utf-8"))

            output = root / "output"
            output.mkdir()
            (output / "report.md").symlink_to(marker)
            with self.assertRaisesRegex(ReportError, "符号链接"):
                write_report(MODEL, output)
            self.assertEqual("outside\n", marker.read_text(encoding="utf-8"))

    def test_write_report_rolls_back_pair_when_second_replace_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            markdown = output / "report.md"
            page = output / "report.html"
            old_markdown = b"old markdown\n"
            old_page = b"<html>old page</html>\n"
            markdown.write_bytes(old_markdown)
            page.write_bytes(old_page)

            real_replace = os.replace
            calls = 0

            def fail_second_replace(source: str | Path, destination: str | Path) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected second replace failure")
                real_replace(source, destination)

            with mock.patch("runtime.reporting.os.replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(ReportError, "原子落盘失败"):
                    write_report(MODEL, output)

            self.assertEqual(old_markdown, markdown.read_bytes())
            self.assertEqual(old_page, page.read_bytes())
            self.assertEqual([], list(output.glob(".report-*")))


if __name__ == "__main__":
    unittest.main()
