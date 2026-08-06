"""Deterministic Markdown and standalone HTML report renderer."""
from __future__ import annotations

import html
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


class ReportError(RuntimeError):
    pass


SYMBOL_ONLY = re.compile(r"^(?:[A-Za-z_]\w*(?:::\w+)*(?:\([^)]*\))?|[A-Za-z_]\w*(?:->|\.)\w+)\s*[。.!;]*$")
IMPLEMENTATION_CODE = re.compile(
    r"```|#\s*include|\b(?:static\s+)?(?:void|int|bool|char|size_t)\s+\w+\s*\([^)]*\)\s*\{|"
    r"\b(?:if|for|while)\s*\([^)]*\)\s*\{",
    re.I,
)
IMPLEMENTATION_STATEMENT = re.compile(
    r"\b[A-Za-z_]\w*(?:(?:::|->|\.)[A-Za-z_]\w*)*\s*"
    r"(?:\([^;{}]*\)|(?:=|\+=|-=|\|=|&=)[^;{}]+)\s*;|"
    r"\b(?:const\s+)?(?:void|int|bool|char|size_t|auto)\s+\w+[^;{}]*;",
    re.I,
)
IMPLEMENTATION_BLOCK = re.compile(
    r"\{[^{}]*(?:;|\b(?:return|if|for|while)\b|(?:->|\.)\s*(?:store|exchange|fetch_add))[^{}]*\}",
    re.I,
)
_LVALUE = (
    r"(?:\(*\s*\**\s*)?[A-Za-z_]\w*"
    r"(?:(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)|(?:\s*\[[^\]\r\n]+\]))*\s*\)*"
    r"(?:(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)|(?:\s*\[[^\]\r\n]+\]))*"
)
RAW_IMPLEMENTATION = re.compile(
    rf"(?<![\w]){_LVALUE}\s*(?:=(?!=)|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<=|>>=)\s*[^=;{{}}\r\n]+|"
    rf"(?:\+\+|--)\s*{_LVALUE}|"
    rf"(?<![\w]){_LVALUE}\s*(?:\+\+|--)|"
    r"\b(?:__atomic_(?:(?:store|exchange)(?:_n)?|compare_exchange(?:_n)?)|"
    r"__c11_atomic_(?:store|exchange|compare_exchange_(?:strong|weak))|__sync_(?:lock_test_and_set|swap))\s*\([^)]*\)|"
    r"\b(?:std::)?(?:atomic_)?(?:store|exchange|compare_exchange_(?:weak|strong))(?:_explicit|_n)?\s*\([^)]*\)|"
    r"\b[A-Za-z_]\w*(?:(?:->|\.)[A-Za-z_]\w*)*(?:->|\.)(?:store|exchange|compare_exchange_weak|compare_exchange_strong|fetch_add|fetch_sub)(?:_explicit)?\s*\([^)]*\)", re.I,
)
TEST_DOUBLE = re.compile(
    r"(?<![A-Za-z])(?:mock|stub|fake|spy)(?![A-Za-z])|"
    r"模拟对象|桩函数|测试替身|替身对象",
    re.I,
)
WHITEBOX_DIRECT_ASSERTION = re.compile(
    r"(?:(?:直接\s*)?调用.{0,80}(?:内部|私有)(?:函数|方法).{0,80}(?:断言|校验|验证).{0,80}"
    r"(?:返回值|执行(?:成功|失败)?|内部变量|成员变量|局部变量|成功|失败)|"
    r"(?:断言|校验|验证).{0,40}(?:内部|私有)(?:函数|方法).{0,80}"
    r"(?:返回值|执行(?:成功|失败)?|内部变量|成员变量|局部变量|成功|失败))",
)
WHITEBOX_INTERNAL_MUTATION = re.compile(
    r"(?:对|给)(?:内部|私有)(?:变量|成员变量|状态).{0,80}(?:进行)?赋值|"
    r"(?:内部|私有)(?:变量|成员变量)\s*(?:被)?赋值|"
    r"(?:执行|调用).{0,80}(?:内部|私有)(?:函数|方法).{0,80}"
    r"(?:检查|断言|校验|验证).{0,40}返回(?:码|值)|"
    r"(?:检查|断言|校验|验证).{0,40}(?:内部|私有)(?:函数|方法).{0,80}返回(?:码|值)",
)
WHITEBOX_INTERNAL_RESULT = re.compile(
    r"(?=.{0,200}(?:内部|私有)(?:函数|方法))"
    r"(?=.{0,200}(?:运行|执行|调用|启动|触发|激活|发起))"
    r"(?=.{0,200}(?:核对|核验|检查|断言|校验|验证|确认|比对|审查))"
    r"(?=.{0,200}(?:返回码|错误码|返回值|状态码|退出码))",
    re.S,
)
VISIBLE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$")
SEVERITIES = ("Low", "Medium", "High", "Critical")
TRANSLATIONS = ("Blackbox-ready", "Graybox-ready", "Developer-confirm")
SECTION_TITLES = (
    "任务契约与覆盖边界", "代码地图", "关键业务流程", "异常分支及进入方式", "全量风险账本",
    "测试场景", "测试用例", "风险与用例覆盖映射", "代码证据附录", "未闭环项与下一步建议",
)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value: Any, default: str = "未说明") -> str:
    if isinstance(value, list):
        return "、".join(str(item) for item in value) or default
    if isinstance(value, dict):
        return "；".join(f"{key}: {_text(item)}" for key, item in value.items()) or default
    return str(value) if value not in (None, "") else default


