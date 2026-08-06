#!/usr/bin/env python3
"""Deterministic run-state controller for PANGEA-TEST.

The LLM performs semantic analysis; this module owns IDs, files, schema
validation, manifest transitions, audit rounds, and resume planning.

The default validator uses only the Python standard library. If ``jsonschema``
is installed, validation is automatically upgraded to Draft 2020-12.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REGISTRY = ROOT / "registry" / "scenarios.json"
SCHEMAS = ROOT / "schemas"
DFX_AGENTS = ["功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性"]
MR_BASELINE = ["原场景回归", "改动功能验证", "影响链回归", "异常与恢复验证"]
AUDITED_MODEL_RELATIVE = "internal/report-model.json"
LEGACY_MODULE_PLAN = {
    "playbooks": ["主干追踪", "分支枚举", "状态机提取", "资源生命周期", "异常传播"],
    "baseline_lenses": ["资源泄漏", "并发", "超时恢复", "数据完整性", "异常处理覆盖"],
}


class RunCtlError(RuntimeError):
    pass


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RunCtlError(f"文件不存在: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RunCtlError(f"JSON 无效: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunCtlError(f"JSON 根节点必须是对象: {path}")
    return value


def atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


_JSON_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _resolve_local_ref(schema: dict[str, Any], root_schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not ref:
        return schema
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise RunCtlError(f"基础校验器仅支持本地 $ref: {ref}")
    node: Any = root_schema
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise RunCtlError(f"schema 本地引用无效: {ref}")
        node = node[part]
    if not isinstance(node, dict):
        raise RunCtlError(f"schema 本地引用不是对象: {ref}")
    return node


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _JSON_TYPES[expected])


def _basic_validate(value: Any, schema: dict[str, Any], root_schema: dict[str, Any], path: str = "$") -> None:
    schema = _resolve_local_ref(schema, root_schema)

    if "const" in schema and value != schema["const"]:
        raise RunCtlError(f"schema 校验失败 [{path}]: 必须等于 {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise RunCtlError(f"schema 校验失败 [{path}]: 值不在允许枚举中")
    if "not" in schema and isinstance(schema["not"], dict):
        try:
            _basic_validate(value, schema["not"], root_schema, path)
        except RunCtlError:
            pass
        else:
            raise RunCtlError(f"schema 校验失败 [{path}]: 不得匹配禁止的结构")

    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else expected
        if not isinstance(expected_types, list) or not all(isinstance(item, str) for item in expected_types):
            raise RunCtlError(f"schema 定义错误 [{path}]: type 非法")
        unknown = [item for item in expected_types if item not in _JSON_TYPES]
        if unknown:
            raise RunCtlError(f"基础校验器不支持类型 [{path}]: {unknown}")
        if not any(_type_matches(value, item) for item in expected_types):
            raise RunCtlError(f"schema 校验失败 [{path}]: 类型应为 {expected_types}")
        if value is None:
            return

    if isinstance(value, dict):
        min_properties = schema.get("minProperties")
        if isinstance(min_properties, int) and len(value) < min_properties:
            raise RunCtlError(f"schema 校验失败 [{path}]: 至少需要 {min_properties} 个字段")
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise RunCtlError(f"schema 校验失败 [{path}]: 缺少必填字段 {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise RunCtlError(f"schema 校验失败 [{path}]: 存在未声明字段 {extra}")
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _basic_validate(child, child_schema, root_schema, f"{path}.{key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                _basic_validate(child, schema["additionalProperties"], root_schema, f"{path}.{key}")

    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if isinstance(min_items, int) and len(value) < min_items:
            raise RunCtlError(f"schema 校验失败 [{path}]: 至少需要 {min_items} 项")
        if isinstance(max_items, int) and len(value) > max_items:
            raise RunCtlError(f"schema 校验失败 [{path}]: 最多允许 {max_items} 项")
        if schema.get("uniqueItems"):
            fingerprints = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            if len(fingerprints) != len(set(fingerprints)):
                raise RunCtlError(f"schema 校验失败 [{path}]: 数组项必须唯一")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _basic_validate(item, item_schema, root_schema, f"{path}[{index}]")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        max_length = schema.get("maxLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise RunCtlError(f"schema 校验失败 [{path}]: 字符串过短")
        if isinstance(max_length, int) and len(value) > max_length:
            raise RunCtlError(f"schema 校验失败 [{path}]: 字符串过长")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            raise RunCtlError(f"schema 校验失败 [{path}]: 不匹配模式 {pattern}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if minimum is not None and value < minimum:
            raise RunCtlError(f"schema 校验失败 [{path}]: 小于最小值 {minimum}")
        if maximum is not None and value > maximum:
            raise RunCtlError(f"schema 校验失败 [{path}]: 大于最大值 {maximum}")

    # Keep the stdlib validator equivalent to the checkpoint gate when the
    # optional jsonschema package is unavailable.
    for condition in schema.get("allOf", []):
        if not isinstance(condition, dict) or not isinstance(condition.get("if"), dict):
            continue
        try:
            _basic_validate(value, condition["if"], root_schema, path)
        except RunCtlError:
            selected = condition.get("else")
        else:
            selected = condition.get("then")
        if isinstance(selected, dict):
            _basic_validate(value, selected, root_schema, path)


def validate(instance: dict[str, Any], schema_name: str) -> str:
    schema = read_json(SCHEMAS / schema_name)
    validator_mode = os.environ.get("PANGEA_VALIDATOR", "auto").lower()
    if validator_mode not in {"auto", "stdlib", "jsonschema"}:
        raise RunCtlError("PANGEA_VALIDATOR 只能是 auto、stdlib 或 jsonschema")

    jsonschema: Any | None = None
    if validator_mode != "stdlib":
        try:
            import jsonschema as imported_jsonschema  # type: ignore
            jsonschema = imported_jsonschema
        except ImportError:
            if validator_mode == "jsonschema":
                raise RunCtlError(
                    "已强制使用 jsonschema，但环境未安装；"
                    "可执行 python -m pip install -r runtime/requirements-strict.txt"
                )

    if jsonschema is None:
        _basic_validate(instance, schema, schema)
        backend = "stdlib"
    else:
        try:
            jsonschema.Draft202012Validator(schema).validate(instance)
        except jsonschema.ValidationError as exc:
            location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
            raise RunCtlError(f"schema 校验失败 [{location}]: {exc.message}") from exc
        backend = "jsonschema"

    if schema_name == "stage-checkpoint.schema.json":
        _assert_completed_facts(instance)
    return backend


def slug(value: str) -> str:
    value = re.sub(r"[\\/:\s]+", "-", value.strip())
    value = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value)
    return value.strip("-.") or "target"


def load_scenario(scenario_id: str) -> dict[str, Any]:
    registry = read_json(REGISTRY)
    scenario_id = registry.get("legacy_aliases", {}).get(scenario_id, scenario_id)
    try:
        scenario = registry["scenarios"][scenario_id]
    except KeyError as exc:
        raise RunCtlError(f"未知 scenario_id: {scenario_id}") from exc
    if not isinstance(scenario, dict):
        raise RunCtlError(f"场景定义不是对象: {scenario_id}")
    return scenario


def _json_value(raw: str | None, label: str, default: Any) -> Any:
    if raw is None:
        return default
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RunCtlError(f"{label} 必须是 JSON: {exc}") from exc
    return value


_REPOSITORY_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _registered_repositories(root: Path, requested: list[str]) -> list[str]:
    """Admit only named, usable Git worktrees owned by pangea-data."""
    from runtime import data_runtime

    workspace = data_runtime.ensure_layout(root)
    repository_root = (workspace / "repositories").resolve()
    admitted: list[str] = []
    for name in requested:
        if not _REPOSITORY_NAME.fullmatch(name):
            raise RunCtlError(f"--repository 必须是 pangea-data/repositories 下的工作树名称: {name}")
        candidate = repository_root / name
        if candidate.is_symlink() or not candidate.is_dir() or candidate.resolve().parent != repository_root:
            raise RunCtlError(f"未登记的仓库工作树: {name}")
        try:
            probe = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "--is-inside-work-tree", "--show-toplevel"],
                text=True, capture_output=True, check=False, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RunCtlError(f"无法验证仓库工作树 {name}: {exc}") from exc
        probe_lines = probe.stdout.splitlines()
        if probe.returncode != 0 or len(probe_lines) != 2 or probe_lines[0].strip() != "true":
            raise RunCtlError(f"登记仓库不是有效 Git 工作树: {name}")
        try:
            top_level = Path(probe_lines[1].strip()).resolve()
        except (OSError, RuntimeError) as exc:
            raise RunCtlError(f"无法解析仓库顶层目录 {name}: {exc}") from exc
        if top_level != candidate.resolve():
            raise RunCtlError(f"登记目录不是独立 Git 工作树根目录: {name}")
        admitted.append(name)
    if len(admitted) != len(set(admitted)):
        raise RunCtlError("--repository 不得重复")
    return admitted


def _repository_commits(raw_commits: list[str], repositories: list[str], mode: str) -> dict[str, str] | None:
    """Parse the explicit MR repository-to-commit contract supplied by the CLI."""
    if mode != "mr_regression":
        if raw_commits:
            raise RunCtlError("模块分析不得携带 --repository-commit")
        return None
    commits: dict[str, str] = {}
    for raw in raw_commits:
        repository, separator, commit = raw.partition("=")
        if not separator or not _REPOSITORY_NAME.fullmatch(repository) or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            raise RunCtlError("--repository-commit 必须为 <仓名>=<40位小写SHA>")
        if repository in commits:
            raise RunCtlError("--repository-commit 不得重复")
        commits[repository] = commit
    if set(commits) != set(repositories):
        raise RunCtlError("MR 必须为每个 --repository 提供且仅提供一个 --repository-commit")
    return commits


_PLACEHOLDER_TEXT = {"x", "ok", "done", "pass", "passed", "na", "n/a", "none", "null", "todo", "tbd", "test", "完成", "已完成", "通过", "无", "暂无", "占位"}
_GENERIC_FACT_STAGES = {"code_map", "flow", "branches", "impact_chain", "dfx_route", "risk_ledger", "specialist", "sfmea", "test_design"}


def _dominated_by_repetition(text: str) -> bool:
    compact = "".join(re.findall(r"[0-9a-z\u4e00-\u9fff]", text))
    if len(compact) < 3:
        return False
    for width in range(1, min(6, len(compact) // 3) + 1):
        for start in range(0, len(compact) - width + 1):
            unit = compact[start:start + width]
            count = compact.count(unit)
            if count >= 3 and count * width / len(compact) >= 0.60:
                return True
    return False


def _meaningful_text(value: Any, minimum: int = 1) -> bool:
    if not isinstance(value, str):
        return False
    normalized = re.sub(r"\s+", " ", value.strip()).casefold()
    if len(normalized) < minimum or normalized in _PLACEHOLDER_TEXT:
        return False
    # Reject filler such as punctuation-only text, "aaaaaaaaaaaa", or "testtesttest".
    if not re.search(r"[0-9a-z\u4e00-\u9fff]", normalized):
        return False
    significant = re.findall(r"[0-9a-z\u4e00-\u9fff]", normalized)
    if minimum >= 4 and len(set(significant)) < 3:
        return False
    return not _dominated_by_repetition(normalized)


def _assert_completed_facts(checkpoint: dict[str, Any]) -> None:
    if checkpoint.get("status") != "completed":
        return
    facts = checkpoint.get("facts")
    if not isinstance(facts, list) or not facts:
        raise RunCtlError(f"completed checkpoint 缺少 facts: {checkpoint.get('stage', '<unknown>')}")
    if any(not isinstance(fact, dict) or not fact for fact in facts):
        raise RunCtlError("completed checkpoint 的每个 fact 必须是非空对象")
    stage = checkpoint.get("stage")
    if stage in _GENERIC_FACT_STAGES:
        if any(not _meaningful_text(fact.get("summary"), 4) or not _meaningful_text(fact.get("evidence"), 4) for fact in facts):
            raise RunCtlError(f"{stage} 的每个 completed fact 必须包含非空有效的 summary 与 evidence")
    elif stage == "dfx_scan":
        if any(not _meaningful_text(fact.get("dfx")) or not _meaningful_text(fact.get("conclusion"), 4)
               or not _meaningful_text(fact.get("evidence"), 4) for fact in facts):
            raise RunCtlError("模块 dfx_scan 每维必须包含 dfx、具体 conclusion 与 evidence")
    elif stage == "mr_baseline":
        if any(not _meaningful_text(fact.get("baseline")) or not _meaningful_text(fact.get("verification"))
               or not _meaningful_text(fact.get("evidence")) for fact in facts):
            raise RunCtlError("MR mr_baseline 的每个 fact 必须包含 baseline、具体 verification 与 evidence")
    elif stage == "report":
        if any(not _meaningful_text(fact.get("report_md")) or not _meaningful_text(fact.get("report_html")) for fact in facts):
            raise RunCtlError("report 的每个 completed fact 必须包含 report_md 与 report_html")
    elif stage == "rework":
        if any(not _meaningful_text(fact.get("rework_summary"), 12) for fact in facts):
            raise RunCtlError("rework 的每个 completed fact 必须包含具体 rework_summary")


def _safe_rework_artifact(value: Any, run_dir: Path | None = None) -> bool:
    if not _meaningful_text(value):
        return False
    artifact = value.replace("\\", "/")
    path = Path(artifact)
    if path.is_absolute() or re.match(r"^[a-zA-Z]:/", artifact) or any(part in {"", ".", ".."} for part in path.parts):
        return False
    if run_dir is None:
        return True
    candidate = run_dir / path
    if candidate.is_symlink() or not candidate.is_file():
        return False
    try:
        candidate.resolve().relative_to(run_dir.resolve())
    except (OSError, ValueError):
        return False
    return True


def _validate_rework_checkpoint(checkpoint: dict[str, Any], audit: dict[str, Any], run_dir: Path | None = None) -> None:
    """Bind a rework checkpoint to every action from one failed audit round."""
    if checkpoint.get("stage") != "rework":
        if "rework" in checkpoint:
            raise RunCtlError("非 rework 检查点不得包含 rework 内容")
        return
    if checkpoint.get("status") != "completed":
        raise RunCtlError("rework 检查点必须为 completed")
    rework = checkpoint.get("rework")
    if not isinstance(rework, dict):
        raise RunCtlError("rework 检查点缺少 rework 内容")
    expected_round = audit.get("rounds")
    if rework.get("audit_round") != expected_round:
        raise RunCtlError("rework 检查点必须对应当前待闭环审计轮次")
    actions = audit.get("required_actions")
    closures = rework.get("action_closures")
    if not isinstance(actions, list) or not actions:
        raise RunCtlError("当前审计没有可闭环的 required_actions")
    if not isinstance(closures, list):
        raise RunCtlError("rework 检查点缺少 action_closures")
    if not all(isinstance(item, dict) and isinstance(item.get("action_index"), int) for item in closures):
        raise RunCtlError("每项 rework 闭环必须提供整数 action_index")
    indexes = [item["action_index"] for item in closures]
    expected = list(range(1, len(actions) + 1))
    if sorted(indexes) != expected or len(indexes) != len(set(indexes)):
        raise RunCtlError("rework 必须逐项且仅一次闭环上一轮所有 required_actions")
    for closure in closures:
        evidence = closure.get("evidence") if isinstance(closure, dict) else None
        if not isinstance(closure, dict) or not _meaningful_text(closure.get("closure"), 12):
            raise RunCtlError("每项 rework 闭环必须给出具体 closure，不能使用占位或机械重复文本")
        if not isinstance(evidence, dict) or not _safe_rework_artifact(evidence.get("artifact"), run_dir) \
                or not _meaningful_text(evidence.get("location"), 4) or not _meaningful_text(evidence.get("verification"), 12):
            raise RunCtlError("每项 rework evidence 必须包含安全相对 artifact、具体 location 与 verification")
        if re.sub(r"\s+", " ", closure["closure"].strip()).casefold() == re.sub(r"\s+", " ", evidence["verification"].strip()).casefold():
            raise RunCtlError("rework closure 与 evidence verification 不得机械重复")


def _assert_rework_complete(run_dir: Path, audit: dict[str, Any]) -> None:
    rework = audit.get("rework")
    if not isinstance(rework, dict) or rework.get("status") != "completed":
        raise RunCtlError("上一轮审计的 required_actions 尚未完成 rework 闭环")
    checkpoint_name = rework.get("checkpoint_file")
    if not isinstance(checkpoint_name, str) or Path(checkpoint_name).name != checkpoint_name:
        raise RunCtlError("rework 检查点引用无效")
    checkpoint = read_json(run_dir / "checkpoints" / checkpoint_name)
    validate(checkpoint, "stage-checkpoint.schema.json")
    _validate_rework_checkpoint(checkpoint, audit, run_dir)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fixed_audit_model(run_dir: Path) -> Path:
    internal_dir = (run_dir / "internal").resolve()
    model_path = run_dir / AUDITED_MODEL_RELATIVE
    if model_path.is_symlink() or model_path.resolve().parent != internal_dir:
        raise RunCtlError("被审报告模型不得通过符号链接指向 Run 外部")
    return model_path.resolve()


def _audit_model_binding(opinion: dict[str, Any], run_dir: Path) -> dict[str, str]:
    raw_path = opinion.get("audited_artifact")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RunCtlError("审计意见缺少 audited_artifact")
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute() or ".." in candidate.parts or candidate.as_posix() != AUDITED_MODEL_RELATIVE:
        raise RunCtlError(f"audited_artifact 必须是 Run 相对路径 {AUDITED_MODEL_RELATIVE}")
    model_path = _fixed_audit_model(run_dir)
    if not model_path.is_file():
        raise RunCtlError(f"审计报告模型不存在: {raw_path}")
    actual = _sha256_file(model_path)
    submitted = opinion.get("audited_sha256")
    if not isinstance(submitted, str):
        raise RunCtlError("审计意见必须提交 audited_sha256")
    if submitted != actual:
        raise RunCtlError("audited_sha256 与被审报告模型当前内容不一致")
    return {"path": str(model_path), "sha256": actual}


def _assert_audit_consistency(opinion: dict[str, Any]) -> None:
    """Reject opinions whose summary verdict can conceal failed dimensions."""
    checks = opinion.get("checks")
    if not isinstance(checks, dict):
        raise RunCtlError("审计意见缺少四维 checks")
    verdicts = [item.get("verdict") for item in checks.values() if isinstance(item, dict)]
    if len(verdicts) != 4 or any(item not in {"PASS", "CONCERNS", "FAIL"} for item in verdicts):
        raise RunCtlError("审计意见的四维 checks 无效")
    for dimension, check in checks.items():
        if not isinstance(check, dict):
            raise RunCtlError(f"审计 check 无效: {dimension}")
        violations = check.get("violations")
        gaps = check.get("gaps")
        if not isinstance(violations, list) or not isinstance(gaps, list):
            raise RunCtlError(f"审计 check 必须提供 violations 与 gaps 数组: {dimension}")
        if check["verdict"] == "PASS" and (violations or gaps):
            raise RunCtlError(f"PASS check 不得包含 violations 或 gaps: {dimension}")
        if check["verdict"] != "PASS" and not (violations or gaps):
            raise RunCtlError(f"非 PASS check 必须包含 violation 或 gap: {dimension}")
    expected = "FAIL" if "FAIL" in verdicts else "CONCERNS" if "CONCERNS" in verdicts else "PASS"
    verdict = opinion.get("verdict")
    required_actions = opinion.get("required_actions")
    if verdict != expected:
        raise RunCtlError(f"审计结论与四维 checks 不一致：应为 {expected}")
    if not isinstance(required_actions, list):
        raise RunCtlError("审计意见 required_actions 无效")
    for index, action in enumerate(required_actions, 1):
        if not isinstance(action, dict) or action.get("action_type") not in {
                "re_excavate", "fix_format", "add_evidence", "rewrite_case"}:
            raise RunCtlError(f"审计 action {index} 缺少有效 action_type")
        if not _meaningful_text(action.get("reason"), 8):
            raise RunCtlError(f"审计 action {index} reason 必须具体且不少于 8 字符")
        if not _meaningful_text(action.get("anchor"), 3):
            raise RunCtlError(f"审计 action {index} 缺少具体 anchor")
        if not _meaningful_text(action.get("verification"), 8):
            raise RunCtlError(f"审计 action {index} verification 必须给出具体闭环判据")
    if verdict == "PASS" and required_actions:
        raise RunCtlError("PASS 审计不得包含 required_actions")
    if verdict in {"CONCERNS", "FAIL"} and not required_actions:
        raise RunCtlError(f"{verdict} 审计必须给出 required_actions")


def _gitlink_gap_description(gap: dict[str, Any]) -> str:
    return (f"gitlink coverage gap: repository={gap['repository']}; path={gap['path']}; "
            f"commit_sha={gap['commit_sha']}")


def _assert_mr_snapshot_binding(root: Path, run_dir: Path) -> list[str]:
    """Return explicit unresolved gitlinks after enforcing one authoritative snapshot per repository."""
    from runtime import data_runtime, repository_runtime

    contract = data_runtime.read_json(run_dir / "internal" / "task-contract.json")
    if contract.get("mode") != "mr_regression":
        return []
    repositories = contract.get("repositories")
    if not isinstance(repositories, list) or not repositories or not all(isinstance(name, str) for name in repositories):
        raise RunCtlError("MR 任务契约缺少已登记主仓")
    expected_commits = contract.get("repository_commits")
    if not isinstance(expected_commits, dict) or set(expected_commits) != set(repositories) or any(
            not isinstance(commit, str) or re.fullmatch(r"[0-9a-f]{40}", commit) is None
            for commit in expected_commits.values()):
        raise RunCtlError("MR 任务契约缺少与仓库精确对应的 repository_commits")
    try:
        status = repository_runtime.verify_snapshots_against_source(root, run_dir.name)
    except repository_runtime.RepositoryRuntimeError as exc:
        raise RunCtlError(f"MR 快照不可用: {exc}") from exc
    snapshot_root = (run_dir / "tmp" / "snapshots").resolve()
    bindings: dict[str, list[dict[str, str]]] = {}
    for item in status.get("snapshots", []):
        if isinstance(item, dict) and isinstance(item.get("repository"), str):
            bindings.setdefault(item["repository"], []).append(item)
    extra = sorted(set(bindings) - set(repositories))
    if extra:
        raise RunCtlError(f"MR 存在未契约仓快照: {', '.join(extra)}")
    wrong_count = [name for name in repositories if len(bindings.get(name, [])) != 1]
    if wrong_count:
        raise RunCtlError(f"MR 每个契约仓必须恰好一个权威快照: {', '.join(wrong_count)}")
    authoritative: dict[str, dict[str, str]] = {}
    for repository in repositories:
        binding = bindings[repository][0]
        commit = binding.get("commit_sha")
        raw_dir = binding.get("snapshot_dir")
        if commit != expected_commits[repository] or not isinstance(raw_dir, str):
            raise RunCtlError(f"MR 主仓快照未精确绑定任务契约 commit: {repository}")
        snapshot_dir = Path(raw_dir)
        manifest_path = snapshot_dir / repository_runtime.MANIFEST_NAME
        try:
            snapshot_dir.resolve().relative_to(snapshot_root)
            read_only = not (snapshot_dir.stat().st_mode & 0o222) and not (manifest_path.stat().st_mode & 0o222)
        except (OSError, ValueError):
            read_only = False
        if snapshot_dir.is_symlink() or manifest_path.is_symlink() or not manifest_path.is_file() or not read_only:
            raise RunCtlError(f"MR 主仓快照不是当前 Run 内只读权威快照: {repository}")
        authoritative[repository] = binding

    unresolved: list[str] = []
    for gap in status.get("coverage_gaps", []):
        if not isinstance(gap, dict) or gap.get("kind") != repository_runtime.GITLINK_GAP_KIND:
            detail = gap.get("detail") if isinstance(gap, dict) else str(gap)
            raise RunCtlError(f"MR 快照清单存在不可闭环缺口: {detail}")
        owner = gap.get("repository")
        commit = gap.get("commit_sha")
        if owner not in authoritative or not isinstance(commit, str):
            raise RunCtlError("MR gitlink coverage gap 归属无效")
        linked = any(name != owner and binding.get("commit_sha") == commit
                     for name, binding in authoritative.items())
        if not linked:
            unresolved.append(_gitlink_gap_description(gap))
    unresolved = sorted(set(unresolved))
    known_gaps = contract.get("known_gaps", [])
    missing_gaps = [gap for gap in unresolved if gap not in known_gaps]
    if missing_gaps:
        raise RunCtlError("MR 未闭环 gitlink 必须逐项写入任务契约 known_gaps: " + " | ".join(missing_gaps))
    return unresolved


def _canonical_risk_for_binding(risk: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize the complete canonical risk-card payload."""
    validate(risk, "risk-card.schema.json")
    normalized = json.loads(json.dumps(risk, ensure_ascii=False))
    normalized["dfx"] = sorted(normalized["dfx"])
    if isinstance(normalized.get("related_risk_ids"), list):
        normalized["related_risk_ids"] = sorted(normalized["related_risk_ids"])
    return normalized


