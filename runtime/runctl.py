#!/usr/bin/env python3
"""Deterministic run-state controller for PANGEA-TEST.

The LLM performs semantic analysis; this module owns IDs, files, schema
validation, manifest transitions, audit rounds, and resume planning.

The default validator uses only the Python standard library. If ``jsonschema``
is installed, validation is automatically upgraded to Draft 2020-12.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
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
DFX_DIMENSIONS = ["功能与状态", "资源与规格", "性能与压力", "并发与异常", "升级与兼容", "可靠性与一致性"]
MR_BASELINE = ["原场景回归", "改动功能验证", "影响链回归", "异常与恢复验证"]
AUDITED_MODEL_RELATIVE = "internal/report-model.json"
PREFLIGHT_RECEIPT_RELATIVE = "session/preflight-receipt.json"
CONTRACT_RECORD_RELATIVE = "internal/contract-record.json"
CONTRACT_CONFIRMATION_RELATIVE = "internal/contract-confirmation.json"
ACTIVATION_PENDING_RELATIVE = "internal/activation-pending.json"
EVALUATOR_INTAKE_SPEC_RELATIVE = "session/evaluator-intake-spec.json"
EVALUATOR_INTAKE_BINDING_RELATIVE = "internal/evaluator-intake-binding.json"
EVALUATOR_INTAKE_RUN_SPEC_RELATIVE = "internal/evaluator-intake-spec.json"
EVALUATOR_CONFIRMED_CONTRACT_RELATIVE = "internal/evaluator-confirmed-contract-record.json"
EVALUATOR_INTAKE_COMMAND = "python3 runtime/runctl.py evaluator-intake-v2"
EVALUATOR_INTAKE_INPUT_BINDING_NAMES = (
    "canonical_case", "canonical_task", "evaluator_intake_spec",
    "confirmed_contract_record", "stage_snapshot",
    "intake_prep_command", "intake_prep_result",
)
PREFLIGHT_MAX_AGE_HOURS = 24
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


def _stable_unique_json(path: Path, label: str, *, read_only: bool = False) -> tuple[dict[str, Any], bytes]:
    """Read one regular-file epoch and reject duplicate JSON object names."""
    duplicate = False

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicate = True
            result[key] = value
        return result

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RunCtlError(f"{label} 不可用") from exc
    try:
        before = os.fstat(descriptor)
        named = os.stat(path, follow_symlinks=False)
        if (not stat.S_ISREG(before.st_mode) or not stat.S_ISREG(named.st_mode)
                or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
                or before.st_uid != os.geteuid() or before.st_nlink != 1
                or (read_only and before.st_mode & 0o222)):
            raise RunCtlError(f"{label} 必须是 evaluator-owned 只读普通文件")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        if tuple(getattr(before, field) for field in fields) != tuple(getattr(after, field) for field in fields):
            raise RunCtlError(f"{label} 在读取期间发生变化")
        payload = b"".join(chunks)
        try:
            value = json.loads(payload.decode("utf-8"), object_pairs_hook=unique_object)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RunCtlError(f"{label} JSON 无效") from exc
        if duplicate or not isinstance(value, dict):
            raise RunCtlError(f"{label} 必须是字段唯一的 JSON 对象")
        return value, payload
    finally:
        os.close(descriptor)


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


def _resolved_repository_heads(root: Path, repositories: list[str]) -> dict[str, str]:
    from runtime.process_runtime import run_text

    repository_root = root / "pangea-data" / "repositories"
    commits: dict[str, str] = {}
    for repository in repositories:
        result = run_text(
            ["git", "-C", str(repository_root / repository), "rev-parse", "--verify", "HEAD^{commit}"],
            timeout=10,
        )
        commit = (result.stdout or "").strip()
        if result.returncode or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
            detail = (result.stderr or result.stdout or f"git exit {result.returncode}").strip()
            raise RunCtlError(f"无法解析登记仓库 HEAD commit {repository}: {detail}")
        commits[repository] = commit
    return commits


def _repository_commits(root: Path, raw_commits: list[str], repositories: list[str], mode: str) -> dict[str, str]:
    """Bind every formal run to immutable repository commits."""
    if mode != "mr_regression":
        if raw_commits:
            raise RunCtlError("模块分析不得携带 --repository-commit；运行时会自动绑定每个仓库的 HEAD commit")
        return _resolved_repository_heads(root, repositories)
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


def _source_scopes(raw_scopes: list[str], repositories: list[str]) -> dict[str, list[str]] | None:
    """Parse explicit R2 source scope entries as ``repository=relative/path``.

    Omission deliberately remains a legacy contract.  The deterministic R2
    pipeline refuses that legacy shape instead of inferring scope from a dirty
    workspace or quietly treating it as fully analysed.
    """
    if not raw_scopes:
        return None
    result: dict[str, list[str]] = {name: [] for name in repositories}
    for raw in raw_scopes:
        repository, separator, relative = raw.partition("=")
        candidate = Path(relative)
        if (not separator or repository not in result or not relative or candidate.is_absolute()
                or ".." in candidate.parts or candidate.as_posix() != relative):
            raise RunCtlError("--source-scope 必须为 <仓名>=<规范相对源码路径>")
        if relative in result[repository]:
            raise RunCtlError("--source-scope 不得重复")
        result[repository].append(relative)
    if any(not value for value in result.values()):
        raise RunCtlError("每个仓库至少需要一个 --source-scope")
    return {name: sorted(value) for name, value in result.items()}


_PLACEHOLDER_TEXT = {"x", "ok", "done", "pass", "passed", "na", "n/a", "none", "null", "todo", "tbd", "test", "完成", "已完成", "通过", "无", "暂无", "占位"}
_GENERIC_FACT_STAGES = {"code_map", "flow", "branches", "impact_chain", "dfx_route", "risk_ledger", "specialist", "sfmea", "test_design"}
ANALYSIS_MODEL_RELATIVE = "internal/analysis-model.json"
COVERAGE_JUDGE_RELATIVE = "internal/coverage-judge.json"
ANALYSIS_OUTCOMES = {"analyzed", "covered_by_other", "not_applicable", "blocked", "need_verify", "truncated"}


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


def _r2_fact_location(fact: dict[str, Any]) -> str:
    start = fact["line_start"]
    end = start + fact["line_count"] - 1
    return f"{fact['path']}:{start}" if start == end else f"{fact['path']}:{start}-{end}"


def _assert_r2_hc_risk_binding(run_dir: Path, model: dict[str, Any]) -> None:
    """Bind every merged H/C fragment chain to the canonical published risk."""
    from runtime import data_runtime, fragment_runtime

    fragments = []
    for path in sorted((run_dir / "internal/fragments").glob("*.json")):
        value = data_runtime.read_json(path)
        fragments.append(value.get("payload", value))
    merged = fragment_runtime.merge_fragments(fragments)
    facts = {(fact["obligation_id"], fact["inventory_id"], fact["line_start"], fact["line_count"]): fact
             for fact in merged["facts"]}
    expected = {risk["risk_id"]: risk for risk in merged["risk_cards"]
                if risk["severity"] in {"High", "Critical"}}
    published = {risk.get("risk_id"): risk for risk in model.get("risks", [])
                 if isinstance(risk, dict) and risk.get("severity") in {"High", "Critical"}}
    if set(published) != set(expected):
        raise RunCtlError("R2 H/C risks 未精确投影到 canonical report")
    field_map = {
        "title": "summary", "severity": "severity", "trigger": "trigger",
        "propagation": "propagation", "external_impact": "impact",
        "observation": "observation", "recovery": "recovery",
    }
    for risk_id, fragment_risk in expected.items():
        risk = published[risk_id]
        for report_field, fragment_field in field_map.items():
            if risk.get(report_field) != fragment_risk.get(fragment_field):
                raise RunCtlError(f"R2 H/C risk 正文未绑定 fragment {fragment_field}: {risk_id}")
        explanation = f"Control: {fragment_risk['control']}\nOracle: {fragment_risk['oracle']}"
        if risk.get("test_explanation") != explanation:
            raise RunCtlError(f"R2 H/C risk 控制与判据未精确绑定 fragment: {risk_id}")
        expected_evidence = set()
        for raw_key in fragment_risk["fact_keys"]:
            fact = facts.get(tuple(raw_key))
            if fact is None:
                raise RunCtlError(f"R2 H/C risk 引用了未知 fact: {risk_id}")
            expected_evidence.add((_r2_fact_location(fact), fact["evidence"]))
        evidence_rows = risk.get("evidence", [])
        actual_evidence = {(item.get("location"), item.get("observation"))
                           for item in evidence_rows if isinstance(item, dict)}
        if len(actual_evidence) != len(evidence_rows) or actual_evidence != expected_evidence:
            raise RunCtlError(f"R2 H/C risk evidence 未与 fragment exact facts 精确闭包: {risk_id}")


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



_ANALYSIS_COLLECTIONS: dict[str, tuple[str, ...]] = {
    "evidence_consumption": ("evidence_id", "source_ref", "status", "parser", "consumed_ranges", "conclusions", "used_by", "unread_ranges", "limitations"),
    "entrypoints": ("entrypoint_id", "title", "external_trigger", "registration", "preconditions", "flow_ids", "status", "disposition_reason", "source_evidence"),
    "flows": ("flow_id", "title", "priority", "external_trigger", "entrypoint_id", "registration", "preconditions", "normal_path", "decisions", "abnormal_paths", "state_changes", "resource_lifecycle", "timeout_retry_recovery", "concurrency", "error_propagation", "latent_or_secondary_failures", "blackbox_controls", "oracles", "source_evidence", "status", "disposition_reason"),
    "branches": ("branch_id", "flow_id", "condition", "true_path", "false_path", "external_effect", "controllability", "observability", "source_evidence", "status", "disposition_reason"),
    "states": ("state_id", "title", "initial_state", "transitions", "illegal_transitions", "external_controls", "observables", "source_evidence", "status", "disposition_reason"),
    "resources": ("resource_id", "title", "acquire", "owner", "release", "abnormal_cleanup", "invariant", "limits", "recovery", "source_evidence", "status", "disposition_reason"),
    "concurrency": ("concurrency_id", "title", "actors", "shared_state", "ordering", "race_windows", "cancellation", "recovery", "source_evidence", "status", "disposition_reason"),
    "error_chains": ("chain_id", "title", "trigger", "propagation", "masking", "terminal_effect", "recovery", "source_evidence", "status", "disposition_reason"),
    "model_applicability": ("dfx", "applicable", "reason", "evidence"),
    "scenario_candidates": ("candidate_id", "title", "drivers", "source_refs", "failure_mechanism", "external_construction", "injection", "oracle", "disposition", "target_ids"),
    "sfmea": ("sfmea_id", "title", "source_refs", "failure_mode", "cause", "local_effect", "external_effect", "detection", "recovery", "severity", "scenario_ids", "test_case_ids"),
    "test_scenarios": ("scenario_id", "title", "source_candidate_ids", "risk_ids", "preconditions", "trigger", "expected", "observations", "cleanup"),
    "test_flows": ("test_flow_id", "title", "scenario_id", "steps", "oracles", "cleanup", "test_case_ids"),
    "test_cases": ("case_id", "title", "scenario_id", "risk_ids", "preconditions", "steps", "expected", "observation", "cleanup", "source_refs"),
    "traceability": ("trace_id", "source_ids", "target_ids", "rationale"),
    "coverage_dispositions": ("item_type", "item_id", "outcome", "evidence", "covered_by", "missing_work"),
}
_ANALYSIS_ID_FIELDS = {
    "evidence_consumption": "evidence_id", "entrypoints": "entrypoint_id", "flows": "flow_id",
    "branches": "branch_id", "states": "state_id", "resources": "resource_id",
    "concurrency": "concurrency_id", "error_chains": "chain_id", "scenario_candidates": "candidate_id",
    "sfmea": "sfmea_id", "test_scenarios": "scenario_id", "test_flows": "test_flow_id",
    "test_cases": "case_id", "traceability": "trace_id",
}
_ANALYSIS_LIST_FIELDS = {
    "consumed_ranges", "conclusions", "used_by", "unread_ranges", "limitations", "flow_ids", "source_evidence",
    "normal_path", "decisions", "abnormal_paths", "state_changes", "resource_lifecycle", "timeout_retry_recovery",
    "concurrency", "error_propagation", "latent_or_secondary_failures", "blackbox_controls", "oracles", "transitions",
    "illegal_transitions", "external_controls", "observables", "limits", "actors", "shared_state", "ordering",
    "race_windows", "cancellation", "propagation", "drivers", "source_refs", "target_ids", "scenario_ids",
    "test_case_ids", "source_candidate_ids", "risk_ids", "observations", "steps", "covered_by", "missing_work", "source_ids",
}


def _analysis_model_path(run_dir: Path) -> Path:
    internal = (run_dir / "internal").resolve()
    path = run_dir / ANALYSIS_MODEL_RELATIVE
    if path.is_symlink() or path.resolve().parent != internal:
        raise RunCtlError("分析模型不得通过符号链接指向 Run 外部")
    return path.resolve()


def _require_analysis_text(value: Any, label: str, minimum: int = 4) -> None:
    if not _meaningful_text(value, minimum):
        raise RunCtlError(f"分析模型字段缺少具体内容: {label}")


def _require_analysis_list(value: Any, label: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise RunCtlError(f"分析模型字段必须是{'可空' if allow_empty else '非空'}数组: {label}")
    for index, item in enumerate(value, 1):
        if isinstance(item, str):
            # Technical identifiers such as N, C1, or a one-letter protocol field are opaque.
            _require_analysis_text(item, f"{label}[{index}]", 1)
        elif not isinstance(item, dict) or not item:
            raise RunCtlError(f"分析模型数组项无效: {label}[{index}]")


def _analysis_ids(model: dict[str, Any]) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = {}
    for collection, field in _ANALYSIS_ID_FIELDS.items():
        values: set[str] = set()
        for index, item in enumerate(model[collection], 1):
            value = item.get(field) if isinstance(item, dict) else None
            _require_analysis_text(value, f"{collection}[{index}].{field}", 2)
            if value in values:
                raise RunCtlError(f"分析模型 ID 重复: {value}")
            values.add(value)
        ids[collection] = values
    return ids


def _validate_analysis_model(model: Any, contract: dict[str, Any], run_id: str) -> dict[str, Any]:
    if not isinstance(model, dict):
        raise RunCtlError("分析模型必须是 JSON 对象")
    validate(model, "analysis-model.schema.json")
    if model.get("run_id") != run_id:
        raise RunCtlError("分析模型 run_id 与当前 Run 不一致")
    if model.get("analysis_depth") != contract.get("analysis_depth"):
        raise RunCtlError("分析模型 analysis_depth 与任务契约不一致")
    if model.get("source_commits") != contract.get("repository_commits"):
        raise RunCtlError("分析模型 source_commits 与任务契约 repository_commits 不一致")

    applicability=model.get("collection_applicability")
    if (not isinstance(applicability,list) or len(applicability)!=5
            or {x.get("collection") for x in applicability if isinstance(x,dict)}!={"states","resources","concurrency","error_chains","scenario_candidates"}):
        raise RunCtlError("analysis model 必须逐项声明可空集合 applicability")
    applicability_by={x["collection"]:x for x in applicability}
    for collection, required in _ANALYSIS_COLLECTIONS.items():
        items = model.get(collection)
        allow_empty=collection in applicability_by and applicability_by[collection].get("disposition")=="not_applicable"
        if not isinstance(items, list) or (not items and not allow_empty):
            raise RunCtlError(f"完整分析缺少适用工件集合或N/A处置: {collection}")
        if items and collection in applicability_by and applicability_by[collection].get("disposition")!="applicable":
            raise RunCtlError(f"{collection} 有工件却声明 not_applicable")
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise RunCtlError(f"分析模型项必须是对象: {collection}[{index}]")
            missing = [field for field in required if field not in item]
            extra = sorted(set(item)-set(required))
            if missing or extra:
                raise RunCtlError(f"{collection}[{index}] 缺少字段: {', '.join(missing)}")
            for field in required:
                value = item[field]
                label = f"{collection}[{index}].{field}"
                if field in _ANALYSIS_LIST_FIELDS:
                    empty_ok=field in {"unread_ranges", "limitations", "covered_by", "missing_work", "test_case_ids"}
                    empty_ok = empty_ok or (field=="concurrency" and applicability_by["concurrency"].get("disposition")=="not_applicable")
                    _require_analysis_list(value, label, allow_empty=empty_ok)
                elif field == "applicable":
                    if not isinstance(value, bool):
                        raise RunCtlError(f"分析模型字段必须是布尔值: {label}")
                elif field == "status":
                    allowed = {"parsed", "partially_parsed", "blocked", "out_of_scope", "unreadable"} \
                        if collection == "evidence_consumption" else ANALYSIS_OUTCOMES
                    if value not in allowed:
                        raise RunCtlError(f"分析模型 disposition 非法: {label}={value}")
                elif field == "outcome":
                    if value not in ANALYSIS_OUTCOMES:
                        raise RunCtlError(f"Coverage outcome 非法: {label}={value}")
                elif field == "disposition":
                    if value not in {"retained", "merged", "untestable", "out_of_scope", "blocked"}:
                        raise RunCtlError(f"场景候选 disposition 非法: {label}={value}")
                elif field == "severity":
                    if value not in {"Low", "Medium", "High", "Critical"}:
                        raise RunCtlError(f"SFMEA 严重度非法: {label}={value}")
                else:
                    _require_analysis_text(value, label, 2)

    ids = _analysis_ids(model)
    dfx = [item.get("dfx") for item in model["model_applicability"]]
    if len(dfx) != len(DFX_DIMENSIONS) or set(dfx) != set(DFX_DIMENSIONS) or len(dfx) != len(set(dfx)):
        raise RunCtlError("model_applicability 必须恰好覆盖六个 canonical DFX")

    entrypoints, flows = ids["entrypoints"], ids["flows"]
    branches, states, resources = ids["branches"], ids["states"], ids["resources"]
    concurrency, chains = ids["concurrency"], ids["error_chains"]
    scenarios, cases = ids["test_scenarios"], ids["test_cases"]
    candidates = ids["scenario_candidates"]
    for item in model["entrypoints"]:
        unknown = set(item["flow_ids"]) - flows
        if unknown:
            raise RunCtlError(f"入口引用未知 flow: {sorted(unknown)}")
    for item in model["flows"]:
        if item["entrypoint_id"] not in entrypoints:
            raise RunCtlError(f"Flow 引用未知 entrypoint: {item['entrypoint_id']}")
        reference_fields = {
            "decisions": branches, "state_changes": states, "resource_lifecycle": resources,
            "concurrency": concurrency, "error_propagation": chains,
        }
        for field, known in reference_fields.items():
            unknown = set(item[field]) - known
            if unknown:
                raise RunCtlError(f"Flow {item['flow_id']} 的 {field} 引用未知 ID: {sorted(unknown)}")
    for item in model["branches"]:
        if item["flow_id"] not in flows:
            raise RunCtlError(f"Branch 引用未知 flow: {item['flow_id']}")
    for item in model["test_scenarios"]:
        unknown = set(item["source_candidate_ids"]) - candidates
        if unknown:
            raise RunCtlError(f"测试场景引用未知 candidate: {sorted(unknown)}")
    for item in model["test_flows"]:
        if item["scenario_id"] not in scenarios:
            raise RunCtlError(f"测试流程引用未知 scenario: {item['scenario_id']}")
        unknown = set(item["test_case_ids"]) - cases
        if unknown:
            raise RunCtlError(f"测试流程引用未知 case: {sorted(unknown)}")
    for item in model["test_cases"]:
        if item["scenario_id"] not in scenarios:
            raise RunCtlError(f"测试用例引用未知 scenario: {item['scenario_id']}")

    all_ids = set().union(*ids.values())
    for item in model["coverage_dispositions"]:
        if item["item_id"] not in all_ids:
            raise RunCtlError(f"Coverage disposition 引用未知分析项: {item['item_id']}")
    covered_items = {item["item_id"] for item in model["coverage_dispositions"]}
    mandatory = entrypoints | flows | branches | states | resources | concurrency | chains | candidates
    missing_dispositions = sorted(mandatory - covered_items)
    if missing_dispositions:
        raise RunCtlError("完整分析存在未处置项: " + ", ".join(missing_dispositions))

    incomplete = {
        item.get(field) for collection, field in _ANALYSIS_ID_FIELDS.items()
        for item in model[collection]
        if item.get("status") in {"blocked", "need_verify", "truncated"}
    }
    incomplete |= {item["item_id"] for item in model["coverage_dispositions"]
                   if item["outcome"] in {"blocked", "need_verify", "truncated"}}
    unresolved_ids = {item.get("item_id") for item in model.get("unresolved", []) if isinstance(item, dict)}
    if incomplete - unresolved_ids:
        raise RunCtlError("blocked/need_verify/truncated 项必须逐项进入 unresolved: " + ", ".join(sorted(incomplete - unresolved_ids)))

    if contract.get("analysis_depth") == "complete":
        truncated = [item["item_id"] for item in model["coverage_dispositions"] if item["outcome"] == "truncated"]
        if truncated:
            raise RunCtlError("complete 模式不得以 truncated 通过门禁: " + ", ".join(truncated))
    elif not model.get("depth_limitations"):
        raise RunCtlError("fast 模式必须明确 depth_limitations，禁止伪装成完整型")
    return model

def _expected_r2_projection(run_dir:Path) -> dict[str,Any]:
    from runtime import fragment_runtime, data_runtime
    fragments=[]
    for path in sorted((run_dir/"internal/fragments").glob("*.json")):
        value=data_runtime.read_json(path); fragments.append(value.get("payload",value))
    merged=fragment_runtime.merge_fragments(fragments)
    contributions={family:{"ids":[x["contribution_id"] for x in merged["contributions"][family]],
                           "sha256":fragment_runtime.digest(merged["contributions"][family])}
                   for family in fragment_runtime.CONTRIBUTION_FAMILIES}
    hc=[x for x in merged["risk_cards"] if x["severity"] in {"High","Critical"}]
    return {"merged_sha256":merged["sha256"],"fragment_ids":merged["fragment_ids"],
            "fragment_sha256s":{fragment["fragment_id"]:fragment_runtime.digest(fragment) for fragment in fragments},
            "contributions":contributions,
            "hc_risks":{"ids":[x["risk_id"] for x in hc],"sha256":fragment_runtime.digest(hc)}}

def _validate_r2_collection_na(run_dir:Path,model:dict[str,Any]) -> None:
    from runtime import fragment_runtime, data_runtime
    fragments=[]
    for path in sorted((run_dir/"internal/fragments").glob("*.json")):
        value=data_runtime.read_json(path); fragments.append(value.get("payload",value))
    merged=fragment_runtime.merge_fragments(fragments)
    known={(fact["obligation_id"],fact["inventory_id"],fact["line_start"],fact["line_count"]) for fact in merged["facts"]}
    for item in model["collection_applicability"]:
        keys=item.get("fact_keys",[])
        if item["disposition"]=="not_applicable" and (not keys or any(tuple(key) not in known for key in keys)):
            raise RunCtlError("R2 collection N/A 必须绑定 fragment 中的 exact fact keys")


def _analysis_model_binding(run_dir: Path, contract: dict[str, Any], *, required: bool) -> dict[str, str] | None:
    path = _analysis_model_path(run_dir)
    if not path.is_file():
        if required:
            raise RunCtlError(f"完整型模块分析缺少固定分析模型: {ANALYSIS_MODEL_RELATIVE}")
        return None
    model = _validate_analysis_model(read_json(path), contract, run_dir.name)
    del model
    return {"path": ANALYSIS_MODEL_RELATIVE, "sha256": _sha256_file(path)}


def _requires_complete_analysis_model(contract: dict[str, Any]) -> bool:
    return contract.get("mode") == "module_analysis" and contract.get("analysis_depth") == "complete"



def _marked_project_root(root: Path) -> bool:
    from runtime import workspace_runtime
    return not workspace_runtime._marker_missing(root.resolve())


def _contract_storage(root: Path) -> Path:
    from runtime import data_runtime
    workspace = data_runtime.ensure_layout(root)
    return data_runtime._ensure_managed_directory(
        workspace / "contracts", workspace.resolve(strict=True), "contracts 目录"
    )


def _contract_record_path(root: Path, contract_id: str, *, create_dir: bool = False) -> Path:
    if not contract_id or Path(contract_id).name != contract_id or contract_id in {".", ".."}:
        raise RunCtlError("contract_id 非法")
    contracts = _contract_storage(root)
    directory = contracts / contract_id
    if create_dir:
        if directory.exists() or directory.is_symlink():
            raise RunCtlError(f"任务契约已存在: {contract_id}")
        directory.mkdir()
    elif directory.is_symlink() or not directory.is_dir():
        raise RunCtlError(f"任务契约不存在: {contract_id}")
    return directory / "contract.json"


def _load_contract_record(root: Path, contract_id: str) -> tuple[Path, dict[str, Any]]:
    path = _contract_record_path(root, contract_id)
    record = read_json(path)
    validate(record, "contract-record.schema.json")
    if record.get("contract_id") != contract_id:
        raise RunCtlError("任务契约 contract_id 与路径不一致")
    _assert_formal_task_contract(record.get("task_contract"))
    return path, record


def _preflight_binding(root: Path, repositories: list[str]) -> dict[str, str]:
    from runtime import data_runtime
    workspace = data_runtime.ensure_layout(root)
    path = workspace / PREFLIGHT_RECEIPT_RELATIVE
    if path.is_symlink() or not path.is_file():
        raise RunCtlError("缺少 portable preflight receipt；请先执行 /initial 或 preflight")
    receipt = read_json(path)
    validate(receipt, "preflight-receipt.schema.json")
    if Path(receipt["project_root"]).resolve() != root.resolve():
        raise RunCtlError("preflight receipt 绑定的 project_root 与当前 root 不一致")
    if Path(receipt["repository_root"]).resolve() != (workspace / "repositories").resolve():
        raise RunCtlError("preflight receipt 的 repository_root 与当前工作区不一致")
    if "draft_contract" not in receipt.get("allowed_next_actions", []):
        raise RunCtlError("preflight 未允许进入任务契约阶段")
    missing = sorted(set(repositories) - set(receipt.get("known_repositories", [])))
    if missing:
        raise RunCtlError("preflight 未识别任务仓库: " + ", ".join(missing))
    try:
        created = datetime.fromisoformat(receipt["created_at"])
        now = datetime.now(created.tzinfo) if created.tzinfo else datetime.now()
    except (TypeError, ValueError) as exc:
        raise RunCtlError("preflight receipt created_at 无效") from exc
    if (now - created).total_seconds() > PREFLIGHT_MAX_AGE_HOURS * 3600:
        raise RunCtlError("preflight receipt 已过期，请重新执行 /initial")
    return {"path": PREFLIGHT_RECEIPT_RELATIVE, "sha256": _sha256_file(path),
            "created_at": receipt["created_at"]}


def _contract_from_args(args: argparse.Namespace, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    scenario = load_scenario(args.scenario)
    mode = scenario["contract_mode"]
    depth = args.analysis_depth or scenario["default_depth"]
    if mode == "mr_regression" and depth != "focused":
        raise RunCtlError("MR 回归仅支持 focused 深度")
    if mode == "mr_regression" and not args.mr_url:
        raise RunCtlError("MR 回归必须提供 --mr-url")
    if mode == "module_analysis" and depth not in {"complete", "fast"}:
        raise RunCtlError("模块分析仅支持 complete 或 fast 深度")
    requested = args.repository or []
    if not requested:
        raise RunCtlError("至少提供一个 --repository")
    repositories = _registered_repositories(root, requested)
    repository_commits = _repository_commits(root, args.repository_commit or [], repositories, mode)
    source_scopes = _source_scopes(getattr(args, "source_scope", None) or [], repositories)
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
    if source_scopes is not None:
        contract["source_scopes"] = source_scopes
        contract["source_scopes_sha256"] = hashlib.sha256(
            json.dumps({name: source_scopes[name] for name in sorted(source_scopes)},
                       ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    _assert_formal_task_contract(contract)
    return scenario, contract


def _assert_run_contract_lifecycle(run_dir: Path) -> dict[str, Any] | None:
    from runtime import data_runtime
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    record_file = manifest.get("contract_record_file") if isinstance(manifest, dict) else None
    confirmation_file = manifest.get("contract_confirmation_file") if isinstance(manifest, dict) else None
    if record_file is None and confirmation_file is None:
        return None
    if record_file != CONTRACT_RECORD_RELATIVE or confirmation_file != CONTRACT_CONFIRMATION_RELATIVE:
        raise RunCtlError("Run 任务契约生命周期文件路径无效")
    record = read_json(run_dir / record_file)
    validate(record, "contract-record.schema.json")
    if record.get("status") != "activated" or record.get("activation", {}).get("run_id") != run_dir.name:
        raise RunCtlError("Run 未绑定已激活任务契约")
    confirmation = read_json(run_dir / confirmation_file)
    if confirmation != record.get("confirmation") or not isinstance(confirmation, dict):
        raise RunCtlError("Run 任务契约确认记录缺失或不一致")
    canonical = data_runtime.read_json(run_dir / "internal" / "task-contract.json")
    if record.get("task_contract") != canonical:
        raise RunCtlError("已激活任务契约与 Run canonical task contract 不一致")
    return record


def draft_contract_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, workspace_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    workspace_runtime.validate_project_root(root)
    evaluator_contract = getattr(args, "_evaluator_contract", None)
    if evaluator_contract is None:
        scenario, contract = _contract_from_args(args, root)
        binding = _preflight_binding(root, contract["repositories"])
    else:
        if (not isinstance(evaluator_contract, dict)
                or set(evaluator_contract) != {"repository_commit", "source_manifest_sha256"}
                or re.fullmatch(r"[0-9a-f]{40}", str(evaluator_contract.get("repository_commit", ""))) is None
                or re.fullmatch(r"[0-9a-f]{64}", str(evaluator_contract.get("source_manifest_sha256", ""))) is None):
            raise RunCtlError("evaluator contract authority 无效")
        scenario = load_scenario(args.scenario)
        repository = (args.repository or [None])[0]
        if len(args.repository or []) != 1 or not isinstance(repository, str):
            raise RunCtlError("evaluator contract 必须绑定唯一 staged repository")
        scopes = _source_scopes(args.source_scope or [], [repository])
        contract = {
            "schema_version": "1.0", "mode": scenario["contract_mode"], "goal": args.goal,
            "target": args.target, "repositories": [repository], "analysis_depth": args.analysis_depth,
            "mr_url": None, "repository_commits": {repository: evaluator_contract["repository_commit"]},
            "source_scopes": scopes,
            "source_scopes_sha256": _canonical_json_sha256({repository: scopes[repository]}),
            "version": args.version, "topology": args.topology,
            "test_focus": args.test_focus or [], "input_refs": args.input_ref or [],
            "excluded_scope": args.exclude or [], "tool_gaps": args.tool_gap or [],
            "known_gaps": args.known_gap or [], "signals": args.signal or [],
            "resource_emphasis": bool(args.resource_emphasis), "created_by": args.created_by,
        }
        _assert_formal_task_contract(contract)
        binding = _preflight_binding(root, [])
    contract_id = args.contract_id or f"{args.scenario}-{slug(args.target)}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path = _contract_record_path(root, contract_id, create_dir=True)
    required = contract["mode"] == "module_analysis" and contract["analysis_depth"] == "complete"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    record = {
        "artifact_type": "task_contract_record", "schema_version": "1.0",
        "contract_id": contract_id, "revision": 1, "status": "draft", "confirmation_required": required,
        "confirmation_policy": "user_required" if required else "auto_unambiguous",
        "task_contract": contract, "preflight": binding, "created_at": now, "updated_at": now,
        "confirmation": None, "activation": None,
    }
    validate(record, "contract-record.schema.json")
    data_runtime.atomic_write_json(path, record)
    print(json.dumps({"contract_id": contract_id, "status": "draft", "record": str(path),
                      "task_contract": contract, "confirmation_required": required,
                      "required_user_action": "请确认分析范围并说明是否还有补充材料" if required else None,
                      "next_step": "confirm-contract-v2"}, ensure_ascii=False))



def revise_contract_v2(args: argparse.Namespace) -> None:
    """Replace a draft canonical contract after user scope/material feedback."""
    from runtime import data_runtime, workspace_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    workspace_runtime.validate_project_root(root)
    path, record = _load_contract_record(root, args.contract_id)
    if record["status"] != "draft":
        raise RunCtlError("只有 draft 任务契约可以修订")
    if record["revision"] != args.expected_revision:
        raise RunCtlError(
            f"任务契约 revision 已变化: expected={args.expected_revision}, current={record['revision']}"
        )
    revised = _assert_formal_task_contract(read_json(Path(args.file).resolve()))
    repositories = _registered_repositories(root, revised["repositories"])
    if revised["mode"] == "module_analysis":
        revised = dict(revised)
        revised["repository_commits"] = _repository_commits(root, [], repositories, "module_analysis")
    else:
        raw = [f"{name}={value}" for name, value in revised.get("repository_commits", {}).items()]
        _repository_commits(root, raw, repositories, "mr_regression")
    binding = _preflight_binding(root, repositories)
    required = revised["mode"] == "module_analysis" and revised["analysis_depth"] == "complete"
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    record.update({
        "revision": record["revision"] + 1,
        "task_contract": revised,
        "confirmation_required": required,
        "confirmation_policy": "user_required" if required else "auto_unambiguous",
        "preflight": binding,
        "confirmation": None,
        "activation": None,
        "updated_at": now,
    })
    validate(record, "contract-record.schema.json")
    data_runtime.atomic_write_json(path, record)
    print(json.dumps({"contract_id": args.contract_id, "status": "draft",
                      "revision": record["revision"], "task_contract": revised,
                      "confirmation_required": required, "next_step": "confirm-contract-v2"},
                     ensure_ascii=False))

def confirm_contract_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, workspace_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    workspace_runtime.validate_project_root(root)
    path, record = _load_contract_record(root, args.contract_id)
    if record["status"] != "draft":
        raise RunCtlError("只有 draft 任务契约可以确认")
    if record["revision"] != args.revision:
        raise RunCtlError(
            f"任务契约 revision 已变化: requested={args.revision}, current={record['revision']}"
        )
    if record["confirmation_required"] and args.source not in {"user_reply", "user_explicit_bypass"}:
        raise RunCtlError("完整型模块分析必须由用户回复或用户明确免确认，禁止自动确认")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    confirmation = {"confirmed_revision": record["revision"], "source": args.source,
                    "materials_status": args.materials_status, "note": args.note, "confirmed_at": now}
    record.update({"status": "confirmed", "confirmation": confirmation, "updated_at": now})
    validate(record, "contract-record.schema.json")
    data_runtime.atomic_write_json(path, record)
    print(json.dumps({"contract_id": args.contract_id, "status": "confirmed",
                      "confirmation": confirmation, "next_step": "activate-contract-v2"}, ensure_ascii=False))


def _activation_marker(run_dir: Path, contract_id: str, revision: int) -> dict[str, Any]:
    marker_path = run_dir / ACTIVATION_PENDING_RELATIVE
    if marker_path.is_symlink() or not marker_path.is_file():
        raise RunCtlError(f"激活 Run 缺少本次操作所有权标记: {run_dir.name}")
    marker = read_json(marker_path)
    expected = {"artifact_type": "activation_pending", "contract_id": contract_id, "revision": revision}
    if marker != expected:
        raise RunCtlError(f"激活 Run 所有权标记与当前任务契约不一致: {run_dir.name}")
    return marker


def _rollback_activation_run(root: Path, run_id: str, contract_id: str, revision: int) -> None:
    """Remove only a checkpoint-free Run carrying this activation's ownership marker."""
    from runtime import data_runtime
    workspace = data_runtime.ensure_layout(root)
    runs = workspace / "runs"
    if (not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id
            or run_id in {".", ".."}):
        raise RunCtlError("拒绝回滚非规范 Run id")
    run_dir = runs / run_id
    if not run_dir.exists() and not run_dir.is_symlink():
        return
    try:
        run_stat = run_dir.lstat()
        runs_resolved = runs.resolve(strict=True)
        expected = runs_resolved / run_id
    except OSError as exc:
        raise RunCtlError(f"拒绝回滚不可解析的激活 Run: {run_id}") from exc
    if (run_dir != expected or stat.S_ISLNK(run_stat.st_mode) or not stat.S_ISDIR(run_stat.st_mode)
            or run_stat.st_uid != os.geteuid() or run_dir.resolve(strict=True) != expected):
        raise RunCtlError(f"拒绝回滚不安全的激活 Run: {run_dir}")
    _activation_marker(run_dir, contract_id, revision)
    root_record_path = workspace / "contracts" / contract_id / "contract.json"
    root_record, _ = _stable_unique_json(root_record_path, "rollback root contract record")
    if (root_record.get("status") != "confirmed" or root_record.get("activation") is not None
            or root_record.get("contract_id") != contract_id
            or root_record.get("revision") != revision):
        raise RunCtlError("拒绝回滚已发布 activation 的 Run")
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
        raise RunCtlError(f"拒绝回滚 manifest 不匹配的激活 Run: {run_id}")
    if manifest.get("status") != "active" or manifest.get("checkpoint_count") != 0 or manifest.get("deliverables") is not None:
        raise RunCtlError(f"拒绝回滚已有分析工件或已结束的 Run: {run_id}")
    members = [run_dir, *run_dir.rglob("*")]
    observed: list[tuple[Path, os.stat_result]] = []
    for member in members:
        value = member.lstat()
        if (value.st_uid != os.geteuid() or stat.S_ISLNK(value.st_mode)
                or not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode))):
            raise RunCtlError(f"拒绝回滚包含非 owner 普通成员的激活 Run: {run_id}")
        observed.append((member, value))
    for member, value in sorted(observed, key=lambda item: len(item[0].parts), reverse=True):
        if stat.S_ISDIR(value.st_mode):
            member.chmod(stat.S_IMODE(value.st_mode) | 0o700)
    shutil.rmtree(run_dir)