def _identifier(item: dict[str, Any], canonical: str, legacy: str) -> str:
    value = item.get(canonical, item.get(legacy))
    if not isinstance(value, str) or not value:
        raise ReportError(f"条目缺少 {canonical}")
    _test_text(value, canonical, allow_symbol=True)
    if VISIBLE_ID.fullmatch(value) is None:
        raise ReportError(f"{canonical} 必须是安全 ASCII 标识符")
    return value


def _normalize_implementation_text(text: str) -> str:
    normalized = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    normalized = re.sub(r"(?<!:)//[^\r\n]*(?:\r?\n|$)", " ", normalized)
    innermost_subscript = re.compile(r"\[[^\[\]\r\n]*\]")
    while innermost_subscript.search(normalized):
        normalized = innermost_subscript.sub(".__index", normalized)
    return normalized


def _test_text(value: Any, context: str, *, required: bool = True, allow_symbol: bool = False) -> None:
    """Reject implementation-shaped content while preserving gray-box control prose."""
    text = _text(value, "").strip()
    if not text:
        if required:
            raise ReportError(f"{context} 缺少测试语义")
        return
    code_text = _normalize_implementation_text(text)
    if (IMPLEMENTATION_CODE.search(code_text) or IMPLEMENTATION_STATEMENT.search(code_text)
            or RAW_IMPLEMENTATION.search(code_text) or IMPLEMENTATION_BLOCK.search(code_text)):
        raise ReportError(f"{context} 不得包含插桩实现代码")
    if TEST_DOUBLE.search(text):
        raise ReportError(f"{context} 不得包含 Mock/Stub/Fake/Spy 等测试替身")
    if (WHITEBOX_DIRECT_ASSERTION.search(text) or WHITEBOX_INTERNAL_MUTATION.search(text)
            or WHITEBOX_INTERNAL_RESULT.search(text)):
        raise ReportError(f"{context} 不得包含直接调用内部函数/方法并断言返回值或内部变量的白盒用例")
    if not allow_symbol and SYMBOL_ONLY.fullmatch(text):
        raise ReportError(f"{context} 缺少测试语义")