def _assert_report_risk_binding(run_dir: Path, model: dict[str, Any]) -> None:
    from runtime import data_runtime

    if not isinstance(model, dict):
        raise RunCtlError("report-model 必须是 JSON 对象")
    ledger = data_runtime.read_json(run_dir / "internal" / "risk-ledger.json", {"risks": []})
    validate(ledger, "risk-ledger.schema.json")
    if ledger.get("run_id") != run_dir.name:
        raise RunCtlError("risk-ledger.run_id 与当前 Run 不一致")
    ledger_risks = ledger.get("risks")
    report_risks = model.get("risks")
    if not isinstance(ledger_risks, list) or not isinstance(report_risks, list):
        raise RunCtlError("risk-ledger 与 report-model 必须包含 risks 数组")
    if not all(isinstance(risk, dict) for risk in ledger_risks + report_risks):
        raise RunCtlError("risk-ledger 与 report-model 的风险必须为 canonical 对象")
    ledger_by_id = {risk.get("risk_id"): risk for risk in ledger_risks}
    report_by_id = {risk.get("risk_id"): risk for risk in report_risks}
    if None in ledger_by_id or None in report_by_id or len(ledger_by_id) != len(ledger_risks) or len(report_by_id) != len(report_risks):
        raise RunCtlError("risk-ledger 与 report-model 的 risk_id 必须唯一且非空")
    if set(ledger_by_id) != set(report_by_id):
        raise RunCtlError("report-model risks 与 risk-ledger risk_id 集合不一致")
    for risk_id in ledger_by_id:
        try:
            ledger_risk = _canonical_risk_for_binding(ledger_by_id[risk_id])
            report_risk = _canonical_risk_for_binding(report_by_id[risk_id])
        except RunCtlError as exc:
            raise RunCtlError(f"正式 Run 风险必须符合 canonical risk-card: {risk_id}: {exc}") from exc
        if ledger_risk != report_risk:
            raise RunCtlError(f"report-model 风险与 risk-ledger 完整 canonical 内容不一致: {risk_id}")