def _activation_payload(run_dir: Path, contract_id: str) -> dict[str, Any]:
    from runtime import data_runtime
    return {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "contract": data_runtime.read_json(run_dir / "internal" / "task-contract.json"),
        "plan": data_runtime.read_json(run_dir / "internal" / "workflow-plan.json"),
        "source_snapshots": data_runtime.read_json(run_dir / "internal" / "source-snapshots.json", None),
        "contract_id": contract_id,
        "contract_status": "activated",
        "contract_record": str(run_dir / CONTRACT_RECORD_RELATIVE),
    }


def activate_contract_v2(args: argparse.Namespace) -> None:
    from runtime import data_runtime, workspace_runtime
    root = Path(args.root).resolve() if args.root else ROOT
    workspace_runtime.validate_project_root(root)
    path, record = _load_contract_record(root, args.contract_id)
    if record["status"] not in {"confirmed", "activated"} or not isinstance(record.get("confirmation"), dict):
        raise RunCtlError("任务契约尚未确认，禁止创建 Run 或源码快照")
    if record["confirmation"].get("confirmed_revision") != record["revision"]:
        raise RunCtlError("任务契约确认未绑定当前 revision")
    evaluator_source = getattr(args, "_evaluator_source_binding", None)
    evaluator_intake = getattr(args, "_evaluator_intake_artifacts", None)
    if ((evaluator_source is None) != (evaluator_intake is None)
            or (evaluator_intake is not None
                and (not isinstance(evaluator_intake, dict)
                     or set(evaluator_intake) != {"spec", "spec_sha256", "confirmed_record"}
                     or evaluator_intake.get("confirmed_record") != record))):
        raise RunCtlError("evaluator intake activation artifacts 无效")
    current_binding = _preflight_binding(
        root, [] if evaluator_source is not None else record["task_contract"]["repositories"],
    )
    if current_binding != record["preflight"]:
        raise RunCtlError("preflight receipt 在契约确认前后发生变化，请重新生成任务契约")

    run_id = args.run_id or args.contract_id
    workspace = data_runtime.ensure_layout(root)
    run_dir = workspace / "runs" / run_id
    if run_dir.exists() or run_dir.is_symlink():
        run_record_path = run_dir / CONTRACT_RECORD_RELATIVE
        if run_record_path.is_file():
            run_record = read_json(run_record_path)
            if (run_record.get("status") == "activated"
                    and run_record.get("contract_id") == args.contract_id
                    and run_record.get("revision") == record["revision"]
                    and run_record.get("activation", {}).get("run_id") == run_id
                    and run_record.get("task_contract") == record["task_contract"]):
                data_runtime.atomic_write_json(path, run_record)
                try:
                    (run_dir / ACTIVATION_PENDING_RELATIVE).unlink()
                except FileNotFoundError:
                    pass
                print(json.dumps(_activation_payload(run_dir, args.contract_id), ensure_ascii=False))
                return
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        canonical = data_runtime.read_json(run_dir / "internal" / "task-contract.json")
        if (isinstance(manifest, dict) and manifest.get("run_id") == run_id
                and manifest.get("status") == "active" and manifest.get("checkpoint_count") == 0
                and manifest.get("deliverables") is None and canonical == record["task_contract"]):
            _rollback_activation_run(root, run_id, args.contract_id, record["revision"])
        else:
            raise RunCtlError(f"Run 已存在且不属于可恢复的当前任务契约: {run_id}")

    contract = record["task_contract"]
    scenario_name = "mr-regression" if contract["mode"] == "mr_regression" else "module-analysis"
    namespace = argparse.Namespace(
        root=str(root), scenario=scenario_name, target=contract["target"], repository=contract["repositories"],
        repository_commit=[f"{name}={value}" for name, value in contract.get("repository_commits", {}).items()],
        run_id=run_id, mr_url=contract.get("mr_url"), goal=contract.get("goal"),
        analysis_depth=contract.get("analysis_depth"), version=contract.get("version"), topology=contract.get("topology"),
        test_focus=contract.get("test_focus"), input_ref=contract.get("input_refs"), exclude=contract.get("excluded_scope"),
        tool_gap=contract.get("tool_gaps"), known_gap=contract.get("known_gaps"), signal=contract.get("signals"),
        resource_emphasis=contract.get("resource_emphasis", False), created_by=contract.get("created_by"),
        max_audit_rounds=args.max_audit_rounds, _canonical_contract=contract, _return_payload=True,
        _evaluator_source_binding=getattr(args, "_evaluator_source_binding", None),
        _activation_pending={"artifact_type": "activation_pending", "contract_id": args.contract_id,
                             "revision": record["revision"]},
    )
    try:
        payload = create_v2_run(namespace)
        expected_run_dir = workspace / "runs" / run_id
        run_dir = Path(payload["run_dir"])
        run_stat = run_dir.lstat()
        if (run_dir != expected_run_dir or stat.S_ISLNK(run_stat.st_mode)
                or not stat.S_ISDIR(run_stat.st_mode) or run_stat.st_uid != os.geteuid()
                or run_dir.resolve(strict=True) != expected_run_dir):
            raise RunCtlError("激活返回了非本次 expected Run 路径")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        activated = json.loads(json.dumps(record, ensure_ascii=False))
        activated.update({"status": "activated", "updated_at": now,
                          "activation": {"run_id": payload["run_id"], "activated_at": now}})
        validate(activated, "contract-record.schema.json")
        data_runtime.atomic_write_json(run_dir / CONTRACT_RECORD_RELATIVE, activated)
        data_runtime.atomic_write_json(run_dir / CONTRACT_CONFIRMATION_RELATIVE, activated["confirmation"])
        if evaluator_intake is not None:
            intake_spec = evaluator_intake["spec"]
            intake_spec_sha256 = evaluator_intake["spec_sha256"]
            if (not isinstance(intake_spec, dict) or not isinstance(intake_spec_sha256, str)
                    or intake_spec.get("expected_run_id") != run_id
                    or intake_spec.get("contract", {}).get("id") != args.contract_id
                    or intake_spec.get("contract", {}).get("confirmed_sha256")
                    != _canonical_json_sha256(record)):
                raise RunCtlError("evaluator intake activation projection 无效")
            _write_evaluator_json_exclusive(
                run_dir / EVALUATOR_CONFIRMED_CONTRACT_RELATIVE, record, canonical=True,
            )
            _write_evaluator_json_exclusive(
                run_dir / EVALUATOR_INTAKE_RUN_SPEC_RELATIVE, intake_spec, canonical=False,
            )
            if _sha256_file(run_dir / EVALUATOR_INTAKE_RUN_SPEC_RELATIVE) != intake_spec_sha256:
                raise RunCtlError("evaluator intake Run spec bytes 漂移")
            _write_evaluator_json_exclusive(
                run_dir / EVALUATOR_INTAKE_BINDING_RELATIVE,
                _evaluator_intake_run_binding(intake_spec, intake_spec_sha256), canonical=False,
            )
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        manifest["contract_record_file"] = CONTRACT_RECORD_RELATIVE
        manifest["contract_confirmation_file"] = CONTRACT_CONFIRMATION_RELATIVE
        validate(manifest, "session-manifest.schema.json")
        data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    except BaseException as exc:
        try:
            _rollback_activation_run(root, run_id, args.contract_id, record["revision"])
        except BaseException as rollback_exc:
            raise RunCtlError(f"任务契约激活失败且安全回滚失败: {exc}; rollback: {rollback_exc}") from exc
        raise

    # Publish durable state last. If this write fails, the bound Run is retained and a retry
    # takes the idempotent path instead of deleting a successfully activated Run.
    data_runtime.atomic_write_json(path, activated)
    try:
        (run_dir / ACTIVATION_PENDING_RELATIVE).unlink()
    except FileNotFoundError:
        pass
    print(json.dumps({**payload, "contract_id": args.contract_id, "contract_status": "activated",
                      "contract_record": str(run_dir / CONTRACT_RECORD_RELATIVE)}, ensure_ascii=False))