def _mermaid_visible_texts(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    labels: list[str] = []
    visible_lines = [line.split("%%", 1)[0] for line in value.splitlines()
                     if not line.lstrip().startswith("%%")]
    visible_source = "\n".join(visible_lines)
    for opener, closer in (("[", "]"), ("(", ")"), ("{", "}")):
        stack: list[int] = []
        for position, character in enumerate(visible_source):
            if character == opener:
                stack.append(position)
            elif character == closer and stack:
                start = stack.pop()
                labels.append(visible_source[start + 1:position])
    labels.extend(re.findall(r"\|([^|]+)\|", visible_source))
    in_note = False
    for raw_line in visible_lines:
        line = raw_line.strip()
        if not line:
            continue
        if in_note:
            if re.fullmatch(r"(?i)end\s+note", line):
                in_note = False
            else:
                labels.append(line)
            continue
        note = re.match(r"(?i)^note\s+(?:left|right)\s+of\s+[^:]+\s*:\s*(.+)$|^note\s+over\s+[^:]+\s*:\s*(.+)$", line)
        if note:
            labels.append(next(group for group in note.groups() if group is not None))
            continue
        if re.match(r"(?i)^note\s+(?:left|right)\s+of\s+\S+\s*$|^note\s+over\s+[^:]+$", line):
            in_note = True
            continue
        participant = re.match(r"(?i)^(?:participant|actor)\s+(.+)$", line)
        if participant:
            declaration = participant.group(1)
            labels.append(declaration.rsplit(" as ", 1)[-1])
            continue
        state = re.match(r"(?i)^state\s+[^:]+\s*:\s*(.+)$", line)
        if state:
            labels.append(state.group(1))
        if ":" in line:
            prefix, message = line.split(":", 1)
            if re.search(r"(?:--?|==?)[)>xo]+", prefix):
                labels.append(message)
        subgraph = re.match(r"(?i)^subgraph\s+(?:[A-Za-z_]\w*\s*\[)?([^\]]+)", line)
        if subgraph:
            labels.append(subgraph.group(1))
        title = re.match(r"(?i)^(?:title|accTitle|accDescr)\s*:?[ \t]+(.+)$", line)
        if title:
            labels.append(title.group(1))
        labels.extend(re.findall(r'''["']([^"']+)["']''', line))
    normalized = [html.unescape(label.strip().strip("\"'")) for label in labels]
    return list(dict.fromkeys(label for label in normalized if label))


def _validate_analysis_entries(model: dict[str, Any]) -> None:
    for key, label in (("code_map", "代码地图"), ("flows", "代码流程"), ("branches", "代码分支")):
        for index, item in enumerate(model.get(key, []), 1):
            explanation = item.get("test_explanation") if isinstance(item, dict) else None
            title = item.get("title") if isinstance(item, dict) else None
            _test_text(title, f"{label}条目 {index} 标题", required=False)
            _test_text(explanation, f"{label}条目 {index} test_explanation")
            if isinstance(item, dict):
                for step_index, step in enumerate(item.get("steps", []), 1):
                    text = step.get("label", step.get("description", "")) if isinstance(step, dict) else step
                    _test_text(text, f"{label}条目 {index} 步骤 {step_index}")
                for text_index, text in enumerate(_mermaid_visible_texts(item.get("mermaid")), 1):
                    _test_text(text, f"{label}条目 {index} Mermaid 文字 {text_index}", allow_symbol=True)
                sanitized_svg = _safe_svg(str(item["diagram_svg"])) if item.get("diagram_svg") else ""
                if sanitized_svg:
                    root = ET.fromstring(sanitized_svg)
                    for node_index, node in enumerate(root.iter(), 1):
                        if node.tag.rsplit("}", 1)[-1] in {"text", "title", "desc"}:
                            _test_text("".join(node.itertext()), f"{label}条目 {index} SVG 文字 {node_index}",
                                       required=False, allow_symbol=True)


def _normalize_risk(item: dict[str, Any]) -> dict[str, Any]:
    evidence = item.get("evidence")
    if evidence is None and item.get("source_evidence"):
        evidence = [{"description": item["source_evidence"]}]
    return {
        **item,
        "risk_id": _identifier(item, "risk_id", "id"),
        "translation_status": item.get("translation_status", item.get("translation", "Developer-confirm")),
        "external_impact": item.get("external_impact", item.get("impact", "")),
        "test_explanation": item.get("test_explanation", item.get("description", "")),
        "evidence": _list(evidence),
        "instrumentation_request": item.get("instrumentation_request", item.get("instrumentation")),
        "dfx": [str(value) for value in _list(item.get("dfx"))],
    }


def normalize_model(model: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(model, dict) or not model.get("title"):
        raise ReportError("报告模型必须包含 title")
    normalized = dict(model)
    normalized["risks"] = [_normalize_risk(item) for item in model.get("risks", [])]
    normalized["scenarios"] = [dict(item) for item in model.get("scenarios", model.get("test_scenarios", []))]
    normalized["test_cases"] = [dict(item) for item in model.get("test_cases", [])]
    normalized["coverage_gaps"] = [dict(item) if isinstance(item, dict) else {"reason": str(item)} for item in model.get("coverage_gaps", [])]
    normalized["unresolved"] = model.get("unresolved", model.get("open_items", []))
    normalized["next_steps"] = model.get("next_steps", [])
    return normalized


def validate_model(model: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_model(model)
    _test_text(normalized["title"], "报告标题")
    for field in ("summary", "scope"):
        _test_text(normalized.get(field), f"报告 {field}", required=False)
    _test_text(normalized.get("task_contract"), "任务契约", required=False, allow_symbol=True)
    for field, label in (("unresolved", "未闭环项"), ("next_steps", "下一步建议")):
        for index, item in enumerate(_list(normalized.get(field)), 1):
            _test_text(item, f"{label} {index}")
    _validate_analysis_entries(normalized)
    risks = {risk["risk_id"]: risk for risk in normalized["risks"]}
    if len(risks) != len(normalized["risks"]):
        raise ReportError("risk_id 必须唯一")
    scenario_refs = {str(risk_id) for scenario in normalized["scenarios"] for risk_id in scenario.get("risk_ids", [])}
    unknown_scenario_refs = scenario_refs - set(risks)
    if unknown_scenario_refs:
        raise ReportError(f"测试场景引用了未知风险: {sorted(unknown_scenario_refs)}")
    for index, scenario in enumerate(normalized["scenarios"], 1):
        scenario["scenario_id"] = _identifier(scenario, "scenario_id", "id")
        _test_text(scenario.get("title"), f"测试场景 {index} 标题", required=False)
        for field, label in (("description", "场景说明"), ("trigger", "场景触发"), ("expected", "场景预期")):
            _test_text(scenario.get(field, scenario.get("test_explanation") if field == "description" else None),
                       f"测试场景 {index} {label}")
        for risk_id in map(str, scenario.get("risk_ids", [])):
            if risks[risk_id]["translation_status"] == "Developer-confirm":
                raise ReportError(f"Developer-confirm 风险不得生成可执行场景: {risk_id}")
    case_refs: set[str] = set()
    for case in normalized["test_cases"]:
        case_id = _identifier(case, "case_id", "id")
        case["case_id"] = case_id
        _test_text(case.get("title"), f"用例 {case_id} 标题", required=False)
        for field, label in (("preconditions", "前置条件"), ("expected", "预期结果"),
                             ("observation", "观测方式"), ("cleanup", "清理/恢复")):
            _test_text(case.get(field), f"用例 {case_id} {label}")
        _test_text(case.get("instrumentation"), f"用例 {case_id} 插桩需求", required=False)
        steps = case.get("steps", [])
        if not steps:
            raise ReportError(f"用例缺少步骤: {case_id}")
        for step in steps:
            text = step if isinstance(step, str) else step.get("description", "")
            _test_text(text, f"用例步骤: {case_id}")
        for risk_id in map(str, case.get("risk_ids", [])):
            if risk_id not in risks:
                raise ReportError(f"用例 {case_id} 引用了未知风险 {risk_id}")
            if risks[risk_id]["translation_status"] == "Developer-confirm":
                raise ReportError(f"Developer-confirm 风险不得生成可执行用例: {risk_id}")
            case_refs.add(risk_id)
    gaps: dict[str, dict[str, Any]] = {}
    for index, gap in enumerate(normalized["coverage_gaps"], 1):
        risk_id = str(gap.get("risk_id", ""))
        if not risk_id or risk_id not in risks:
            raise ReportError(f"coverage gap {index} 缺少有效 risk_id")
        _test_text(gap.get("reason"), f"coverage gap {risk_id} 原因")
        gaps[risk_id] = gap
    for risk in normalized["risks"]:
        risk_id = risk["risk_id"]
        if risk["severity"] not in SEVERITIES:
            raise ReportError(f"风险严重度无效: {risk_id}")
        if risk["translation_status"] not in TRANSLATIONS:
            raise ReportError(f"风险转译状态无效: {risk_id}")
        _test_text(risk.get("title"), f"风险 {risk_id} 标题", required=False)
        for field, label in (("trigger", "触发条件"), ("propagation", "传播路径"),
                             ("external_impact", "外部影响"), ("observation", "观测方式"),
                             ("recovery", "恢复判据"), ("test_explanation", "测试解释")):
            _test_text(risk.get(field), f"风险 {risk_id} {label}")
        _test_text(risk.get("instrumentation_request"), f"风险 {risk_id} 插桩需求", required=False)
        _test_text(risk.get("coverage_gap"), f"风险 {risk_id} coverage_gap", required=False)
        if risk["translation_status"] != "Developer-confirm" and risk_id not in scenario_refs | case_refs:
            if not _coverage_gap_text(risk, gaps.get(risk_id)):
                raise ReportError(f"可转译风险缺少场景/用例或 coverage gap: {risk_id}")
    return normalized


def _coverage_gap_text(risk: dict[str, Any], mapped_gap: dict[str, Any] | None = None) -> str:
    values = [risk.get("coverage_gap"), mapped_gap.get("reason") if mapped_gap else None]
    return "；".join(dict.fromkeys(str(value).strip() for value in values if isinstance(value, str) and value.strip()))


def _coverage_gap_map(model: dict[str, Any]) -> dict[str, str]:
    mapped = {str(gap["risk_id"]): gap for gap in model["coverage_gaps"] if gap.get("risk_id")}
    return {risk["risk_id"]: _coverage_gap_text(risk, mapped.get(risk["risk_id"])) for risk in model["risks"]}


def render(model: dict[str, Any]) -> tuple[str, str]:
    normalized = validate_model(model)
    markdown = _markdown(normalized)
    return markdown, _html(normalized)


def _safe_output_directory(output_dir: str | Path) -> Path:
    output = Path(output_dir).absolute()
    existing = output
    while not existing.exists() and not existing.is_symlink():
        existing = existing.parent
    if existing.is_symlink():
        raise ReportError(f"报告输出路径不得包含符号链接: {existing}")
    output.mkdir(parents=True, exist_ok=True)
    try:
        mode = output.lstat().st_mode
    except OSError as exc:
        raise ReportError(f"报告输出目录不可用: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ReportError("报告输出路径必须是非符号链接目录")
    return output


def _atomic_text_pair(output: Path, values: tuple[tuple[str, str], ...]) -> tuple[Path, ...]:
    temporaries: list[Path] = []
    backups: dict[Path, Path] = {}
    destinations = tuple(output / name for name, _ in values)
    existed = tuple(destination.exists() for destination in destinations)
    commit_started = False

    def staged_file(content: str | bytes, prefix: str) -> Path:
        payload = content.encode("utf-8") if isinstance(content, str) else content
        with tempfile.NamedTemporaryFile("wb", dir=output, prefix=prefix, delete=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            return Path(handle.name)

    try:
        for destination in destinations:
            if destination.is_symlink():
                raise ReportError(f"报告文件不得是符号链接: {destination.name}")
            if destination.exists() and not destination.is_file():
                raise ReportError(f"报告目标不是普通文件: {destination.name}")
        if any(existed) and not all(existed):
            raise ReportError("调用前报告输出必须是完整报告对或两者均不存在")
        if all(existed):
            for destination in destinations:
                backups[destination] = staged_file(destination.read_bytes(), ".report-backup-")
        for _, content in values:
            temporaries.append(staged_file(content, ".report-new-"))
        commit_started = True
        for temporary, destination in zip(temporaries, destinations):
            if destination.is_symlink():
                raise ReportError(f"报告文件不得是符号链接: {destination.name}")
            os.replace(temporary, destination)
        return destinations
    except (OSError, ReportError) as exc:
        rollback_failed = False
        if commit_started and backups:
            for destination in destinations:
                try:
                    os.replace(backups[destination], destination)
                except OSError:
                    rollback_failed = True
                    break
        elif commit_started:
            for destination in destinations:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    rollback_failed = True
        if rollback_failed:
            for destination in destinations:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
        raise ReportError(f"报告原子落盘失败: {exc}") from exc
    finally:
        for temporary in [*temporaries, *backups.values()]:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def write_report(model: dict[str, Any], output_dir: str | Path) -> tuple[Path, Path]:
    output = _safe_output_directory(output_dir)
    markdown, page = render(model)
    md, html_path = _atomic_text_pair(output, (("report.md", markdown), ("report.html", page)))
    return md, html_path


def _contract_rows(model: dict[str, Any]) -> list[tuple[str, str]]:
    contract = model.get("task_contract", {})
    rows = [(str(key), _text(value)) for key, value in contract.items()] if isinstance(contract, dict) else []
    return rows or [("任务摘要", _text(model.get("summary"))), ("覆盖边界", _text(model.get("scope")))]


def _entry_evidence(item: dict[str, Any]) -> str:
    return _text(item.get("source_evidence", item.get("evidence")), "未提供")


def _markdown(model: dict[str, Any]) -> str:
    out = [f"# {model['title']}", ""]
    out += [f"## 1. {SECTION_TITLES[0]}"] + [f"- {key}：{value}" for key, value in _contract_rows(model)] + [""]
    for number, (key, title) in enumerate((("code_map", SECTION_TITLES[1]), ("flows", SECTION_TITLES[2]), ("branches", SECTION_TITLES[3])), 2):
        out += [f"## {number}. {title}"]
        entries = model.get(key, [])
        if not entries:
            out += ["未提供。", ""]
        for raw in entries:
            item = raw if isinstance(raw, dict) else {"title": str(raw)}
            out += [f"### {item.get('title', '未命名')}", f"测试解释：{_text(item.get('test_explanation'))}", ""]
            if key == "flows":
                out += _flow_markdown(item)
            out += ["<details><summary>源码证据</summary>", "", _entry_evidence(item), "", "</details>", ""]
    out += [f"## 5. {SECTION_TITLES[4]}", ""]
    case_map = _risk_case_map(model)
    for risk in model["risks"]:
        rid = risk["risk_id"]
        out += [f"### <a id=\"risk-{rid}\"></a>{rid} {risk.get('title', '未命名')}",
                f"- 严重度：{risk.get('severity')}；可信度：{risk.get('confidence', '未说明')}",
                f"- DFX：{_text(risk.get('dfx'))}；转译状态：{risk['translation_status']}",
                f"- 测试解释：{risk['test_explanation']}",
                f"- 触发与传播：{_text(risk.get('trigger'))} -> {_text(risk.get('propagation'))}",
                f"- 外部影响：{risk['external_impact']}",
                f"- 观测与恢复：{_text(risk.get('observation'))}；{_text(risk.get('recovery'))}",
                f"- 关联场景/用例：{_md_links(case_map[rid])}", ""]
        if risk.get("coverage_gap"):
            out += [f"- 覆盖缺口：{risk['coverage_gap']}", ""]
        if risk.get("instrumentation_request"):
            out += [f"- 插桩需求（仅控制语义，不生成代码）：{_text(risk['instrumentation_request'])}", ""]
    out += [f"## 6. {SECTION_TITLES[5]}", ""]
    for scenario in model["scenarios"]:
        sid = str(scenario.get("scenario_id", scenario.get("id", "SC-未编号")))
        out += [f"### <a id=\"scenario-{sid}\"></a>{sid} {scenario.get('title', '未命名')}",
                f"- 关联风险：{_md_risk_links(scenario.get('risk_ids', []))}",
                f"- 场景说明：{_text(scenario.get('description', scenario.get('test_explanation')))}",
                f"- 触发与判据：{_text(scenario.get('trigger'))}；{_text(scenario.get('expected'))}", ""]
    if not model["scenarios"]: out += ["未单独定义测试场景。", ""]
    out += [f"## 7. {SECTION_TITLES[6]}", ""]
    for case in model["test_cases"]:
        cid = case["case_id"]
        out += [f"### <a id=\"case-{cid}\"></a>{cid} {case.get('title', '未命名')}", f"- 关联风险：{_md_risk_links(case.get('risk_ids', []))}",
                f"- 前置条件：{_text(case.get('preconditions'))}", "- 操作步骤："]
        out += [f"  {index}. {step if isinstance(step, str) else step.get('description', '')}" for index, step in enumerate(case["steps"], 1)]
        out += [f"- 预期结果：{_text(case.get('expected'))}", f"- 观测方式：{_text(case.get('observation'))}", f"- 清理/恢复：{_text(case.get('cleanup'))}", ""]
        if case.get("instrumentation"):
            out += [f"- 插桩需求（仅控制语义，不生成代码）：{_text(case['instrumentation'])}", ""]
    if not model["test_cases"]: out += ["无可执行测试用例。", ""]
    out += [f"## 8. {SECTION_TITLES[7]}", "", "| 风险 | 转译状态 | 场景/用例 | 覆盖结论 |", "| --- | --- | --- | --- |"]
    gaps = _coverage_gap_map(model)
    for risk in model["risks"]:
        rid = risk["risk_id"]; links = case_map[rid]; gap = gaps[rid]
        conclusion = f"缺口：{gap}" if gap else ("已覆盖" if links else "待开发确认")
        out += [f"| [{rid}](#risk-{rid}) | {risk['translation_status']} | {_md_links(links)} | {conclusion} |"]
    out += ["", f"## 9. {SECTION_TITLES[8]}", ""]
    evidence_entries = _collect_evidence(model)
    for reference, title, evidence in evidence_entries:
        out += [f"### {reference} {title}", evidence, ""]
    if not evidence_entries: out += ["未提供代码证据。", ""]
    out += [f"## 10. {SECTION_TITLES[9]}", "", "### 未闭环项", _bullet_text(model["unresolved"]), "", "### 下一步建议", _bullet_text(model["next_steps"]), ""]
    return "\n".join(out)


def _flow_markdown(item: dict[str, Any]) -> list[str]:
    steps = [str(step.get("label", step.get("description", ""))) if isinstance(step, dict) else str(step) for step in item.get("steps", [])]
    out = ["文字流程：" + " -> ".join(steps), ""] if steps else []
    if item.get("mermaid"):
        out += ["> Mermaid 未配置离线渲染器，以下保留源码；文字流程仍是正式降级表示。", "", "```mermaid", str(item["mermaid"]), "```", ""]
    return out


def _risk_case_map(model: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    mapping = {risk["risk_id"]: [] for risk in model["risks"]}
    for scenario in model["scenarios"]:
        sid = str(scenario.get("scenario_id", scenario.get("id", "SC-未编号")))
        for rid in map(str, scenario.get("risk_ids", [])):
            if rid in mapping: mapping[rid].append(("scenario", sid))
    for case in model["test_cases"]:
        for rid in map(str, case.get("risk_ids", [])):
            if rid in mapping: mapping[rid].append(("case", case["case_id"]))
    return mapping


def _md_links(links: list[tuple[str, str]]) -> str:
    return "、".join(f"[{'场景' if kind == 'scenario' else '用例'} {item}](#{kind}-{item})" for kind, item in links) or "无"


def _md_risk_links(risk_ids: list[Any]) -> str:
    return "、".join(f"[风险 {rid}](#risk-{rid})" for rid in risk_ids) or "未关联"


def _evidence_markdown(entries: list[Any]) -> str:
    if not entries: return "未提供。"
    return "\n".join(f"- {_text(entry)}" for entry in entries)


def _collect_evidence(model: dict[str, Any]) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for section, label in (("code_map", "代码地图"), ("flows", "关键流程"), ("branches", "异常分支")):
        for index, raw in enumerate(model.get(section, []), 1):
            item = raw if isinstance(raw, dict) else {"title": str(raw)}
            evidence = _entry_evidence(item)
            if evidence != "未提供":
                entries.append((f"{label}-{index}", str(item.get("title", "未命名")), evidence))
    for risk in model["risks"]:
        if risk["evidence"]:
            entries.append((risk["risk_id"], str(risk.get("title", "未命名")), _evidence_markdown(risk["evidence"])))
    return entries


def _bullet_text(value: Any) -> str:
    items = _list(value)
    return "\n".join(f"- {_text(item)}" for item in items) if items else "- 无"


def _html(model: dict[str, Any]) -> str:
    case_map = _risk_case_map(model)
    risk_by_id = {risk["risk_id"]: risk for risk in model["risks"]}
    dfx = sorted({item for risk in model["risks"] for item in risk["dfx"]})
    options = lambda values: "".join(f'<option value="{html.escape(value)}">{html.escape(value)}</option>' for value in values)
    sections = [
        _html_contract(model),
        _html_analysis(2, SECTION_TITLES[1], model.get("code_map", []), False),
        _html_analysis(3, SECTION_TITLES[2], model.get("flows", []), True),
        _html_analysis(4, SECTION_TITLES[3], model.get("branches", []), False),
        '<section><h2>5. 全量风险账本</h2>' + "".join(_html_risk(risk, case_map[risk["risk_id"]]) for risk in model["risks"]) + '</section>',
        _html_scenarios(model["scenarios"], risk_by_id),
        '<section><h2>7. 测试用例</h2>' + ("".join(_html_case(case, risk_by_id) for case in model["test_cases"]) or '<p>无可执行测试用例。</p>') + '</section>',
        _html_coverage(model, case_map),
        _html_evidence(model),
        _html_next(model),
    ]
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(str(model['title']))}</title>
<style>body{{font:16px system-ui,sans-serif;line-height:1.6;max-width:1120px;margin:32px auto;padding:0 20px;color:#17212b}}header{{border-bottom:2px solid #157a6e}}input,select{{padding:7px;margin:3px}}article{{border:1px solid #ccd6d3;padding:16px;margin:12px 0;border-radius:5px}}.tag{{font-size:13px;background:#e8f1ef;padding:2px 6px;border-radius:3px}}details{{margin-top:8px;background:#f6f8f7;padding:8px}}.hidden{{display:none!important}}pre{{white-space:pre-wrap}}a{{color:#076d61}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd6d3;padding:8px;text-align:left}}.flow{{display:flex;gap:8px;align-items:center;overflow:auto;padding:8px 0}}.flow-step{{border:1px solid #157a6e;padding:8px;min-width:110px}}.arrow{{font-weight:bold}}svg{{max-width:100%;height:auto}}</style></head><body>
<header><h1>{html.escape(str(model['title']))}</h1><input id="search" placeholder="搜索报告"><select id="severity"><option value="">全部严重度</option>{options(SEVERITIES)}</select><select id="dfx"><option value="">全部 DFX</option>{options(dfx)}</select><select id="translation"><option value="">全部转译状态</option>{options(TRANSLATIONS)}</select></header><main>{''.join(sections)}</main>
<script>const q=id=>document.getElementById(id);function filt(){{let s=q('search').value.toLowerCase(),a=q('severity').value,b=q('dfx').value,c=q('translation').value;document.querySelectorAll('[data-filterable]').forEach(x=>{{let hide=(a&&!x.dataset.severity.includes(a))||(b&&!x.dataset.dfx.includes(b))||(c&&!x.dataset.translation.includes(c))||(s&&!x.innerText.toLowerCase().includes(s));x.classList.toggle('hidden',!!hide)}})}}['search','severity','dfx','translation'].forEach(x=>q(x).addEventListener('input',filt));</script></body></html>'''


def _html_contract(model: dict[str, Any]) -> str:
    rows = "".join(f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in _contract_rows(model))
    return f"<section><h2>1. {SECTION_TITLES[0]}</h2><table>{rows}</table></section>"


def _html_analysis(number: int, title: str, entries: list[Any], flow: bool) -> str:
    cards = []
    for raw in entries:
        item = raw if isinstance(raw, dict) else {"title": str(raw)}
        diagram = _flow_diagram(item) if flow else ""
        cards.append(f'<article><h3>{html.escape(str(item.get("title", "未命名")))}</h3><p><b>测试解释：</b>{html.escape(_text(item.get("test_explanation")))}</p>{diagram}<details><summary>源码证据</summary><pre>{html.escape(_entry_evidence(item))}</pre></details></article>')
    return f"<section><h2>{number}. {html.escape(title)}</h2>{''.join(cards) or '<p>未提供。</p>'}</section>"


def _flow_diagram(item: dict[str, Any]) -> str:
    steps = [str(step.get("label", step.get("description", ""))) if isinstance(step, dict) else str(step) for step in item.get("steps", [])]
    fallback = '<div class="flow" aria-label="文字流程图">' + '<span class="arrow">→</span>'.join(f'<span class="flow-step">{html.escape(step)}</span>' for step in steps) + '</div>' if steps else ""
    diagram = ""
    if item.get("diagram_svg"):
        svg = _safe_svg(str(item["diagram_svg"]))
        if svg:
            diagram = f'<div class="diagram">{svg}</div>'
    diagram += fallback
    if item.get("mermaid"):
        diagram += f'<p>Mermaid 未配置离线渲染器，已降级为文字流程并保留源码。</p><details><summary>Mermaid 源码</summary><pre>{html.escape(str(item["mermaid"]))}</pre></details>'
    return diagram


def _safe_svg(value: str) -> str:
    try:
        root = ET.fromstring(value)
    except ET.ParseError:
        return ""
    allowed = {"svg", "g", "path", "rect", "circle", "line", "polyline", "polygon", "text", "tspan", "defs", "marker", "title", "desc"}
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] not in allowed:
            return ""
        for key in list(node.attrib):
            local = key.rsplit("}", 1)[-1].lower()
            attribute_value = node.attrib[key]
            if (
                local.startswith("on")
                or local == "style"
                or (local in {"href", "src"} and not _is_svg_fragment(attribute_value))
                or _has_external_svg_url(attribute_value)
            ):
                del node.attrib[key]
    return ET.tostring(root, encoding="unicode")


_SVG_URL = re.compile(r"url\s*\(\s*([^)]*?)\s*\)", re.I | re.S)
_SVG_FRAGMENT = re.compile(r"^#[A-Za-z_][\w:.-]*$")
_CSS_ESCAPE = re.compile(r"\\([0-9a-fA-F]{1,6})(?:\s|\r\n|[\t\r\n\f])?|\\(.)", re.S)


def _is_svg_fragment(value: str) -> bool:
    return bool(_SVG_FRAGMENT.fullmatch(value.strip()))


def _has_external_svg_url(value: str) -> bool:
    """Return whether a presentation attribute contains a non-local URL reference.

    ElementTree resolves XML character references before this runs.  CSS escapes
    are normalized as well so that spellings such as ``u\\72l(...)`` cannot
    evade the URL check.  A fragment-only reference is the sole supported URL.
    """
    def unescape(match: re.Match[str]) -> str:
        hexadecimal, character = match.groups()
        if hexadecimal:
            try:
                return chr(int(hexadecimal, 16))
            except ValueError:
                return ""
        return character

    normalized = _CSS_ESCAPE.sub(unescape, value)
    for match in _SVG_URL.finditer(normalized):
        if not _is_svg_fragment(match.group(1)):
            return True
    return False


def _filter_attrs(risks: list[dict[str, Any]]) -> str:
    severity = " ".join(sorted({risk["severity"] for risk in risks}))
    dfx = " ".join(sorted({item for risk in risks for item in risk["dfx"]}))
    translations = " ".join(sorted({risk["translation_status"] for risk in risks}))
    return f'data-filterable data-severity="{html.escape(severity)}" data-dfx="{html.escape(dfx)}" data-translation="{html.escape(translations)}"'


def _html_risk(risk: dict[str, Any], links: list[tuple[str, str]]) -> str:
    rid = html.escape(risk["risk_id"]); linked = _html_links(links)
    instrumentation = f'<p><b>插桩需求（仅控制语义，不生成代码）：</b>{html.escape(_text(risk["instrumentation_request"]))}</p>' if risk.get("instrumentation_request") else ""
    coverage_gap = f'<p><b>覆盖缺口：</b>{html.escape(str(risk["coverage_gap"]))}</p>' if risk.get("coverage_gap") else ""
    return f'<article id="risk-{rid}" {_filter_attrs([risk])}><h3>{rid} {html.escape(str(risk.get("title", "未命名")))}</h3><span class="tag">严重度：{html.escape(risk["severity"])}</span> <span class="tag">可信度：{html.escape(_text(risk.get("confidence")))}</span> <span class="tag">{html.escape(_text(risk["dfx"]))}</span> <span class="tag">{html.escape(risk["translation_status"])}</span><p><b>测试解释：</b>{html.escape(risk["test_explanation"])}</p><p><b>触发与传播：</b>{html.escape(_text(risk.get("trigger")))} → {html.escape(_text(risk.get("propagation")))}</p><p><b>外部影响：</b>{html.escape(risk["external_impact"])}</p><p><b>观测与恢复：</b>{html.escape(_text(risk.get("observation")))}；{html.escape(_text(risk.get("recovery")))}</p><p><b>关联场景/用例：</b>{linked}</p>{coverage_gap}{instrumentation}</article>'


def _html_links(links: list[tuple[str, str]]) -> str:
    return "、".join(f'<a href="#{kind}-{html.escape(item)}">{"场景" if kind == "scenario" else "用例"} {html.escape(item)}</a>' for kind, item in links) or "无"


def _linked_risks(item: dict[str, Any], risk_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    return [risk_by_id[str(rid)] for rid in item.get("risk_ids", []) if str(rid) in risk_by_id]


def _html_scenarios(scenarios: list[dict[str, Any]], risk_by_id: dict[str, dict[str, Any]]) -> str:
    cards = []
    for scenario in scenarios:
        sid = str(scenario.get("scenario_id", scenario.get("id", "SC-未编号"))); risks = _linked_risks(scenario, risk_by_id)
        cards.append(f'<article id="scenario-{html.escape(sid)}" {_filter_attrs(risks)}><h3>{html.escape(sid)} {html.escape(str(scenario.get("title", "未命名")))}</h3><p><b>关联风险：</b>{_html_risk_links(scenario.get("risk_ids", []))}</p><p><b>场景说明：</b>{html.escape(_text(scenario.get("description", scenario.get("test_explanation"))))}</p><p><b>触发与判据：</b>{html.escape(_text(scenario.get("trigger")))}；{html.escape(_text(scenario.get("expected")))}</p></article>')
    return '<section><h2>6. 测试场景</h2>' + ("".join(cards) or '<p>未单独定义测试场景。</p>') + '</section>'


def _html_risk_links(risk_ids: list[Any]) -> str:
    return "、".join(f'<a href="#risk-{html.escape(str(rid))}">风险 {html.escape(str(rid))}</a>' for rid in risk_ids) or "未关联"


def _html_case(case: dict[str, Any], risk_by_id: dict[str, dict[str, Any]]) -> str:
    cid = html.escape(case["case_id"]); risks = _linked_risks(case, risk_by_id)
    steps = "".join(f"<li>{html.escape(step if isinstance(step, str) else str(step.get('description', '')))}</li>" for step in case["steps"])
    instrumentation = f'<p><b>插桩需求（不生成代码）：</b>{html.escape(_text(case["instrumentation"]))}</p>' if case.get("instrumentation") else ""
    return f'<article id="case-{cid}" {_filter_attrs(risks)}><h3>{cid} {html.escape(str(case.get("title", "未命名")))}</h3><p><b>关联风险：</b>{_html_risk_links(case.get("risk_ids", []))}</p><p><b>前置：</b>{html.escape(_text(case.get("preconditions")))}</p><ol>{steps}</ol><p><b>预期：</b>{html.escape(_text(case.get("expected")))}</p><p><b>观测与恢复：</b>{html.escape(_text(case.get("observation")))}；{html.escape(_text(case.get("cleanup")))}</p>{instrumentation}</article>'


def _html_coverage(model: dict[str, Any], mapping: dict[str, list[tuple[str, str]]]) -> str:
    gaps = _coverage_gap_map(model)
    rows = []
    for risk in model["risks"]:
        rid = risk["risk_id"]; links = mapping[rid]; gap = gaps[rid]
        conclusion = f"缺口：{gap}" if gap else ("已覆盖" if links else "待开发确认")
        rows.append(f'<tr><td><a href="#risk-{html.escape(rid)}">{html.escape(rid)}</a></td><td>{html.escape(risk["translation_status"])}</td><td>{_html_links(links)}</td><td>{html.escape(conclusion)}</td></tr>')
    return '<section><h2>8. 风险与用例覆盖映射</h2><table><thead><tr><th>风险</th><th>转译状态</th><th>场景/用例</th><th>结论</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table></section>'


def _html_evidence(model: dict[str, Any]) -> str:
    cards = "".join(
        f'<article><h3>{html.escape(reference)} {html.escape(title)}</h3>'
        f'<details><summary>源码证据</summary><pre>{html.escape(evidence)}</pre></details></article>'
        for reference, title, evidence in _collect_evidence(model)
    )
    return '<section><h2>9. 代码证据附录</h2>' + (cards or '<p>未提供代码证据。</p>') + '</section>'


def _html_next(model: dict[str, Any]) -> str:
    listing = lambda value: "<ul>" + "".join(f"<li>{html.escape(_text(item))}</li>" for item in _list(value)) + "</ul>" if _list(value) else "<p>无</p>"
    return f'<section><h2>10. 未闭环项与下一步建议</h2><h3>未闭环项</h3>{listing(model["unresolved"])}<h3>下一步建议</h3>{listing(model["next_steps"])}</section>'