def _assert_formal_task_contract(contract: Any) -> dict[str, Any]:
    """Validate that a persisted task contract has no blank or placeholder inputs."""
    if not isinstance(contract, dict):
        raise RunCtlError("正式任务契约必须是 JSON 对象")
    validate(contract, "task-contract.schema.json")
    required_text = ("schema_version", "mode", "goal", "target", "analysis_depth", "created_by")
    if any(not _meaningful_text(contract.get(key)) for key in required_text):
        raise RunCtlError("正式任务契约包含空白或占位值")
    repositories = contract.get("repositories")
    if not isinstance(repositories, list) or not repositories or any(not _meaningful_text(item) for item in repositories):
        raise RunCtlError("正式任务契约包含空白或占位仓库")
    for key in ("mr_url", "version", "topology"):
        value = contract.get(key)
        if value is not None and not _meaningful_text(value):
            raise RunCtlError(f"正式任务契约字段 {key} 包含空白或占位值")
    for key in ("test_focus", "input_refs", "excluded_scope", "tool_gaps", "known_gaps", "signals"):
        value = contract.get(key, [])
        if not isinstance(value, list) or any(not _meaningful_text(item) for item in value):
            raise RunCtlError(f"正式任务契约字段 {key} 包含空白或占位值")
    if not isinstance(contract.get("resource_emphasis"), bool):
        raise RunCtlError("正式任务契约缺少布尔字段 resource_emphasis")
    return contract