def _validate_evaluator_intake_spec_value(spec: Any) -> dict[str, Any]:
    """Validate every member and intrinsic relation of an evaluator intake spec."""
    digest = re.compile(r"[0-9a-f]{64}"); object_id = re.compile(r"[0-9a-f]{40}")
    top = {"artifact_type", "schema_version", "owner", "mode", "case", "task", "contract",
           "repository", "source_scope_sha256", "expected_run_id"}
    if (not isinstance(spec, dict) or set(spec) != top
            or spec.get("artifact_type") != "evaluator_intake_spec"
            or spec.get("schema_version") != "2.0" or spec.get("owner") != "evaluator"
            or spec.get("mode") != "canonical-case-one-shot-v2"):
        raise RunCtlError("evaluator intake spec 顶层契约无效")
    case = spec.get("case"); task = spec.get("task"); contract = spec.get("contract"); repository = spec.get("repository")
    if (not isinstance(case, dict) or set(case) != {"path", "id", "sha256"}
            or case.get("path") != "CASE.json" or not isinstance(case.get("id"), str) or not case["id"]
            or not digest.fullmatch(str(case.get("sha256", "")))
            or not isinstance(task, dict) or set(task) != {"path", "sha256", "agent_input_sha256"}
            or task.get("path") != "TASK.md"
            or any(not digest.fullmatch(str(task.get(key, ""))) for key in ("sha256", "agent_input_sha256"))
            or not isinstance(contract, dict)
            or set(contract) != {"id", "record_path", "confirmed_sha256", "revision", "task_contract_sha256",
                                 "confirmation_source", "materials_status"}
            or type(contract.get("revision")) is not int or contract["revision"] != 1
            or any(not digest.fullmatch(str(contract.get(key, "")))
                   for key in ("confirmed_sha256", "task_contract_sha256"))
            or contract.get("confirmation_source") not in {"user_reply", "user_explicit_bypass", "auto_unambiguous"}
            or contract.get("materials_status") not in {"provided", "confirmed_none", "unchanged"}
            or not isinstance(repository, dict)
            or set(repository) != {"id", "url", "commit", "git_tree", "materialization_sha256",
                                   "stage_repository_sha256", "source_manifest_sha256"}
            or not isinstance(repository.get("id"), str) or not repository["id"]
            or not isinstance(repository.get("url"), str) or not repository["url"]
            or not object_id.fullmatch(str(repository.get("commit", "")))
            or not object_id.fullmatch(str(repository.get("git_tree", "")))
            or any(not digest.fullmatch(str(repository.get(key, ""))) for key in
                   ("materialization_sha256", "stage_repository_sha256", "source_manifest_sha256"))
            or not digest.fullmatch(str(spec.get("source_scope_sha256", "")))
            or contract.get("id") != "case-" + case["sha256"]
            or contract.get("record_path") != f"pangea-data/contracts/{contract.get('id')}/contract.json"
            or spec.get("expected_run_id") != contract.get("id")):
        raise RunCtlError("evaluator intake spec 字段闭包无效")
    return dict(spec)


