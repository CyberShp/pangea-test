"""Deterministic Markdown and interactive offline HTML report projection."""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from runtime import engine, model


class ReportError(RuntimeError):
    pass


def _list(values: list[str]) -> str:
    return "；".join(values) if values else "无"


def _evidence(rows: list[dict[str, Any]]) -> str:
    return "；".join(f"{row['path']}:{row['line_start']}-{row['line_end']}（{row['fact']}）" for row in rows) or "无"


def _refs(values: list[str]) -> str:
    return "、".join(f"[{value}](#{value})" for value in values) or "无"


def _semantic_markdown(value: dict[str, Any], family: str, title: str, fields: list[tuple[str, str]]) -> list[str]:
    out = [f"## {title}", ""]
    rows = value[family]
    if not rows:
        return [*out, "本范围没有形成该类语义对象。", ""]
    for row in rows:
        out += [f"### <a id=\"{row['id']}\"></a>{row['id']} {row['title']}", ""]
        for key, label in fields:
            item = row[key]
            text = _list(item) if isinstance(item, list) else str(item)
            out.append(f"- {label}：{text}")
        out += [f"- 关联对象：{_refs(row['related_ids'])}", f"- 代码证据：{_evidence(row['evidence'])}", ""]
    return out


def _markdown(value: dict[str, Any], review: dict[str, Any]) -> str:
    task = value["task"]
    coverage = value["coverage"]
    out = [
        f"# {task['target']} 代码分析、DFX 与测试报告", "",
        f"- Run：`{value['run_id']}`", f"- 仓库：`{task['repository']}`", f"- 目标范围：`{task['scope']}`",
        f"- Analysis revision：`{value['analysis_revision']}`", f"- Review：`{review['verdict']}`", "",
        "## 1. 结论与覆盖边界", "",
        *[f"- {item}" for item in value["summary"]],
        f"- Source coverage：冻结 {coverage['source']['files']} 个文件、{coverage['source']['lines']} 行，"
        f"frozen scope 为 `{coverage['source']['frozen_scope']}`；其中 "
        f"{coverage['source']['evidenced_lines']} 行被语义对象或 Gap 直接引用。",
        f"- Semantic coverage：{coverage['semantic']['inventory_evidenced']}/{coverage['semantic']['inventory_total']} "
        "个函数/分支 inventory 有语义或 Gap 证据。",
        f"- 说明：{coverage['semantic']['statement']}",
        f"- 应用方法：{_list(value['methods'])}", "",
        "## 2. 源码地图", "", "| 文件 | 语言 | 行数 | 函数 |", "| --- | --- | ---: | --- |",
    ]
    for file in value["source_map"]["files"]:
        out.append(f"| `{file['path']}` | {file['language']} | {file['line_count']} | {_list([row['name'] for row in file['symbols']])} |")
    sections = [
        ("behaviors", "3. Behavior", [("category", "类别"), ("description", "说明"), ("inputs", "输入"), ("outputs", "输出")]),
        ("flows", "4. Flow", [("trigger", "触发"), ("steps", "步骤"), ("result", "结果")]),
        ("branches", "5. Branch", [("condition", "条件"), ("outcomes", "不同结果")]),
        ("states", "6. State", [("from_state", "原状态"), ("event", "事件"), ("to_state", "目标状态"), ("action", "动作")]),
        ("resources", "7. Resource", [("resource", "资源"), ("acquire", "申请"), ("owner", "所有者"), ("release", "释放"), ("error_release", "异常回滚")]),
        ("concurrency", "8. Concurrency", [("contexts", "执行上下文"), ("shared_state", "共享状态"), ("coordination", "协调"), ("ordering", "时序")]),
        ("error_chains", "9. Error Chain", [("trigger", "触发"), ("propagation", "传播"), ("handling", "处理"), ("recovery", "恢复"), ("effect", "外部影响")]),
    ]
    for family, title, fields in sections:
        out += _semantic_markdown(value, family, title, fields)

    out += ["## 10. 六维 DFX", ""]
    for assessment in value["dfx"]:
        out += [f"### {assessment['dimension']}", "", assessment["summary"], ""]
        if not assessment["findings"]:
            out += ["- 当前证据没有形成独立 finding。", ""]
        for finding in assessment["findings"]:
            out += [
                f"#### <a id=\"{finding['id']}\"></a>{finding['id']} [{finding['type']}] {finding['title']}", "",
                finding["description"], "", f"- 关联对象：{_refs(finding['related_ids'])}",
                f"- 代码证据：{_evidence(finding['evidence'])}", "",
            ]

    out += ["## 11. Canonical Risk", ""]
    if not value["risks"]:
        out += ["当前证据没有确认 canonical risk。", ""]
    for risk in value["risks"]:
        out += [
            f"### <a id=\"{risk['id']}\"></a>{risk['id']} {risk['title']}", "",
            f"- DFX：{_list(risk['dfx_dimensions'])}", f"- 严重度 / 可信度：{risk['severity']} / {risk['confidence']}",
            f"- 成立条件：{risk['condition']}", f"- 传播：{risk['propagation']}", f"- 外部影响：{risk['effect']}",
            f"- 观测：{risk['observation']}", f"- 恢复/排除：{risk['recovery']}",
            f"- Testability：`{risk['testability']}`", f"- 缺失证据：{_list(risk['missing_evidence'])}",
            f"- 关联语义：{_refs(risk['related_ids'])}", f"- 代码证据：{_evidence(risk['evidence'])}", "",
        ]

    out += ["## 12. SFMEA", "", "| ID | 失效模式 | 机理 | 影响 | S/O/D | 风险 | 观测 | 建议 |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in value["sfmea"]:
        out.append(
            f"| <a id=\"{row['id']}\"></a>{row['id']} | {row['failure_mode']} | {row['mechanism']} | {row['effect']} | "
            f"{row['severity']}/{row['occurrence']}/{row['detection']} | {_refs(row['risk_ids'])} | {_list(row['observations'])} | {row['recommendation']} |"
        )

    out += ["", "## 13. 测试场景与用例", ""]
    for scenario in value["scenarios"]:
        out += [
            f"### <a id=\"{scenario['id']}\"></a>{scenario['id']} {scenario['title']}", "",
            f"- 风险：{_refs(scenario['risk_ids'])}", f"- 语义：{_refs(scenario['semantic_ids'])}",
            f"- 参数空间：{json.dumps(scenario['parameters'], ensure_ascii=False)}",
            f"- 前置：{_list(scenario['preconditions'])}", f"- 操作：{scenario['action']}",
            f"- 预期：{scenario['expected']}", f"- 观测：{scenario['observation']}", "",
        ]
    for case in value["test_cases"]:
        out += [
            f"### <a id=\"{case['id']}\"></a>{case['id']} {case['title']}", "",
            f"- 场景：{_refs([case['scenario_id']])}", f"- 风险：{_refs(case['risk_ids'])}",
            f"- 语义：{_refs(case['semantic_ids'])}", f"- 参数：{json.dumps(case['parameters'], ensure_ascii=False)}",
            f"- 前置：{_list(case['preconditions'])}", f"- 步骤：{_list(case['steps'])}",
            f"- 预期：{_list(case['expected'])}", f"- 观测：{_list(case['observations'])}",
            f"- 清理：{_list(case['cleanup'])}", "",
        ]

    out += ["## 14. Gap、风险判定与全局决策", ""]
    for gap in value["gaps"]:
        out += [
            f"- <a id=\"{gap['id']}\"></a>**{gap['id']} {gap['title']}**：{gap['reason']}；"
            f"需要：{gap['needed_evidence']}；关联：{_refs(gap['related_ids'])}；证据：{_evidence(gap['evidence'])}"
        ]
    for decision in value["risk_decisions"]:
        out.append(f"- Risk candidate `{decision['candidate_id']}`：`{decision['status']}`；{decision['reason']}；canonical risk：{decision['risk_id'] or '无'}")
    for decision in value["composition_decisions"]:
        out.append(f"- 删除 {_list(decision['object_ids'])}：{decision['reason']}")
    if not value["gaps"] and not value["risk_decisions"] and not value["composition_decisions"]:
        out.append("- 无 Gap 或删除决定。")

    metrics = review["metrics"]
    out += [
        "", "## 15. 独立 Review", "",
        "- 结构化计数："
        f"semantic={metrics['semantic_objects']}；DFX finding={metrics['dfx_findings']}；"
        f"candidate={metrics['risk_candidates']}（confirmed={metrics['confirmed_candidates']}，"
        f"dismissed={metrics['dismissed_candidates']}）；canonical risk={metrics['canonical_risks']}；"
        f"blocked={metrics['blocked_risks']}；scenario={metrics['scenarios']}；test case={metrics['test_cases']}。",
        "", review["summary"], "",
    ]
    if not review["findings"]:
        out.append("- 无 Review finding。")
    for finding in review["findings"]:
        out.append(f"- [{finding['severity']}] `{finding['code']}`：{finding['message']}（对象：{_refs(finding['object_ids'])}）")
    out.append("")
    return "\n".join(out)


def _h(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _links(values: list[str]) -> str:
    return " ".join(f'<a href="#{_h(value)}">{_h(value)}</a>' for value in values) or "无"


def _card(identifier: str, title: str, kind: str, rows: list[tuple[str, Any]], *, severity: str = "", testability: str = "") -> str:
    body = "".join(f"<dt>{_h(label)}</dt><dd>{value if isinstance(value, _Raw) else _h(value)}</dd>" for label, value in rows)
    return (
        f'<article id="{_h(identifier)}" class="card searchable" data-kind="{_h(kind)}" '
        f'data-severity="{_h(severity)}" data-testability="{_h(testability)}">'
        f'<div class="tag">{_h(kind)}</div><h3>{_h(identifier)} · {_h(title)}</h3><dl>{body}</dl></article>'
    )


class _Raw(str):
    pass


def _html(value: dict[str, Any], review: dict[str, Any], title: str) -> str:
    cards: list[str] = []
    semantic_fields = {
        "behaviors": [("类别", "category"), ("说明", "description"), ("输入", "inputs"), ("输出", "outputs")],
        "flows": [("触发", "trigger"), ("步骤", "steps"), ("结果", "result")],
        "branches": [("条件", "condition"), ("结果", "outcomes")],
        "states": [("原状态", "from_state"), ("事件", "event"), ("目标状态", "to_state"), ("动作", "action")],
        "resources": [("资源", "resource"), ("申请", "acquire"), ("所有者", "owner"), ("释放", "release"), ("异常回滚", "error_release")],
        "concurrency": [("上下文", "contexts"), ("共享状态", "shared_state"), ("协调", "coordination"), ("时序", "ordering")],
        "error_chains": [("触发", "trigger"), ("传播", "propagation"), ("处理", "handling"), ("恢复", "recovery"), ("影响", "effect")],
    }
    for family, fields in semantic_fields.items():
        for row in value[family]:
            rows = [(label, _list(row[key]) if isinstance(row[key], list) else row[key]) for label, key in fields]
            rows += [("关联", _Raw(_links(row["related_ids"]))), ("证据", _evidence(row["evidence"]))]
            cards.append(_card(row["id"], row["title"], family, rows))
    for assessment in value["dfx"]:
        if not assessment["findings"]:
            cards.append(_card("DFX-" + assessment["dimension"], assessment["dimension"], "dfx-empty", [("总结", assessment["summary"])]))
        for row in assessment["findings"]:
            cards.append(_card(row["id"], row["title"], row["type"], [
                ("维度", assessment["dimension"]), ("总结", assessment["summary"]), ("说明", row["description"]),
                ("关联", _Raw(_links(row["related_ids"]))), ("证据", _evidence(row["evidence"])),
            ]))
    for row in value["risks"]:
        cards.append(_card(row["id"], row["title"], "risk", [
            ("DFX", _list(row["dfx_dimensions"])), ("条件", row["condition"]), ("传播", row["propagation"]),
            ("影响", row["effect"]), ("观测", row["observation"]), ("恢复", row["recovery"]),
            ("缺失证据", _list(row["missing_evidence"])), ("关联", _Raw(_links(row["related_ids"]))),
        ], severity=row["severity"], testability=row["testability"]))
    for row in value["scenarios"]:
        cards.append(_card(row["id"], row["title"], "scenario", [
            ("风险", _Raw(_links(row["risk_ids"]))), ("语义", _Raw(_links(row["semantic_ids"]))),
            ("前置", _list(row["preconditions"])), ("操作", row["action"]), ("预期", row["expected"]), ("观测", row["observation"]),
        ]))
    for row in value["test_cases"]:
        cards.append(_card(row["id"], row["title"], "test-case", [
            ("场景", _Raw(_links([row["scenario_id"]]))), ("风险", _Raw(_links(row["risk_ids"]))),
            ("步骤", _list(row["steps"])), ("预期", _list(row["expected"])),
            ("观测", _list(row["observations"])), ("清理", _list(row["cleanup"])),
        ]))
    source_rows = "".join(
        f"<tr><td><code>{_h(row['path'])}</code></td><td>{_h(row['language'])}</td><td>{row['line_count']}</td>"
        f"<td>{_h(_list([item['name'] for item in row['symbols']]))}</td></tr>"
        for row in value["source_map"]["files"]
    )
    options = "".join(f'<option value="{_h(item)}">{_h(item)}</option>' for item in [*model.DFX_FINDING_TYPES, "risk", "scenario", "test-case"])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_h(title)}</title><style>
:root{{--ink:#17212b;--muted:#637083;--line:#dce3ea;--soft:#f5f7fa;--accent:#1769aa}}*{{box-sizing:border-box}}
body{{margin:0;font:15px/1.65 system-ui;color:var(--ink);background:#eef2f6}}header,main{{max-width:1180px;margin:auto}}
header{{padding:38px 24px 18px}}h1{{margin:0 0 8px;font-size:30px}}.meta{{color:var(--muted)}}.controls{{position:sticky;top:0;z-index:2;display:flex;gap:10px;padding:12px 24px;background:#fff;border-block:1px solid var(--line)}}
input,select{{padding:9px 12px;border:1px solid var(--line);border-radius:8px;background:white}}input{{flex:1}}main{{padding:22px 24px 60px}}section{{margin:0 0 30px}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:9px;border:1px solid var(--line);text-align:left}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}}.card{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px;scroll-margin-top:80px}}.card h3{{margin:4px 0 12px}}.tag{{display:inline-block;color:var(--accent);font-size:12px;font-weight:700;text-transform:uppercase}}dl{{display:grid;grid-template-columns:100px 1fr;gap:6px 10px;margin:0}}dt{{color:var(--muted)}}dd{{margin:0;overflow-wrap:anywhere}}a{{color:var(--accent)}}.hidden{{display:none}}
</style></head><body><header><h1>{_h(title)}</h1><div class="meta">Run {_h(value['run_id'])} · revision {value['analysis_revision']} · Review {_h(review['verdict'])}</div><p>{_h(value['coverage']['semantic']['statement'])}</p></header>
<div class="controls"><input id="q" placeholder="搜索对象、证据、步骤…"><select id="kind"><option value="">全部类型</option>{options}</select><select id="testability"><option value="">全部 testability</option><option>blackbox_ready</option><option>graybox_ready</option><option>blocked</option></select></div>
<main><section><h2>源码与方法</h2><p>方法：{_h(_list(value['methods']))}。Semantic inventory：{value['coverage']['semantic']['inventory_evidenced']}/{value['coverage']['semantic']['inventory_total']}。</p><table><thead><tr><th>文件</th><th>语言</th><th>行数</th><th>函数</th></tr></thead><tbody>{source_rows}</tbody></table></section>
<section><h2>语义、DFX、风险与测试</h2><div class="grid">{''.join(cards)}</div></section>
<section><h2>独立 Review</h2><p>结构化计数：semantic={review['metrics']['semantic_objects']}；DFX finding={review['metrics']['dfx_findings']}；candidate={review['metrics']['risk_candidates']}（confirmed={review['metrics']['confirmed_candidates']}，dismissed={review['metrics']['dismissed_candidates']}）；canonical risk={review['metrics']['canonical_risks']}；blocked={review['metrics']['blocked_risks']}；scenario={review['metrics']['scenarios']}；test case={review['metrics']['test_cases']}。</p><p>{_h(review['summary'])}</p><ul>{''.join(f'<li>[{_h(row["severity"])}] {_h(row["code"])}：{_h(row["message"])}</li>' for row in review['findings']) or '<li>无 finding</li>'}</ul></section></main>
<script>const cards=[...document.querySelectorAll('.searchable')];function filter(){{const q=document.querySelector('#q').value.toLowerCase(),k=document.querySelector('#kind').value,t=document.querySelector('#testability').value;cards.forEach(c=>c.classList.toggle('hidden',!!((q&&!c.textContent.toLowerCase().includes(q))||(k&&c.dataset.kind!==k)||(t&&c.dataset.testability!==t))))}}document.querySelectorAll('input,select').forEach(x=>x.addEventListener('input',filter));</script></body></html>'''


def publish(root: Path, run_id: str) -> dict[str, str]:
    run = engine.load_run(root, run_id)
    if run["state"] not in {"reviewing", "completed"} or run["review"]["verdict"] != "pass":
        raise ReportError("current analysis revision does not have a pass review")
    directory = engine.run_dir(root, run_id)
    value = engine.read_json(directory / "analysis.json")
    review = engine.read_json(directory / "review.json")
    if review["analysis_revision"] != value["analysis_revision"]:
        raise ReportError("review does not bind current analysis revision")
    markdown = _markdown(value, review)
    title = f"{value['task']['target']} 代码分析、DFX 与测试报告"
    html_value = _html(value, review, title)
    reports = root.resolve() / "pangea-data" / "reports" / run_id
    reports.mkdir(parents=True, exist_ok=True)
    markdown_path, html_path = reports / "report.md", reports / "report.html"
    markdown_path.write_text(markdown, encoding="utf-8")
    html_path.write_text(html_value, encoding="utf-8")
    if run["state"] == "reviewing":
        engine.commit_stage(
            root, run_id, expected="reviewing", target="completed",
            changes={"report": {
                "markdown": str(markdown_path.relative_to(root.resolve())),
                "html": str(html_path.relative_to(root.resolve())),
                "analysis_revision": value["analysis_revision"],
            }},
            event="report_published",
        )
    return {"markdown": str(markdown_path), "html": str(html_path)}