def _report_gap_descriptions(model: dict[str, Any]) -> list[str]:
    descriptions: list[str] = []
    for key in ("coverage_gaps", "unresolved"):
        values = model.get(key, [])
        if not isinstance(values, list):
            raise RunCtlError(f"report-model {key} 必须是数组")
        for value in values:
            if isinstance(value, str):
                descriptions.append(value)
            elif isinstance(value, dict):
                for field in ("reason", "detail", "gap", "description"):
                    if isinstance(value.get(field), str):
                        descriptions.append(value[field])
    return descriptions


def _assert_report_gap_binding(model: dict[str, Any], gaps: list[str]) -> None:
    descriptions = _report_gap_descriptions(model)
    missing = [gap for gap in gaps if descriptions.count(gap) != 1]
    if missing:
        raise RunCtlError("MR 未闭环 gitlink 必须在 report-model coverage_gaps 或 unresolved 中逐项映射一次: "
                          + " | ".join(missing))


def _assert_report_contract_and_sections(run_dir: Path, model: Any) -> dict[str, Any]:
    """Bind every formal report to the exact persisted task contract and core sections."""
    from runtime import data_runtime

    if not isinstance(model, dict):
        raise RunCtlError("report-model 必须是 JSON 对象")
    canonical = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    if "task_contract" not in model:
        raise RunCtlError("report-model 缺少 task_contract")
    reported = model["task_contract"]
    if not isinstance(reported, dict):
        raise RunCtlError("report-model task_contract 必须是 JSON 对象")
    _assert_formal_task_contract(reported)
    if reported != canonical:
        raise RunCtlError("report-model task_contract 与 Run internal/task-contract.json canonical 内容不一致")
    required_sections = ("code_map", "flows", "branches", "risks")
    empty_sections = [name for name in required_sections if not model.get(name)]
    if empty_sections:
        raise RunCtlError(f"报告模型缺少有效内容: {', '.join(empty_sections)}")
    return model