def _validate_evaluator_intake_spec(root: Path) -> tuple[dict[str, Any], str, Path]:
    """Validate the evaluator-owned one-shot specification and its path closures."""
    workspace = root / "pangea-data"
    session = workspace / "session"
    spec_path = workspace / EVALUATOR_INTAKE_SPEC_RELATIVE
    try:
        session_members = set(os.listdir(session))
    except OSError as exc:
        raise RunCtlError("evaluator intake session 目录不可用") from exc
    if session_members != {"preflight-receipt.json", "evaluator-intake-spec.json"}:
        raise RunCtlError("evaluator intake session 成员闭包不唯一")
    spec, spec_payload = _stable_unique_json(spec_path, "evaluator intake spec", read_only=True)
    _validate_evaluator_intake_spec_value(spec)
    case = spec.get("case"); task = spec.get("task"); contract = spec.get("contract"); repository = spec.get("repository")
    contracts = workspace / "contracts"
    members = list(contracts.iterdir()) if contracts.is_dir() and not contracts.is_symlink() else []
    directory = contracts / contract["id"]
    if (len(members) != 1 or members[0] != directory or directory.is_symlink() or not directory.is_dir()
            or set(os.listdir(directory)) != {"contract.json"}):
        raise RunCtlError("evaluator intake contract record 闭包不唯一")
    case_value, case_payload = _stable_unique_json(root / "CASE.json", "canonical CASE", read_only=True)
    task_path = root / "TASK.md"
    try:
        task_payload = task_path.read_bytes()
    except OSError as exc:
        raise RunCtlError("canonical TASK 不可用") from exc
    if (hashlib.sha256(case_payload).hexdigest() != case["sha256"] or case_value.get("id") != case["id"]
            or hashlib.sha256(task_payload).hexdigest() != task["sha256"]
            or hashlib.sha256(str(case_value.get("agent_input", "")).encode("utf-8")).hexdigest() != task["agent_input_sha256"]
            or task_payload.decode("utf-8").splitlines().count(case_value.get("agent_input")) != 1
            or case_value.get("repository_id") != repository["id"]
            or case_value.get("repository_url") != repository["url"]
            or case_value.get("frozen_commit") != repository["commit"]):
        raise RunCtlError("evaluator intake canonical case/task binding drift")
    stage_receipt, _ = _stable_unique_json(root / "stage-receipt.json", "public stage receipt")
    rows = stage_receipt.get("repositories")
    staged = next((row for row in rows if isinstance(row, dict) and row.get("id") == repository["id"]), None) \
        if isinstance(rows, list) else None
    source_manifest = _evaluator_source_manifest(root, repository["id"])
    if (not isinstance(staged, dict) or len(rows) != 2
            or staged.get("commit") != repository["commit"]
            or staged.get("git_tree") != repository["git_tree"]
            or staged.get("materialization_sha256") != repository["materialization_sha256"]
            or _canonical_json_sha256(staged) != repository["stage_repository_sha256"]
            or _canonical_json_sha256(source_manifest) != repository["source_manifest_sha256"]):
        raise RunCtlError("evaluator intake staged repository materialization drift")
    record_path = root / contract["record_path"]
    confirmed, _ = _stable_unique_json(
        record_path, "evaluator confirmed contract record", read_only=True,
    )
    if (confirmed.get("status") != "confirmed"
            or confirmed.get("contract_id") != contract["id"]
            or confirmed.get("revision") != contract["revision"]
            or _canonical_json_sha256(confirmed) != contract["confirmed_sha256"]
            or _canonical_json_sha256(confirmed.get("task_contract")) != contract["task_contract_sha256"]):
        raise RunCtlError("evaluator confirmed contract record binding drift")
    return spec, hashlib.sha256(spec_payload).hexdigest(), record_path