def _load_v2_workflow_plan(run_dir: Path) -> dict[str, Any]:
    """Load only a non-empty plan that still exactly matches the registry."""
    from runtime import data_runtime

    contract = data_runtime.read_json(run_dir / "internal" / "task-contract.json")
    plan = data_runtime.read_json(run_dir / "internal" / "workflow-plan.json", {})
    if not isinstance(plan, dict) or not plan:
        raise RunCtlError("Run 缺少有效 workflow plan")
    workflow = plan.get("workflow")
    if workflow not in {"mr-regression", "module-analysis"}:
        raise RunCtlError("workflow plan 的 workflow 未知")
    scenario = load_scenario(workflow)
    if contract.get("mode") != scenario.get("contract_mode"):
        raise RunCtlError("workflow plan 与任务契约 mode 不一致")
    canonical = v2_plan(contract)
    if plan != canonical:
        raise RunCtlError("workflow plan 与持久化任务契约 canonical 计划不一致")
    return plan


def v2_plan(contract: dict[str, Any]) -> dict[str, Any]:
    if contract["mode"] == "mr_regression":
        signals = [item.lower() for item in contract.get("signals", [])]
        routing = {
            "资源与规格": ("resource", "queue", "pool", "counter", "alloc", "free", "memory", "额度"),
            "性能与压力": ("performance", "latency", "throughput", "pressure", "iops", "性能"),
            "并发与异常": ("lock", "atomic", "async", "race", "timeout", "并发", "异步"),
            "升级与兼容": ("upgrade", "compat", "version", "abi", "firmware", "升级", "兼容"),
            "可靠性与一致性": ("reset", "recover", "reconnect", "persist", "consisten", "恢复", "一致"),
        }
        selected = ["功能与状态"]
        for agent, keywords in routing.items():
            if any(keyword in signal for signal in signals for keyword in keywords):
                selected.append(agent)
        scenario = load_scenario("mr-regression")
        return {"workflow": "mr-regression", "baseline_verification": MR_BASELINE,
                "dfx_agents": selected, "signals": contract.get("signals", []),
                "stages": scenario["stages"]}
    scenario = load_scenario("module-analysis")
    return {"workflow": "module-analysis", "dfx_agents": DFX_AGENTS,
            "resource_deep_dive": bool(contract.get("resource_emphasis")) or any(
                word in " ".join(contract.get("signals", [])).lower()
                for word in ("resource", "queue", "pool", "counter", "memory", "资源", "队列", "计数", "内存")),
            "stages": scenario["stages"]}


def create_v2_run(args: argparse.Namespace) -> None:
    """Create the Architecture v2 run through the pangea-data owner."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    scenario = load_scenario(args.scenario)
    mode = scenario["contract_mode"]
    depth = args.analysis_depth or scenario["default_depth"]
    if mode == "mr_regression" and depth != "focused":
        raise RunCtlError("MR 回归仅支持 focused 深度")
    if mode == "mr_regression" and not args.mr_url:
        raise RunCtlError("MR 回归必须提供 --mr-url")
    if mode == "module_analysis" and depth not in {"complete", "fast"}:
        raise RunCtlError("模块分析仅支持 complete 或 fast 深度")
    requested_repositories = args.repository or []
    if not requested_repositories:
        raise RunCtlError("至少提供一个 --repository")
    repositories = _registered_repositories(root, requested_repositories)
    repository_commits = _repository_commits(args.repository_commit or [], repositories, mode)
    contract = {
        "schema_version": "1.0", "mode": mode, "goal": args.goal or scenario["display_name"],
        "target": args.target, "repositories": repositories, "analysis_depth": depth,
        "mr_url": args.mr_url if mode == "mr_regression" else None,
        "version": args.version, "topology": args.topology,
        "test_focus": args.test_focus or [], "input_refs": args.input_ref or [],
        "excluded_scope": args.exclude or [], "tool_gaps": args.tool_gap or [],
        "known_gaps": args.known_gap or [], "created_by": args.created_by,
        "signals": args.signal or [], "resource_emphasis": bool(args.resource_emphasis),
    }
    if repository_commits is not None:
        contract["repository_commits"] = repository_commits
    schema_contract = contract
    run_id = args.run_id or f"{args.scenario}-{slug(args.target)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    created = data_runtime.create_run(root, run_id, schema_contract, args.max_audit_rounds)
    plan = v2_plan(contract)
    run_dir = Path(created["run_dir"])
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest["audit"]["rework"] = None
    validate(manifest, "session-manifest.schema.json")
    data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    atomic_write(run_dir / "internal" / "workflow-plan.json", plan)
    data_runtime.set_run_state(root, run_id, "mapping", "已建立任务契约，准备共享代码地图")
    print(json.dumps({"run_id": run_id, "run_dir": str(run_dir), "contract": schema_contract,
                      "plan": plan, "validation_backend": validate(schema_contract, "task-contract.schema.json")}, ensure_ascii=False))


def _specialist_skip_permitted(plan: dict[str, Any], checkpoint: dict[str, Any]) -> bool:
    reason = checkpoint.get("skip_reason")
    return (
        plan.get("workflow") == "module-analysis"
        and checkpoint.get("stage") == "specialist"
        and checkpoint.get("status") == "skipped"
        and _meaningful_text(reason, 4)
        and isinstance(reason, str)
        and re.search(r"(?:未命中|未发现|没有|无).{0,12}(?:专项|深挖|specialist)", reason, re.IGNORECASE) is not None
    )


def _v2_progress(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    planned = [str(stage) for stage in plan.get("stages", [])]
    analysis_stages = [stage for stage in planned if stage != "report"]
    effective: dict[str, dict[str, Any]] = {}
    manifest = read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("run_id") != run_dir.name:
        raise RunCtlError("Run manifest provenance 与当前 Run 不一致")
    checkpoint_count = manifest.get("checkpoint_count")
    if not isinstance(checkpoint_count, int) or checkpoint_count < 0:
        raise RunCtlError("manifest checkpoint_count 无效")
    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_files = sorted(checkpoint_dir.iterdir(), key=lambda item: item.name)
    if any(path.is_symlink() or not path.is_file() for path in checkpoint_files):
        raise RunCtlError("checkpoints 目录包含非普通文件")
    if len(checkpoint_files) != checkpoint_count:
        raise RunCtlError("checkpoint 文件数量与 manifest.checkpoint_count 不一致")

    highest_analysis = -1

    def stage_satisfied(stage: str) -> bool:
        checkpoint = effective.get(stage, {})
        return checkpoint.get("status") == "completed" or (
            stage == "specialist" and _specialist_skip_permitted(plan, checkpoint)
        )

    for expected_sequence, path in enumerate(checkpoint_files, 1):
        match = re.fullmatch(r"(\d{3})-([a-z_]+)\.json", path.name)
        if match is None or int(match.group(1)) != expected_sequence:
            raise RunCtlError("checkpoint 文件名序号必须从 001 连续且与 manifest 一致")
        checkpoint = read_json(path)
        if not isinstance(checkpoint, dict):
            raise RunCtlError(f"checkpoint 必须是 JSON 对象: {path.name}")
        checkpoint.setdefault("status", "completed")
        stage = checkpoint.get("stage")
        if stage not in planned and stage != "rework":
            raise RunCtlError(f"checkpoint 包含未知或非本工作流 stage: {stage}")
        if checkpoint.get("run_id") != run_dir.name:
            raise RunCtlError(f"checkpoint.run_id 与当前 Run 不一致: {path.name}")
        if checkpoint.get("sequence") != expected_sequence:
            raise RunCtlError(f"checkpoint.sequence 与文件名不一致: {path.name}")
        if match.group(2) != stage:
            raise RunCtlError(f"checkpoint stage 与文件名不一致: {path.name}")
        validate(checkpoint, "stage-checkpoint.schema.json")
        _assert_completed_facts(checkpoint)

        if stage in analysis_stages:
            stage_index = analysis_stages.index(stage)
            if stage_index < highest_analysis:
                raise RunCtlError(f"分析 checkpoint 未按 workflow plan 单调推进: {stage}")
            if stage_index > highest_analysis:
                missing_prior = [prior for prior in analysis_stages[:stage_index] if not stage_satisfied(prior)]
                if missing_prior:
                    raise RunCtlError("分析 checkpoint 越过未完成阶段: " + ", ".join(missing_prior))
                highest_analysis = stage_index
        elif stage == "rework":
            if any(not stage_satisfied(item) for item in analysis_stages):
                raise RunCtlError("rework checkpoint 只能出现在分析阶段全部完成后")
            rework = checkpoint.get("rework")
            audit_round = rework.get("audit_round") if isinstance(rework, dict) else None
            if checkpoint.get("status") != "completed" or not isinstance(audit_round, int) \
                    or audit_round < 1 or audit_round > manifest.get("audit", {}).get("rounds", 0):
                raise RunCtlError("rework checkpoint 未绑定合法审计轮次")
            continue
        elif stage == "report":
            audit = manifest.get("audit", {})
            if expected_sequence != checkpoint_count or any(not stage_satisfied(item) for item in analysis_stages) \
                    or audit.get("status") != "PASS" \
                    or (isinstance(audit.get("rework"), dict) and audit["rework"].get("status") == "required"):
                raise RunCtlError("report checkpoint 所处位置或审计状态非法")

        prior = effective.get(stage)
        if prior is None or checkpoint["sequence"] > prior["sequence"]:
            effective[stage] = checkpoint
    specialist = effective.get("specialist", {})
    if specialist.get("status") == "skipped" and not _specialist_skip_permitted(plan, specialist):
        raise RunCtlError("specialist 仅可在模块分析未命中专项且提供有效 skip_reason 时 skipped")
    completed = [stage for stage in planned if effective.get(stage, {}).get("status") == "completed"
                 or (stage == "specialist" and _specialist_skip_permitted(plan, effective.get(stage, {})))]
    pending = [stage for stage in planned if stage not in completed]
    return {"completed_stages": completed, "pending_stages": pending,
            "effective_checkpoints": effective,
            "last_checkpoint": checkpoint_files[-1].name if checkpoint_files else None}


def _assert_analysis_stages_complete(run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    progress = _v2_progress(run_dir, plan)
    missing = [stage for stage in progress["pending_stages"] if stage != "report"]
    if missing:
        raise RunCtlError(f"mandatory stages 尚未完成: {', '.join(missing)}")
    for stage in progress["completed_stages"]:
        if stage == "report":
            continue
        _assert_completed_facts(progress["effective_checkpoints"][stage])
    if plan.get("workflow") == "module-analysis":
        dfx_facts = progress["effective_checkpoints"].get("dfx_scan", {}).get("facts")
        if not isinstance(dfx_facts, list) or len(dfx_facts) != len(DFX_AGENTS):
            raise RunCtlError("模块 dfx_scan 必须恰好记录六个 canonical DFX 事实")
        covered: list[str] = []
        for fact in dfx_facts:
            if not isinstance(fact, dict) or not isinstance(fact.get("dfx"), str) or not _meaningful_text(fact.get("conclusion"), 4) or not _meaningful_text(fact.get("evidence"), 4):
                raise RunCtlError("模块 dfx_scan 每维必须包含 dfx、具体 conclusion 与 evidence")
            covered.append(fact["dfx"])
        if set(covered) != set(DFX_AGENTS) or len(covered) != len(set(covered)):
            raise RunCtlError("模块 dfx_scan 必须恰好覆盖六个 canonical DFX")
    if plan.get("workflow") == "mr-regression":
        baseline = progress["effective_checkpoints"].get("mr_baseline", {}).get("facts")
        if not isinstance(baseline, list):
            raise RunCtlError("MR mr_baseline 缺少结构化 facts")
        found: set[str] = set()
        for fact in baseline:
            if not isinstance(fact, dict):
                continue
            name = fact.get("baseline")
            if name in MR_BASELINE and all(isinstance(fact.get(key), str) and fact[key].strip()
                                           for key in ("verification", "evidence")):
                found.add(name)
        missing_baselines = [name for name in MR_BASELINE if name not in found]
        if missing_baselines:
            raise RunCtlError("MR mr_baseline 缺少结构化事实: " + ", ".join(missing_baselines))
    return progress


def resume_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, repository_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    ledger = data_runtime.read_json(run_dir / "internal" / "risk-ledger.json", {"risks": []})
    plan = _load_v2_workflow_plan(run_dir)
    progress = _v2_progress(run_dir, plan)
    audit = manifest.get("audit", {"status": "pending", "rounds": 0, "required_actions": []})
    snapshots = repository_runtime.snapshot_status(root, args.run_id)
    analysis_pending = [stage for stage in progress["pending_stages"] if stage != "report"]
    if analysis_pending:
        next_stage = analysis_pending[0]
    elif isinstance(audit.get("rework"), dict) and audit["rework"].get("status") == "required":
        next_stage = "rework"
    elif audit.get("status") != "PASS":
        next_stage = "audit"
    elif "report" in progress["pending_stages"]:
        next_stage = "report"
    else:
        next_stage = None
    print(json.dumps({"run_id": args.run_id, "status": manifest["status"], "machine_state": manifest["machine_state"],
                      "last_checkpoint": progress["last_checkpoint"], "next_stage": next_stage,
                      "completed_stages": progress["completed_stages"], "pending_stages": progress["pending_stages"],
                      "audit": audit, "open_risks": len(ledger.get("risks", [])), "plan": plan,
                      "snapshots": snapshots}, ensure_ascii=False, indent=2))


def record_rework_v2(args: argparse.Namespace) -> None:
    """Record concrete closure evidence for every action in the current audit."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    audit = manifest.get("audit", {})
    if audit.get("status") not in {"CONCERNS", "FAIL"}:
        raise RunCtlError("只有 CONCERNS 或 FAIL 审计可以记录 rework")
    if not isinstance(audit.get("rework"), dict) or audit["rework"].get("status") != "required":
        raise RunCtlError("当前审计不需要 rework，或 rework 已完成")
    payload = read_json(Path(args.file).resolve())
    allowed = {"action_closures", "facts", "context_digest"}
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise RunCtlError(f"rework 证据存在未声明字段: {unexpected}")
    checkpoint = {
        "stage": "rework", "status": "completed",
        "facts": payload.get("facts") or [{"rework_summary": "已逐项完成审计要求的整改并记录可复核证据。"}],
        "open_items": [], "next_step": "重新提交独立审计", "context_digest": payload.get("context_digest"),
        "rework": {"audit_round": audit["rounds"], "action_closures": payload.get("action_closures")},
    }
    _validate_rework_checkpoint(checkpoint, audit, run_dir)
    saved = data_runtime.append_checkpoint(root, args.run_id, checkpoint)
    audit["rework"] = {"audit_round": audit["rounds"], "status": "completed",
                       "checkpoint_file": f"{saved['sequence']:03d}-rework.json"}
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest["audit"] = audit
    manifest["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    validate(manifest, "session-manifest.schema.json")
    data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    data_runtime.set_run_state(root, args.run_id, "mining", f"第 {audit['rounds']} 轮审计整改已逐项闭环")
    print(json.dumps({"run_id": args.run_id, "audit_round": audit["rounds"],
                      "checkpoint": str(run_dir / "checkpoints" / audit["rework"]["checkpoint_file"]),
                      "closed_actions": len(audit["required_actions"])}, ensure_ascii=False))


def apply_audit_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可追加审计")
    plan = _load_v2_workflow_plan(run_dir)
    _assert_analysis_stages_complete(run_dir, plan)
    snapshot_gaps = _assert_mr_snapshot_binding(root, run_dir)
    opinion_path = Path(args.file).resolve()
    opinion = read_json(opinion_path)
    backend = validate(opinion, "audit-opinion.schema.json")
    audited_model = _audit_model_binding(opinion, run_dir)
    report_model = _assert_report_contract_and_sections(run_dir, read_json(Path(audited_model["path"])))
    _assert_report_gap_binding(report_model, snapshot_gaps)
    _assert_report_risk_binding(run_dir, report_model)
    verdict = opinion["verdict"]
    required_actions = opinion["required_actions"]
    _assert_audit_consistency(opinion)
    audit = manifest.setdefault("audit", {"rounds": 0, "max_rounds": 2, "status": "pending",
                                          "opinion_file": None, "required_actions": [], "rework": None})
    pending_rework = audit.get("rework")
    if isinstance(pending_rework, dict) and pending_rework.get("status") == "required":
        raise RunCtlError("上一轮审计的 required_actions 尚未完成 rework 闭环")
    if audit.get("status") in {"CONCERNS", "FAIL"}:
        _assert_rework_complete(run_dir, audit)
        previous = audit.get("audited_model", {}).get("sha256") if isinstance(audit.get("audited_model"), dict) else None
        if audited_model["sha256"] == previous:
            raise RunCtlError("整改后的下一轮审计必须绑定不同的 report-model SHA-256")
    round_no = int(audit["rounds"]) + 1
    if round_no > int(audit["max_rounds"]):
        raise RunCtlError("已达到最大审计轮数，禁止继续自动重审")
    out = run_dir / "internal" / "audit" / f"round-{round_no:02d}.json"
    atomic_write(out, opinion)
    rework = None
    if verdict in {"CONCERNS", "FAIL"}:
        rework = {"audit_round": round_no, "status": "required", "checkpoint_file": None}
    audit.update({"rounds": round_no, "status": verdict,
                  "opinion_file": str(out.relative_to(run_dir)), "required_actions": required_actions,
                  "rework": rework, "audited_model": audited_model})
    data_runtime.set_run_state(root, args.run_id, "reviewing", f"第 {round_no} 轮审计结果：{verdict}")
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest["audit"] = audit
    manifest["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    validate(manifest, "session-manifest.schema.json")
    data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    print(json.dumps({"run_id": args.run_id, "round": round_no, "verdict": verdict,
                      "required_actions": required_actions, "remaining_rounds": audit["max_rounds"] - round_no,
                      "opinion_file": str(out), "audited_model": audited_model,
                      "validation_backend": backend}, ensure_ascii=False))


def _safe_final_directory(run_dir: Path) -> Path:
    final_dir = run_dir / "final"
    try:
        mode = final_dir.lstat().st_mode
        run_resolved = run_dir.resolve(strict=True)
        final_resolved = final_dir.resolve(strict=True)
    except OSError as exc:
        raise RunCtlError(f"Run final 目录不可用: {exc}") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode) or final_resolved.parent != run_resolved:
        raise RunCtlError("Run final 必须是 Run 内非符号链接固定目录")
    for name in ("report.md", "report.html"):
        target = final_dir / name
        if target.is_symlink():
            raise RunCtlError(f"Run final 报告目标不得是符号链接: {name}")
        if target.exists() and not target.is_file():
            raise RunCtlError(f"Run final 报告目标必须是普通文件: {name}")
    return final_dir


def finalize_v2(args: argparse.Namespace) -> None:
    """Render the final pair lazily so reporting remains an optional runtime module."""
    from runtime import data_runtime, repository_runtime
    try:
        from runtime import reporting
    except ImportError as exc:
        raise RunCtlError(f"报告模块不可用: {exc}") from exc
    root = Path(args.root).resolve() if args.root else ROOT
    try:
        run_dir, manifest = data_runtime._load_run(root, args.run_id)
    except data_runtime.DataRuntimeError as exc:
        raise RunCtlError(str(exc)) from exc
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("Run 已结束，不可重复完成")
    plan = _load_v2_workflow_plan(run_dir)
    _assert_analysis_stages_complete(run_dir, plan)
    snapshot_gaps = _assert_mr_snapshot_binding(root, run_dir)
    if manifest.get("audit", {}).get("status") != "PASS":
        raise RunCtlError(f"审计尚未 PASS: {manifest.get('audit', {}).get('status', 'pending')}")
    audited_model = manifest["audit"].get("audited_model")
    if not isinstance(audited_model, dict):
        raise RunCtlError("PASS 审计缺少被审报告模型绑定")
    model_path = Path(args.model).resolve()
    fixed_model = _fixed_audit_model(run_dir)
    if model_path != fixed_model or str(fixed_model) != audited_model.get("path"):
        raise RunCtlError(f"--model 只能指向 Run 内固定文件 {AUDITED_MODEL_RELATIVE}")
    if not model_path.is_file() or _sha256_file(model_path) != audited_model.get("sha256"):
        raise RunCtlError("--model 已在 PASS 审计后变更，必须重新审计")
    model = _assert_report_contract_and_sections(run_dir, read_json(model_path))
    _assert_report_gap_binding(model, snapshot_gaps)
    _assert_report_risk_binding(run_dir, model)
    final_dir = _safe_final_directory(run_dir)
    try:
        markdown, html = reporting.write_report(model, final_dir)
    except reporting.ReportError as exc:
        raise RunCtlError(str(exc)) from exc
    data_runtime.append_checkpoint(root, args.run_id, {
        "stage": "report", "status": "completed",
        "facts": [{"report_md": str(markdown), "report_html": str(html)}],
        "open_items": [], "next_step": "Run 已完成",
    })
    try:
        cleanup = repository_runtime.cleanup_run_tmp(root, args.run_id)
    except (repository_runtime.RepositoryRuntimeError, OSError) as exc:
        raise RunCtlError(f"报告已生成且检查点已写入，但临时快照清理失败: {exc}") from exc
    data_runtime.set_run_state(root, args.run_id, "completed", "报告已生成")
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest.update({"status": "completed", "machine_state": "completed", "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")})
    validate(manifest, "session-manifest.schema.json")
    data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    print(json.dumps({"run_id": args.run_id, "report_md": str(markdown), "report_html": str(html),
                      "tmp_cleanup": cleanup}, ensure_ascii=False))