def _evaluator_source_manifest(root: Path, repository: str) -> dict[str, Any]:
    manifest, _ = _stable_unique_json(root / "public-bundle-manifest.json", "public bundle manifest")
    files = manifest.get("files"); directories = manifest.get("directories")
    prefix = f"repositories/{repository}"
    if not isinstance(files, dict) or not isinstance(directories, list):
        raise RunCtlError("public bundle manifest source closure 无效")
    source_files = [{"path": path, "sha256": value} for path, value in sorted(files.items())
                    if isinstance(path, str) and path.startswith(prefix + "/")]
    source_directories = sorted(path for path in directories
                                if isinstance(path, str) and (path == prefix or path.startswith(prefix + "/")))
    source_root = root / prefix
    observed_files: list[str] = []
    observed_directories: list[str] = [prefix] if source_root.is_dir() and not source_root.is_symlink() else []
    for path in source_root.rglob("*") if observed_directories else []:
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise RunCtlError("staged repository 包含非普通成员")
        if stat.S_ISDIR(mode):
            observed_directories.append(relative)
        else:
            observed_files.append(relative)
    expected = {row["path"]: row["sha256"] for row in source_files}
    if (sorted(observed_files) != sorted(expected) or sorted(observed_directories) != source_directories
            or any(_sha256_file(root / path) != expected[path] for path in observed_files)):
        raise RunCtlError("staged repository 文件闭包与 public manifest 不一致")
    return {"files": source_files, "directories": source_directories}


def _evaluator_intake_active_closure(root: Path, spec: dict[str, Any], spec_sha256: str,
                                     record: dict[str, Any]) -> dict[str, Any]:
    """Accept only the exact activated Run created from this immutable spec."""
    run_id = spec["expected_run_id"]
    runs = root / "pangea-data" / "runs"
    members = list(runs.iterdir()) if runs.is_dir() and not runs.is_symlink() else []
    run_dir = runs / run_id
    if (len(members) != 1 or members[0] != run_dir or run_dir.is_symlink() or not run_dir.is_dir()
            or record.get("status") != "activated"
            or record.get("contract_id") != spec["contract"]["id"]
            or record.get("revision") != spec["contract"]["revision"]
            or record.get("activation", {}).get("run_id") != run_id):
        raise RunCtlError("evaluator intake activated Run 闭包不唯一")
    binding_path = run_dir / EVALUATOR_INTAKE_BINDING_RELATIVE
    binding, _ = _stable_unique_json(binding_path, "evaluator intake Run binding", read_only=True)
    run_spec, run_spec_payload = _stable_unique_json(
        run_dir / EVALUATOR_INTAKE_RUN_SPEC_RELATIVE,
        "evaluator intake Run spec", read_only=True,
    )
    _validate_evaluator_intake_spec_value(run_spec)
    confirmed, confirmed_payload = _stable_unique_json(
        run_dir / EVALUATOR_CONFIRMED_CONTRACT_RELATIVE,
        "evaluator durable confirmed contract", read_only=True,
    )
    evaluator_members = {
        path.name for path in (run_dir / "internal").iterdir()
        if path.name.startswith("evaluator-")
    }
    if (evaluator_members != {
            Path(EVALUATOR_INTAKE_BINDING_RELATIVE).name,
            Path(EVALUATOR_INTAKE_RUN_SPEC_RELATIVE).name,
            Path(EVALUATOR_CONFIRMED_CONTRACT_RELATIVE).name,
        }
            or binding != _evaluator_intake_run_binding(spec, spec_sha256) or run_spec != spec
            or hashlib.sha256(run_spec_payload).hexdigest() != spec_sha256):
        raise RunCtlError("evaluator intake Run binding 与 spec 不一致")
    canonical = read_json(run_dir / "internal/task-contract.json")
    if (_canonical_json_sha256(canonical) != spec["contract"]["task_contract_sha256"]
            or confirmed_payload != _canonical_json_bytes(confirmed)
            or _canonical_json_sha256(confirmed) != spec["contract"]["confirmed_sha256"]
            or confirmed.get("status") != "confirmed"
            or confirmed.get("task_contract") != canonical
            or read_json(run_dir / CONTRACT_RECORD_RELATIVE) != record):
        raise RunCtlError("evaluator intake active contract 内容漂移")
    return {"run_id": run_id, "status": "active", "idempotent": True}


def _canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _write_evaluator_json_exclusive(path: Path, value: dict[str, Any], *, canonical: bool) -> None:
    """Exclusively publish one evaluator-owned JSON file without replacement."""
    payload = (_canonical_json_bytes(value) if canonical else
               (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    created: os.stat_result | None = None
    try:
        descriptor = os.open(path, flags, 0o400)
        created = os.fstat(descriptor)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload); handle.flush(); os.fsync(handle.fileno())
            os.fchmod(handle.fileno(), 0o400)
        named = path.lstat()
        if (not stat.S_ISREG(named.st_mode) or named.st_uid != os.geteuid()
                or named.st_nlink != 1 or named.st_mode & 0o222
                or (named.st_dev, named.st_ino) != (created.st_dev, created.st_ino)):
            raise RunCtlError("evaluator intake durable file publication failed")
        observed, observed_payload = _stable_unique_json(
            path, "evaluator intake durable file", read_only=True,
        )
        if observed != value or observed_payload != payload:
            raise RunCtlError("evaluator intake durable file bytes differ")
    except BaseException:
        if descriptor is not None:
            os.close(descriptor)
        if created is not None:
            try:
                named = path.lstat()
                if (named.st_dev, named.st_ino) == (created.st_dev, created.st_ino):
                    path.unlink()
            except FileNotFoundError:
                pass
        raise


def _evaluator_intake_run_binding(spec: dict[str, Any], spec_sha256: str) -> dict[str, Any]:
    _validate_evaluator_intake_spec_value(spec)
    return {
        "artifact_type": "evaluator_intake_binding", "schema_version": "2.0",
        "owner": "evaluator", "mode": "canonical-case-one-shot-v2",
        "spec_sha256": spec_sha256,
        "confirmed_record_sha256": spec["contract"]["confirmed_sha256"],
        "contract_id": spec["contract"]["id"], "revision": spec["contract"]["revision"],
        "run_id": spec["expected_run_id"],
    }


def _evaluator_intake_prep_command(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "api": "runtime.runctl.contract-lifecycle-v2", "mode": "direct-python-api",
        "steps": ["draft-contract-v2", "confirm-contract-v2"],
        "contract_id": spec["contract"]["id"], "revision": spec["contract"]["revision"],
    }


def _evaluator_intake_prep_result(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "confirmed", "contract_id": spec["contract"]["id"],
        "revision": spec["contract"]["revision"], "expected_run_id": spec["expected_run_id"],
    }


def _evaluator_intake_input_bindings(
    spec: dict[str, Any], spec_sha256: str, confirmed_record_sha256: str,
) -> list[dict[str, str]]:
    """Return the fixed evaluator-owned preparation binding sequence."""
    _validate_evaluator_intake_spec_value(spec)
    values = {
        "canonical_case": spec["case"]["sha256"],
        "canonical_task": spec["task"]["sha256"],
        "evaluator_intake_spec": spec_sha256,
        "confirmed_contract_record": confirmed_record_sha256,
        "stage_snapshot": _canonical_json_sha256(spec["repository"]),
        "intake_prep_command": _canonical_json_sha256(_evaluator_intake_prep_command(spec)),
        "intake_prep_result": _canonical_json_sha256(_evaluator_intake_prep_result(spec)),
    }
    return [{"name": name, "sha256": values[name]} for name in EVALUATOR_INTAKE_INPUT_BINDING_NAMES]


def evaluator_intake_v2(args: argparse.Namespace) -> None:
    """Activate exactly the evaluator-prepared canonical-case contract."""
    from runtime import workspace_runtime

    root = Path.cwd().resolve()
    workspace_runtime.validate_project_root(root)
    spec, spec_sha256, record_path = _validate_evaluator_intake_spec(root)
    record, _ = _stable_unique_json(
        record_path, "evaluator confirmed contract record", read_only=True,
    )
    contract = spec["contract"]
    if record.get("status") == "activated":
        print(json.dumps(_evaluator_intake_active_closure(root, spec, spec_sha256, record), ensure_ascii=False))
        return
    runs = root / "pangea-data" / "runs"
    if (runs.is_symlink() or not runs.is_dir() or list(runs.iterdir())
            or record.get("status") != "confirmed"
            or record.get("contract_id") != contract["id"]
            or record.get("revision") != contract["revision"]
            or record.get("confirmation", {}).get("confirmed_revision") != contract["revision"]
            or record.get("confirmation", {}).get("source") != contract["confirmation_source"]
            or record.get("confirmation", {}).get("materials_status") != contract["materials_status"]
            or _canonical_json_sha256(record) != contract["confirmed_sha256"]
            or _canonical_json_sha256(record.get("task_contract")) != contract["task_contract_sha256"]):
        raise RunCtlError("evaluator intake 仅接受唯一 confirmed contract 与零 Run")
    output = io.StringIO()
    namespace = argparse.Namespace(root=str(root), contract_id=contract["id"],
                                  run_id=spec["expected_run_id"], max_audit_rounds=2,
                                  _evaluator_source_binding=spec["repository"],
                                  _evaluator_intake_artifacts={
                                      "spec": spec, "spec_sha256": spec_sha256,
                                      "confirmed_record": record,
                                  })
    with contextlib.redirect_stdout(output):
        activate_contract_v2(namespace)
    del output
    _, activated = _load_contract_record(root, contract["id"])
    result = _evaluator_intake_active_closure(root, spec, spec_sha256, activated)
    result["idempotent"] = False
    print(json.dumps(result, ensure_ascii=False))


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
    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))
    if binding is not None:
        if model.get("analysis_artifact") != binding:
            raise RunCtlError("report-model 未精确绑定当前固定分析模型")
        from runtime import analysis_reporting
        try:
            analysis_reporting.assert_projection(model, data_runtime.read_json(_analysis_model_path(run_dir)))
        except ValueError as exc:
            raise RunCtlError(str(exc)) from exc
    return model


def _load_v2_workflow_plan(run_dir: Path) -> dict[str, Any]:
    """Load only a non-empty plan that still exactly matches the registry."""
    from runtime import data_runtime

    contract = data_runtime.read_json(run_dir / "internal" / "task-contract.json")
    _assert_run_contract_lifecycle(run_dir)
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
                "dfx_dimensions": selected, "signals": contract.get("signals", []),
                "stages": scenario["stages"]}
    scenario = load_scenario("module-analysis")
    return {"workflow": "module-analysis", "dfx_dimensions": DFX_DIMENSIONS,
            "resource_deep_dive": bool(contract.get("resource_emphasis")) or any(
                word in " ".join(contract.get("signals", [])).lower()
                for word in ("resource", "queue", "pool", "counter", "memory", "资源", "队列", "计数", "内存")),
            "stages": scenario["stages"]}


def _create_evaluator_source_snapshot(root: Path, run_dir: Path, source: dict[str, Any]) -> dict[str, Any]:
    """Copy the already-attested public materialization into the managed Run."""
    from runtime import repository_runtime

    required = {"id", "url", "commit", "git_tree", "materialization_sha256",
                "stage_repository_sha256", "source_manifest_sha256"}
    if not isinstance(source, dict) or set(source) != required:
        raise RunCtlError("evaluator source binding 无效")
    manifest = _evaluator_source_manifest(root, source["id"])
    if _canonical_json_sha256(manifest) != source["source_manifest_sha256"]:
        raise RunCtlError("evaluator source manifest binding drift")
    source_root = root / "repositories" / source["id"]
    destination = run_dir / "tmp" / "snapshots" / source["id"]
    if destination.exists() or destination.is_symlink():
        raise RunCtlError("evaluator source snapshot 不得预建或替换")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source_root, destination, symlinks=False)
        destination.chmod(stat.S_IMODE(destination.lstat().st_mode) | 0o700)
        snapshot_manifest = {
            "schema_version": "1.1", "repository": source["id"],
            "source_repository": f"repositories/{source['id']}",
            "requested_ref": source["commit"], "commit_sha": source["commit"],
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "file_count": len(manifest["files"]),
            "content_sha256": repository_runtime._snapshot_content_sha256(destination),
            "coverage_gaps": [],
            "evaluator_materialization": {
                "git_tree": source["git_tree"],
                "materialization_sha256": source["materialization_sha256"],
                "stage_repository_sha256": source["stage_repository_sha256"],
                "source_manifest_sha256": source["source_manifest_sha256"],
            },
        }
        manifest_path = destination / repository_runtime.MANIFEST_NAME
        _write_evaluator_json_exclusive(
            manifest_path, snapshot_manifest, canonical=False,
        )
        _verify_evaluator_snapshot_closure(
            destination, manifest, snapshot_manifest, read_only=False,
        )
        repository_runtime._make_read_only(destination)
        _verify_evaluator_snapshot_closure(
            destination, manifest, snapshot_manifest, read_only=True,
        )
    except BaseException:
        if destination.exists() and not destination.is_symlink():
            repository_runtime._make_removable(destination)
            shutil.rmtree(destination)
        raise
    item = {"snapshot_id": source["id"], "repository": source["id"],
            "snapshot_dir": str(destination), "manifest": snapshot_manifest}
    return {"snapshots": [item], "coverage_gaps": []}


def _verify_evaluator_snapshot_closure(
    destination: Path, source_manifest: dict[str, Any], snapshot_manifest: dict[str, Any],
    *, read_only: bool,
) -> None:
    """Verify the exact copied staged tree before and after read-only sealing."""
    from runtime import repository_runtime

    prefix = f"repositories/{snapshot_manifest['repository']}"
    expected_files = {
        row["path"][len(prefix) + 1:]: row["sha256"] for row in source_manifest["files"]
    }
    expected_directories = {
        "." if path == prefix else path[len(prefix) + 1:]
        for path in source_manifest["directories"]
    }
    observed_files: dict[str, str] = {}
    observed_directories = {"."}
    for path in destination.rglob("*"):
        relative = path.relative_to(destination).as_posix()
        value = path.lstat()
        if (value.st_uid != os.geteuid() or stat.S_ISLNK(value.st_mode)
                or not (stat.S_ISDIR(value.st_mode) or stat.S_ISREG(value.st_mode))
                or (read_only and value.st_mode & 0o222)):
            raise RunCtlError("evaluator source snapshot member closure 无效")
        if stat.S_ISDIR(value.st_mode):
            observed_directories.add(relative)
        elif relative != repository_runtime.MANIFEST_NAME:
            if value.st_nlink != 1:
                raise RunCtlError("evaluator source snapshot file link closure 无效")
            observed_files[relative] = _sha256_file(path)
    root_stat = destination.lstat()
    if (root_stat.st_uid != os.geteuid() or (read_only and root_stat.st_mode & 0o222)
            or observed_files != expected_files or observed_directories != expected_directories):
        raise RunCtlError("evaluator source snapshot content closure 无效")
    persisted, payload = _stable_unique_json(
        destination / repository_runtime.MANIFEST_NAME,
        "evaluator snapshot manifest", read_only=True,
    )
    expected_payload = (json.dumps(snapshot_manifest, ensure_ascii=False, indent=2) + "\n").encode()
    if (persisted != snapshot_manifest or payload != expected_payload
            or snapshot_manifest.get("file_count") != len(expected_files)
            or repository_runtime._snapshot_content_sha256(destination)
            != snapshot_manifest.get("content_sha256")):
        raise RunCtlError("evaluator source snapshot manifest/hash closure 无效")