def create_run(args: argparse.Namespace) -> None:
    scenario = load_scenario(args.scenario)
    # v1's artifact protocol is retained only for existing automation/tests.
    # Architecture v2 never exposes this plan as a user workflow.
    if "playbooks" not in scenario:
        scenario = {**scenario, **LEGACY_MODULE_PLAN}
    date = datetime.now().strftime("%Y%m%d")
    base_id = f"{args.scenario}-{slug(args.target)}-{date}"
    runs_root = Path(args.runs_root).resolve()
    task_id = args.task_id or base_id
    run_dir = runs_root / task_id
    if run_dir.exists():
        raise RunCtlError(f"任务目录已存在: {run_dir}；请使用 resume 或显式指定新的 --task-id")

    playbooks = scenario.get("playbooks", [])
    lenses = scenario.get("baseline_lenses", [])
    planned: list[dict[str, Any]] = []
    for index, playbook in enumerate(playbooks, 1):
        planned.append({
            "artifact_id": f"structure-{index:02d}", "artifact_type": "code_evidence",
            "playbook": playbook, "target": args.target, "lens": None,
            "status": "pending", "artifact_file": None, "error": None,
        })
    for index, lens in enumerate(lenses, 1):
        planned.append({
            "artifact_id": f"risk-{index:02d}", "artifact_type": "code_evidence",
            "playbook": "风险扫描", "target": args.target, "lens": lens,
            "status": "pending", "artifact_file": None, "error": None,
        })

    envelope = {
        "schema_version": "1.0", "task_id": task_id, "scenario_id": args.scenario,
        "mode": "deep",
        "target": {"name": args.target, "source_path": str(Path(args.source_path).resolve()), "symbols": args.symbol or []},
        "source": {"commit_sha": args.commit_sha, "mr": None, "dirty": False},
        "constraints": {"max_parallel_tasks": args.max_parallel, "max_audit_rounds": args.max_audit_rounds,
                        "evidence_required": True, "output_language": "zh-CN"},
        "workspace": {"run_dir": str(run_dir)},
        "requested_outputs": ["flow_explanation", "sfmea", "blackbox_scenarios", "test_cases", "coverage_audit"],
    }
    manifest = {
        "artifact_type": "run_manifest", "schema_version": "1.0", "task_id": task_id,
        "scenario_id": args.scenario, "target": args.target, "mode": "deep",
        "inputs_ref": [str(Path(args.source_path).resolve())], "planned_artifacts": planned,
        "summary_status": "pending",
        "audit": {"rounds": 0, "max_rounds": args.max_audit_rounds, "status": "pending", "opinion_file": None},
    }
    backend = validate(envelope, "task-envelope.schema.json")
    validate(manifest, "run-manifest.schema.json")
    atomic_write(run_dir / "task-envelope.json", envelope)
    atomic_write(run_dir / "manifest.json", manifest)
    for name in ("evidence", "audit", "final", "logs"):
        (run_dir / name).mkdir(parents=True, exist_ok=True)
    print(json.dumps({
        "task_id": task_id,
        "run_dir": str(run_dir),
        "planned_tasks": len(planned),
        "validation_backend": backend,
    }, ensure_ascii=False))