def create_v2_run(args: argparse.Namespace) -> dict[str, Any] | None:
    """Create a v2 Run; marked project roots require an activated contract."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    canonical = getattr(args, "_canonical_contract", None)
    if canonical is None and _marked_project_root(root):
        raise RunCtlError(
            "正式项目根禁止直接 create-v2；请依次使用 draft-contract-v2、confirm-contract-v2、activate-contract-v2"
        )
    evaluator_source = getattr(args, "_evaluator_source_binding", None)
    if canonical is None:
        scenario, contract = _contract_from_args(args, root)
    else:
        contract = _assert_formal_task_contract(canonical)
        scenario_name = "mr-regression" if contract["mode"] == "mr_regression" else "module-analysis"
        scenario = load_scenario(scenario_name)
        if evaluator_source is None:
            registered = _registered_repositories(root, contract["repositories"])
            if registered != contract["repositories"]:
                raise RunCtlError("激活时仓库登记集合与任务契约不一致")
        else:
            if (not isinstance(evaluator_source, dict)
                    or contract["repositories"] != [evaluator_source.get("id")]):
                raise RunCtlError("evaluator staged repository 与任务契约不一致")
            registered = list(contract["repositories"])
        expected = contract.get("repository_commits")
        if not isinstance(expected, dict) or set(expected) != set(registered):
            raise RunCtlError("激活任务契约缺少完整 repository_commits")
    mode = contract["mode"]
    repositories = contract["repositories"]
    repository_commits = contract.get("repository_commits")
    run_id = args.run_id or f"{scenario_name if canonical is not None else args.scenario}-{slug(contract['target'])}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    created = data_runtime.create_run(root, run_id, contract, args.max_audit_rounds)
    run_dir = Path(created["run_dir"])
    activation_pending = getattr(args, "_activation_pending", None)
    if activation_pending is not None:
        if not isinstance(activation_pending, dict):
            raise RunCtlError("activation pending marker 必须是 JSON 对象")
        atomic_write(run_dir / ACTIVATION_PENDING_RELATIVE, activation_pending)
    plan = v2_plan(contract)
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest["audit"]["rework"] = None
    validate(manifest, "session-manifest.schema.json")
    data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
    atomic_write(run_dir / "internal" / "workflow-plan.json", plan)
    source_snapshots: dict[str, Any] | None = None
    state_message = "已建立任务契约，准备共享代码地图"
    if mode == "module_analysis":
        if evaluator_source is not None:
            source_snapshots = _create_evaluator_source_snapshot(root, run_dir, evaluator_source)
        else:
            from runtime import repository_runtime
            specs = [{"repository": repository, "ref": repository_commits[repository], "snapshot_id": repository}
                     for repository in repositories]
            source_snapshots = repository_runtime.create_snapshots(root, run_id, specs)
        atomic_write(run_dir / "internal" / "source-snapshots.json", source_snapshots)
        state_message = ("已绑定仓库 commit；部分只读快照不可用，按覆盖缺口继续"
                         if source_snapshots["coverage_gaps"] else
                         "已绑定仓库 commit 并创建只读源码快照，准备共享代码地图")
    data_runtime.set_run_state(root, run_id, "mapping", state_message)
    payload = {"run_id": run_id, "run_dir": str(run_dir), "contract": contract,
               "plan": plan, "source_snapshots": source_snapshots,
               "validation_backend": validate(contract, "task-contract.schema.json")}
    if getattr(args, "_return_payload", False):
        return payload
    print(json.dumps(payload, ensure_ascii=False))
    return None


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
    checkpoint_files = (sorted(checkpoint_dir.iterdir(), key=lambda item: item.name)
                        if checkpoint_dir.is_dir() else [])
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
        if not isinstance(dfx_facts, list) or len(dfx_facts) != len(DFX_DIMENSIONS):
            raise RunCtlError("模块 dfx_scan 必须恰好记录六个 canonical DFX 事实")
        covered: list[str] = []
        for fact in dfx_facts:
            if not isinstance(fact, dict) or not isinstance(fact.get("dfx"), str) or not _meaningful_text(fact.get("conclusion"), 4) or not _meaningful_text(fact.get("evidence"), 4):
                raise RunCtlError("模块 dfx_scan 每维必须包含 dfx、具体 conclusion 与 evidence")
            covered.append(fact["dfx"])
        if set(covered) != set(DFX_DIMENSIONS) or len(covered) != len(set(covered)):
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
                      "deliverables": manifest.get("deliverables"),
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




def _coverage_judge_path(run_dir: Path) -> Path:
    internal = (run_dir / "internal").resolve()
    path = run_dir / COVERAGE_JUDGE_RELATIVE
    if path.is_symlink() or path.resolve().parent != internal:
        raise RunCtlError("Coverage Judge 工件不得通过符号链接指向 Run 外部")
    return path.resolve()


def _binding(path: Path, relative: str) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise RunCtlError(f"Judge 绑定工件不存在或不是普通文件: {relative}")
    return {"path": relative, "sha256": _sha256_file(path)}


def _judge_required(contract: dict[str, Any]) -> bool:
    return contract.get("mode") == "module_analysis" and contract.get("analysis_depth") == "complete"

def _r2_judge_file_bindings(run_dir:Path, repositories:list[str]) -> list[dict[str,str]]:
    paths=[run_dir/"internal/task-contract.json",run_dir/"internal/contract-record.json",
           run_dir/"internal/contract-confirmation.json",run_dir/"internal/source-snapshots.json",
           run_dir/"internal/denominator-state.json",run_dir/"internal/context-publication-state.json",
           run_dir/"internal/source-bindings.json",run_dir/"internal/inventory-index.json",
           run_dir/"internal/obligation-index.json",run_dir/"internal/assignment-index.json",
           _analysis_model_path(run_dir),_fixed_audit_model(run_dir),run_dir/"internal/risk-ledger.json"]
    paths += [run_dir/f"internal/inventories/{repo}.json" for repo in repositories]
    paths += [run_dir/f"internal/ledgers/{repo}.json" for repo in repositories]
    paths += [run_dir/f"internal/baseline-ledgers/{repo}.json" for repo in repositories]
    for pattern in ("internal/fragments/*.json","internal/transactions/*.json","internal/telemetry/*.json",
                    "internal/semantic-assessments/*.json","internal/execution-receipts/*.json",
                    "internal/compact-native-outputs/*.json","internal/compact-adapter-receipts/*.json",
                    "internal/context-packs/*/CONTEXT.json"):
        paths += sorted(run_dir.glob(pattern))
    relative=[str(path.relative_to(run_dir)) for path in paths]
    if len(relative)!=len(set(relative)): raise RunCtlError("R2 Judge 文件绑定存在重复路径")
    return [_binding(run_dir/rel,rel) for rel in sorted(relative)]


def _run_coverage_judge(run_dir: Path, contract: dict[str, Any]) -> dict[str, Any]:
    from runtime import coverage_judge, data_runtime

    denominator_state=run_dir/"internal/denominator-state.json"
    if denominator_state.is_file():
        from runtime import analysis_pipeline
        try: analysis_pipeline.validate_run_for_judge(run_dir.parents[2],run_dir.name)
        except analysis_pipeline.PipelineError as exc: raise RunCtlError(f"R2 replay validation failed: {exc}") from exc
        expected_projection=_expected_r2_projection(run_dir)
        analysis=data_runtime.read_json(_analysis_model_path(run_dir)); report=data_runtime.read_json(_fixed_audit_model(run_dir))
        if analysis.get("r2_projection")!=expected_projection or report.get("analysis_details",{}).get("r2_projection")!=expected_projection:
            raise RunCtlError("R2 merged contributions 未精确投影到 analysis model/report")
        risk_ledger=data_runtime.read_json(run_dir/"internal/risk-ledger.json")
        if risk_ledger.get("r2_projection")!=expected_projection:
            raise RunCtlError("R2 projection 未精确保存到 risk-ledger")
        _assert_report_risk_binding(run_dir, report)
        _assert_r2_hc_risk_binding(run_dir, report)
        expected_hc=set(expected_projection["hc_risks"]["ids"])
        ledger_hc={x.get("risk_id") for x in risk_ledger.get("risks",[]) if x.get("severity") in {"High","Critical"}}
        report_hc={x.get("risk_id") for x in report.get("risks",[]) if x.get("severity") in {"High","Critical"}}
        if ledger_hc!=expected_hc or report_hc!=expected_hc: raise RunCtlError("R2 H/C risks 未精确投影到 ledger/report")
        def pipeline_payload(path:Path) -> dict[str,Any]:
            value=data_runtime.read_json(path)
            if not isinstance(value,dict) or not isinstance(value.get("payload"),dict):
                raise RunCtlError(f"R2 Judge 工件无效: {path}")
            return value["payload"]
        repositories=contract.get("repositories",[])
        inventories=[pipeline_payload(run_dir/f"internal/inventories/{repo}.json") for repo in repositories]
        ledgers=[pipeline_payload(run_dir/f"internal/ledgers/{repo}.json") for repo in repositories]
        assignments=pipeline_payload(run_dir/"internal/assignment-index.json").get("assignments",[])
        fragments=[pipeline_payload(path) for path in sorted((run_dir/"internal/fragments").glob("*.json"))]
        native_outputs=[data_runtime.read_json(path) for path in sorted((run_dir/"internal/compact-native-outputs").glob("*.json"))]
        adapter_receipts=[data_runtime.read_json(path) for path in sorted((run_dir/"internal/compact-adapter-receipts").glob("*.json"))]
        telemetry=[data_runtime.read_json(path) for path in sorted((run_dir/"internal/telemetry").glob("*.json"))] if (run_dir/"internal/telemetry").is_dir() else []
        semantic=[data_runtime.read_json(path) for path in sorted((run_dir/"internal/semantic-assessments").glob("*.json"))] if (run_dir/"internal/semantic-assessments").is_dir() else []
        attestations=[data_runtime.read_json(path) for path in sorted((run_dir/"internal/execution-receipts").glob("*.json"))] if (run_dir/"internal/execution-receipts").is_dir() else []
        for attestation in attestations: validate(attestation,"role-execution-attestation.schema.json")
        for assessment in semantic: validate(assessment,"semantic-assessment.schema.json")
        receipt_values:list[dict[str,Any]]=[]
        for path in sorted((run_dir/"internal/context-packs").glob("*/CONTEXT.json")):
            candidate=pipeline_payload(path).get("candidate",{})
            for receipt in candidate.get("skill_receipts",[]):
                receipt_values.append(receipt)
        receipt_ids=[receipt.get("receipt_id") for receipt in receipt_values]
        if None in receipt_ids or len(receipt_ids)!=len(set(receipt_ids)):
            raise RunCtlError("R2 context packs 包含重复 skill receipt artifact")
        manifests=[pipeline_payload(denominator_state)]
        context_state=run_dir/"internal/context-publication-state.json"
        if context_state.is_file(): manifests.append(pipeline_payload(context_state))
        files=_r2_judge_file_bindings(run_dir,repositories)
        judge_inputs={"run_id":run_dir.name,"inventories":inventories,"ledgers":ledgers,
            "assignments":assignments,"fragments":fragments,"native_outputs":native_outputs,"adapter_receipts":adapter_receipts,"skill_receipts":receipt_values,
            "telemetry":telemetry,"semantic_assessments":semantic,"publication_manifests":manifests,
            "execution_attestations":attestations,"artifact_bindings":[]}
        judge_inputs["artifact_bindings"]=coverage_judge._expected_artifact_bindings(judge_inputs)
        judged=coverage_judge.judge_r2(judge_inputs)
        judged["input_artifacts"]=files
        validate(judged,"coverage-judge-r2.schema.json")
        data_runtime.atomic_write_json(_coverage_judge_path(run_dir),judged)
        return judged

    analysis_path = _analysis_model_path(run_dir)
    report_path = _fixed_audit_model(run_dir)
    ledger_path = run_dir / "internal" / "risk-ledger.json"
    analysis = _validate_analysis_model(data_runtime.read_json(analysis_path), contract, run_dir.name)
    report = _assert_report_contract_and_sections(run_dir, data_runtime.read_json(report_path))
    ledger = data_runtime.read_json(ledger_path)
    validate(ledger, "risk-ledger.schema.json")
    judged = coverage_judge.judge(analysis, report, ledger)
    payload = {
        "artifact_type": "coverage_judge", "schema_version": "1.0", "run_id": run_dir.name,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "analysis_artifact": _binding(analysis_path, ANALYSIS_MODEL_RELATIVE),
        "report_artifact": _binding(report_path, AUDITED_MODEL_RELATIVE),
        "risk_ledger_artifact": _binding(ledger_path, "internal/risk-ledger.json"),
        "verdict": judged["verdict"], "checks": judged["checks"],
    }
    validate(payload, "coverage-judge.schema.json")
    data_runtime.atomic_write_json(_coverage_judge_path(run_dir), payload)
    return payload


def _coverage_judge_binding(run_dir: Path, contract: dict[str, Any], *, required: bool) -> dict[str, Any] | None:
    if not required:
        return None
    path = _coverage_judge_path(run_dir)
    if not path.is_file():
        raise RunCtlError(f"完整型模块分析缺少独立 Coverage Judge 工件: {COVERAGE_JUDGE_RELATIVE}")
    payload = read_json(path)
    validate(payload, "coverage-judge-r2.schema.json" if payload.get("artifact_type")=="coverage_judge_r2" else "coverage-judge.schema.json")
    if payload.get("artifact_type")=="coverage_judge_r2":
        from runtime import analysis_pipeline
        try: analysis_pipeline.validate_run_for_judge(run_dir.parents[2],run_dir.name)
        except analysis_pipeline.PipelineError as exc: raise RunCtlError(f"R2 replay validation failed: {exc}") from exc
        expected_inputs=_r2_judge_file_bindings(run_dir,contract.get("repositories",[]))
        if payload.get("input_artifacts")!=expected_inputs:
            raise RunCtlError("R2 Coverage Judge 输入工件已变化，必须重新执行")
        if payload.get("verdict")!="PASS": raise RunCtlError("独立 Coverage Judge 未通过，禁止提交 auditor 或完成 Run")
        return {"path": COVERAGE_JUDGE_RELATIVE, "sha256": _sha256_file(path), "verdict": "PASS"}
    expected = {
        "analysis_artifact": _binding(_analysis_model_path(run_dir), ANALYSIS_MODEL_RELATIVE),
        "report_artifact": _binding(_fixed_audit_model(run_dir), AUDITED_MODEL_RELATIVE),
        "risk_ledger_artifact": _binding(run_dir / "internal" / "risk-ledger.json", "internal/risk-ledger.json"),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RunCtlError(f"Coverage Judge 的 {key} 已过期，必须重新执行")
    if payload.get("verdict") != "PASS":
        raise RunCtlError("独立 Coverage Judge 未通过，禁止提交 auditor 或完成 Run")
    return {"path": COVERAGE_JUDGE_RELATIVE, "sha256": _sha256_file(path), "verdict": "PASS"}


def _invalidate_fixed_artifact(path: Path) -> None:
    if path.is_symlink():
        raise RunCtlError(f"拒绝删除符号链接工件: {path}")
    if path.exists():
        if not path.is_file():
            raise RunCtlError(f"固定工件不是普通文件: {path}")
        path.unlink()


def stage_analysis_v2(args: argparse.Namespace) -> None:
    """Validate and atomically stage the complete source-driven analysis model."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入分析模型")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    if contract.get("mode") != "module_analysis":
        raise RunCtlError("stage-analysis-v2 当前仅用于模块分析")
    plan = _load_v2_workflow_plan(run_dir)
    _assert_analysis_stages_complete(run_dir, plan)
    if args.json is not None:
        try:
            model = json.loads(args.json)
        except json.JSONDecodeError as exc:
            raise RunCtlError(f"--json 分析模型无效: {exc}") from exc
    else:
        source = Path(args.file).expanduser()
        if source.is_symlink() or not source.is_file():
            raise RunCtlError(f"分析模型输入必须是普通文件: {source}")
        model = read_json(source.resolve())
    normalized = _validate_analysis_model(model, contract, args.run_id)
    projection=None
    if (run_dir/"internal/denominator-state.json").is_file():
        projection=_expected_r2_projection(run_dir)
        if normalized.get("r2_projection")!=projection:
            raise RunCtlError("analysis model 未精确投影 R2 fragments/contributions/H-C risks")
        _validate_r2_collection_na(run_dir,normalized)
    target = _analysis_model_path(run_dir)
    _invalidate_fixed_artifact(_fixed_audit_model(run_dir))
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
    data_runtime.atomic_write_json(target, normalized)
    if projection is not None:
        ledger_path=run_dir/"internal/risk-ledger.json"
        ledger=data_runtime.read_json(ledger_path); ledger["r2_projection"]=projection
        validate(ledger,"risk-ledger.schema.json"); data_runtime.atomic_write_json(ledger_path,ledger)
    digest = _sha256_file(target)
    data_runtime.set_run_state(root, args.run_id, "reviewing", "完整分析模型已落盘，准备生成报告模型")
    print(json.dumps({"run_id": args.run_id, "analysis_model": str(target),
                      "analysis_artifact": ANALYSIS_MODEL_RELATIVE, "sha256": digest,
                      "next_step": "stage-report-v2"}, ensure_ascii=False))


def stage_report_v2(args: argparse.Namespace) -> None:
    """Validate and atomically stage the sole report model accepted by audit."""
    from runtime import data_runtime, reporting

    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可写入报告模型")
    if manifest.get("audit", {}).get("status") == "PASS":
        raise RunCtlError("报告模型已经 PASS；修改前必须开启新的审计流程")
    plan = _load_v2_workflow_plan(run_dir)
    _assert_analysis_stages_complete(run_dir, plan)
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))
    if args.json is not None:
        try:
            model = json.loads(args.json)
        except json.JSONDecodeError as exc:
            raise RunCtlError(f"--json 报告模型无效: {exc}") from exc
    else:
        source = Path(args.file).expanduser()
        if source.is_symlink() or not source.is_file():
            raise RunCtlError(f"报告模型输入必须是普通文件: {source}")
        model = read_json(source.resolve())
    if not isinstance(model, dict):
        raise RunCtlError("报告模型必须是 JSON 对象")
    if analysis_binding is not None:
        from runtime import analysis_reporting
        model = analysis_reporting.apply_projection(model, data_runtime.read_json(_analysis_model_path(run_dir)))
        model["analysis_artifact"] = analysis_binding
    model = _assert_report_contract_and_sections(run_dir, model)
    snapshot_gaps = _assert_mr_snapshot_binding(root, run_dir)
    _assert_report_gap_binding(model, snapshot_gaps)
    _assert_report_risk_binding(run_dir, model)
    if (run_dir / "internal/denominator-state.json").is_file():
        _assert_r2_hc_risk_binding(run_dir, model)
    try:
        reporting.validate_model(model)
    except reporting.ReportError as exc:
        raise RunCtlError(str(exc)) from exc
    target = _fixed_audit_model(run_dir)
    _invalidate_fixed_artifact(_coverage_judge_path(run_dir))
    data_runtime.atomic_write_json(target, model)
    digest = _sha256_file(target)
    judge = _run_coverage_judge(run_dir, contract) if _judge_required(contract) else None
    if judge is not None and judge["verdict"] != "PASS":
        failed = [name for name, check in judge["checks"].items() if check["verdict"] != "PASS"]
        raise RunCtlError("独立 Coverage Judge 未通过: " + ", ".join(failed))
    data_runtime.set_run_state(root, args.run_id, "reviewing", "报告模型和独立覆盖审查已落盘，等待 auditor")
    print(json.dumps({"run_id": args.run_id, "report_model": str(target),
                      "audited_artifact": AUDITED_MODEL_RELATIVE, "sha256": digest,
                      "coverage_judge": str(_coverage_judge_path(run_dir)) if judge is not None else None,
                      "next_step": "audit"}, ensure_ascii=False))



def judge_analysis_v2(args: argparse.Namespace) -> None:
    """Re-run the independent deterministic judge from fixed Run artifacts."""
    from runtime import data_runtime

    root = Path(args.root).resolve() if args.root else ROOT
    run_dir, manifest = data_runtime._load_run(root, args.run_id)
    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:
        raise RunCtlError("已结束 Run 不可重新执行 Coverage Judge")
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    if not _judge_required(contract):
        raise RunCtlError("judge-analysis-v2 仅用于完整型模块分析")
    payload = _run_coverage_judge(run_dir, contract)
    data_runtime.set_run_state(root, args.run_id, "reviewing", f"独立 Coverage Judge：{payload['verdict']}")
    if payload["verdict"] != "PASS":
        failed = [name for name, check in payload["checks"].items() if check["verdict"] != "PASS"]
        raise RunCtlError("独立 Coverage Judge 未通过: " + ", ".join(failed))
    print(json.dumps({"run_id": args.run_id, "verdict": "PASS", "judge": str(_coverage_judge_path(run_dir)),
                      "analysis_artifact": payload["analysis_artifact"], "report_artifact": payload["report_artifact"]},
                     ensure_ascii=False))


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
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))
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


def _safe_report_directory(root: Path, run_id: str) -> Path:
    from runtime import data_runtime

    workspace = data_runtime.ensure_layout(root)
    workspace_resolved = workspace.resolve(strict=True)
    reports_root = workspace / "reports"
    data_runtime._ensure_managed_directory(reports_root, workspace_resolved, "reports 目录")
    reports_resolved = data_runtime._require_managed_directory(reports_root, workspace_resolved, "reports 目录")
    destination = reports_root / run_id
    if destination.exists() or destination.is_symlink():
        raise RunCtlError(f"正式报告目录已存在，拒绝覆盖: {destination}")
    try:
        destination.resolve().relative_to(reports_resolved)
    except ValueError as exc:
        raise RunCtlError(f"正式报告目录越界: {destination}") from exc
    return destination


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
    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))
    _coverage_judge_binding(run_dir, contract, required=_judge_required(contract))
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
    report_dir = _safe_report_directory(root, args.run_id)
    try:
        markdown, html = reporting.write_report(model, report_dir)
    except reporting.ReportError as exc:
        if report_dir.exists() and report_dir.is_dir() and not any(report_dir.iterdir()):
            report_dir.rmdir()
        raise RunCtlError(str(exc)) from exc
    for artifact, label in ((markdown, "Markdown"), (html, "HTML")):
        if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size == 0                 or artifact.resolve().parent != report_dir.resolve():
            raise RunCtlError(f"{label} 正式报告未实际生成或路径异常: {artifact}")
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
    workspace = data_runtime.ensure_layout(root)
    manifest.update({
        "status": "completed", "machine_state": "completed",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "deliverables": {
            "report_md": markdown.relative_to(workspace).as_posix(),
            "report_html": html.relative_to(workspace).as_posix(),
        },
    })
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


def build_denominator_v2(args: argparse.Namespace) -> None:
    from runtime import analysis_pipeline
    root = Path(args.root).resolve() if args.root else ROOT
    print(json.dumps(analysis_pipeline.build_denominator(root, args.run_id), ensure_ascii=False))


def issue_context_v2(args: argparse.Namespace) -> None:
    from runtime import analysis_pipeline
    root = Path(args.root).resolve() if args.root else ROOT
    print(json.dumps(analysis_pipeline.issue_context(root, args.run_id, args.worker_id), ensure_ascii=False))


def apply_fragment_v2(args: argparse.Namespace) -> None:
    from runtime import analysis_pipeline
    root = Path(args.root).resolve() if args.root else ROOT
    print(json.dumps(analysis_pipeline.apply_fragment(root, args.run_id, Path(args.fragment)), ensure_ascii=False))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="PANGEA-TEST deterministic run controller")
    sub = p.add_subparsers(dest="command", required=True)
    evaluator_intake = sub.add_parser(
        "evaluator-intake-v2", help="激活 evaluator 预备的 canonical-case one-shot contract",
    )
    evaluator_intake.set_defaults(func=evaluator_intake_v2)
    val = sub.add_parser("validate")
    val.add_argument("--file", required=True)
    val.add_argument("--schema", required=True)
    val.set_defaults(func=validate_file)
    v2 = sub.add_parser("create-v2", help="创建 Architecture v2 的 pangea-data Run")
    v2.add_argument("--scenario", choices=["mr-regression", "module-analysis"], required=True)
    v2.add_argument("--target", required=True)
    v2.add_argument("--repository", action="append", required=True)
    v2.add_argument("--repository-commit", action="append")
    v2.add_argument("--source-scope", action="append")
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
    draft2 = sub.add_parser("draft-contract-v2", help="生成待确认的正式任务契约")
    draft2.add_argument("--scenario", choices=["mr-regression", "module-analysis"], required=True)
    draft2.add_argument("--target", required=True)
    draft2.add_argument("--repository", action="append", required=True)
    draft2.add_argument("--repository-commit", action="append")
    draft2.add_argument("--source-scope", action="append")
    draft2.add_argument("--contract-id")
    draft2.add_argument("--root")
    draft2.add_argument("--mr-url")
    draft2.add_argument("--goal")
    draft2.add_argument("--analysis-depth")
    draft2.add_argument("--version")
    draft2.add_argument("--topology")
    draft2.add_argument("--test-focus", action="append")
    draft2.add_argument("--input-ref", action="append")
    draft2.add_argument("--exclude", action="append")
    draft2.add_argument("--tool-gap", action="append")
    draft2.add_argument("--known-gap", action="append")
    draft2.add_argument("--signal", action="append")
    draft2.add_argument("--resource-emphasis", action="store_true")
    draft2.add_argument("--created-by", default="pangea-test")
    draft2.set_defaults(func=draft_contract_v2)
    revise2 = sub.add_parser("revise-contract-v2", help="按用户反馈修订 draft 任务契约")
    revise2.add_argument("--contract-id", required=True)
    revise2.add_argument("--expected-revision", required=True, type=int)
    revise2.add_argument("--file", required=True)
    revise2.add_argument("--root")
    revise2.set_defaults(func=revise_contract_v2)
    confirm2 = sub.add_parser("confirm-contract-v2", help="持久化任务契约确认")
    confirm2.add_argument("--contract-id", required=True)
    confirm2.add_argument("--revision", required=True, type=int)
    confirm2.add_argument("--source", required=True,
                          choices=["user_reply", "user_explicit_bypass", "auto_unambiguous"])
    confirm2.add_argument("--materials-status", required=True,
                          choices=["provided", "confirmed_none", "unchanged"])
    confirm2.add_argument("--note")
    confirm2.add_argument("--root")
    confirm2.set_defaults(func=confirm_contract_v2)
    activate2 = sub.add_parser("activate-contract-v2", help="从已确认契约创建 Run 与只读快照")
    activate2.add_argument("--contract-id", required=True)
    activate2.add_argument("--run-id")
    activate2.add_argument("--root")
    activate2.add_argument("--max-audit-rounds", type=int, default=2)
    activate2.set_defaults(func=activate_contract_v2)
    resume2 = sub.add_parser("resume-v2", help="读取 pangea-data Run 的续跑计划")
    resume2.add_argument("--run-id", required=True)
    resume2.add_argument("--root")
    resume2.set_defaults(func=resume_v2)
    rework2 = sub.add_parser("record-rework-v2", help="记录审计 required_actions 的逐项闭环证据")
    rework2.add_argument("--run-id", required=True)
    rework2.add_argument("--file", required=True)
    rework2.add_argument("--root")
    rework2.set_defaults(func=record_rework_v2)
    judge2 = sub.add_parser("judge-analysis-v2", help="独立核对完整分析、测试追溯与报告投影")
    judge2.add_argument("--run-id", required=True)
    judge2.add_argument("--root")
    judge2.set_defaults(func=judge_analysis_v2)
    analysis2 = sub.add_parser("stage-analysis-v2", help="校验并实际落盘完整分析模型")
    analysis2.add_argument("--run-id", required=True)
    analysis_input = analysis2.add_mutually_exclusive_group(required=True)
    analysis_input.add_argument("--file")
    analysis_input.add_argument("--json")
    analysis2.add_argument("--root")
    analysis2.set_defaults(func=stage_analysis_v2)
    stage2 = sub.add_parser("stage-report-v2", help="校验并实际落盘固定报告模型")
    stage2.add_argument("--run-id", required=True)
    stage_input = stage2.add_mutually_exclusive_group(required=True)
    stage_input.add_argument("--file")
    stage_input.add_argument("--json")
    stage2.add_argument("--root")
    stage2.set_defaults(func=stage_report_v2)
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
    denominator2 = sub.add_parser("build-denominator-v2", help="从确认的只读快照建立独立分析分母")
    denominator2.add_argument("--run-id", required=True)
    denominator2.add_argument("--root")
    denominator2.set_defaults(func=build_denominator_v2)
    context2 = sub.add_parser("issue-context-v2", help="为 pending obligations 签发只读 worker context packs")
    context2.add_argument("--run-id", required=True)
    context2.add_argument("--worker-id", default="analysis-worker")
    context2.add_argument("--root")
    context2.set_defaults(func=issue_context_v2)
    fragment2 = sub.add_parser("apply-fragment-v2", help="验证并原子应用一个 analysis fragment")
    fragment2.add_argument("--run-id", required=True)
    fragment2.add_argument("--fragment", required=True)
    fragment2.add_argument("--root")
    fragment2.set_defaults(func=apply_fragment_v2)
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