def put_artifact(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    artifact = read_json(Path(args.file).resolve())
    backend = validate(artifact, "code-evidence.schema.json")
    if artifact.get("artifact_id") != args.artifact_id:
        raise RunCtlError("artifact_id 与命令参数不一致")
    target = next((item for item in manifest["planned_artifacts"] if item["artifact_id"] == args.artifact_id), None)
    if target is None:
        raise RunCtlError(f"manifest 中不存在 artifact_id: {args.artifact_id}")
    out = run_dir / "evidence" / f"{args.artifact_id}.json"
    atomic_write(out, artifact)
    target["status"] = artifact["status"]
    target["artifact_file"] = str(out.relative_to(run_dir))
    target["error"] = None
    validate(manifest, "run-manifest.schema.json")
    atomic_write(manifest_path, manifest)
    print(json.dumps({"accepted": args.artifact_id, "status": artifact["status"], "validation_backend": backend}, ensure_ascii=False))


def apply_audit(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    manifest = read_json(manifest_path)
    opinion = read_json(Path(args.file).resolve())
    backend = validate(opinion, "audit-opinion.schema.json")
    round_no = manifest["audit"]["rounds"] + 1
    if round_no > manifest["audit"]["max_rounds"]:
        raise RunCtlError("已达到最大审计轮数，禁止继续自动回挖")
    out = run_dir / "audit" / f"round-{round_no:02d}.json"
    atomic_write(out, opinion)
    manifest["audit"].update({"rounds": round_no, "status": opinion["verdict"], "opinion_file": str(out.relative_to(run_dir))})
    if opinion["verdict"] == "PASS":
        manifest["summary_status"] = "complete"
    validate(manifest, "run-manifest.schema.json")
    atomic_write(manifest_path, manifest)
    print(json.dumps({
        "round": round_no,
        "verdict": opinion["verdict"],
        "required_actions": opinion["required_actions"],
        "validation_backend": backend,
    }, ensure_ascii=False))


def resume(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    validate(manifest, "run-manifest.schema.json")
    pending = [item for item in manifest["planned_artifacts"] if item["status"] in {"pending", "partial", "failed"}]
    print(json.dumps({"task_id": manifest["task_id"], "summary_status": manifest["summary_status"],
                      "audit": manifest["audit"], "next_tasks": pending}, ensure_ascii=False, indent=2))


def validate_file(args: argparse.Namespace) -> None:
    instance = read_json(Path(args.file).resolve())
    backend = validate(instance, args.schema)
    print(json.dumps({"valid": True, "file": args.file, "schema": args.schema, "validation_backend": backend}, ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PANGEA-TEST deterministic run controller")
    sub = p.add_subparsers(dest="command", required=True)
    val = sub.add_parser("validate")
    val.add_argument("--file", required=True)
    val.add_argument("--schema", required=True)
    val.set_defaults(func=validate_file)
    v2 = sub.add_parser("create-v2", help="创建 Architecture v2 的 pangea-data Run")
    v2.add_argument("--scenario", choices=["mr-regression", "module-analysis"], required=True)
    v2.add_argument("--target", required=True)
    v2.add_argument("--repository", action="append", required=True)
    v2.add_argument("--repository-commit", action="append")
    v2.add_argument("--run-id")
    v2.add_argument("--root")
    v2.add_argument("--mr-url")
    v2.add_argument("--goal")
    v2.add_argument("--analysis-depth")
    v2.add_argument("--version")
    v2.add_argument("--topology")
    v2.add_argument("--test-focus", action="append")
    v2.add_argument("--input-ref", action="append")
    v2.add_argument("--exclude", action="append")
    v2.add_argument("--tool-gap", action="append")
    v2.add_argument("--known-gap", action="append")
    v2.add_argument("--signal", action="append")
    v2.add_argument("--resource-emphasis", action="store_true")
    v2.add_argument("--created-by", default="pangea-test")
    v2.add_argument("--max-audit-rounds", type=int, default=2)
    v2.set_defaults(func=create_v2_run)
    resume2 = sub.add_parser("resume-v2", help="读取 pangea-data Run 的续跑计划")
    resume2.add_argument("--run-id", required=True)
    resume2.add_argument("--root")
    resume2.set_defaults(func=resume_v2)
    rework2 = sub.add_parser("record-rework-v2", help="记录审计 required_actions 的逐项闭环证据")
    rework2.add_argument("--run-id", required=True)
    rework2.add_argument("--file", required=True)
    rework2.add_argument("--root")
    rework2.set_defaults(func=record_rework_v2)
    audit2 = sub.add_parser("apply-audit-v2", help="提交 Architecture v2 独立审计意见")
    audit2.add_argument("--run-id", required=True)
    audit2.add_argument("--file", required=True)
    audit2.add_argument("--root")
    audit2.set_defaults(func=apply_audit_v2)
    final2 = sub.add_parser("finalize-v2", help="生成 report.md 与 report.html")
    final2.add_argument("--run-id", required=True)
    final2.add_argument("--model", required=True)
    final2.add_argument("--root")
    final2.set_defaults(func=finalize_v2)
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
        return 0
    except RunCtlError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
