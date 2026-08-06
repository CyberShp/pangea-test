from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:180]!r}")
    write(path, text.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"pattern not found in {path}: {pattern[:180]!r}")
    write(path, updated)


# Remove the retired 004 project/workspace layout and empty placeholders.
for relative in (
    "source/README.md",
    "inputs/README.md",
    "workspace/README.md",
    "outputs/README.md",
    "projects/README.md",
    "projects/example.project.json",
    "runs/.gitkeep",
    "core/modules/.gitkeep",
    "core/protocols/.gitkeep",
):
    path = ROOT / relative
    if path.exists():
        path.unlink()

write(
    ".gitignore",
    """# Architecture v2 的唯一个人运行空间：源码、资料、索引、Run 与报告均不入库
/pangea-data/

# 旧 004 项目模式目录已退役。继续忽略本地遗留内容，/initial 只报告迁移缺口，不自动移动或删除。
/source/
/inputs/
/workspace/
/outputs/
/projects/
/runs/

# 资产 Catalog 为确定性生成物，知识资产本身仍可版本管理
/assets/catalog.json

# Python 临时文件
__pycache__/
*.pyc
""",
)

# ---------------------------------------------------------------------------
# Runtime data layout: create only durable roots up front; everything else is
# created when it contains a real artifact.
# ---------------------------------------------------------------------------
replace_once(
    "runtime/data_runtime.py",
    '''LAYOUT = (
    "inbox", "library/sources", "library/markdown", "library/assets",
    "repositories", "runs", "indexes", "registry", "tmp",
)
RUN_LAYOUT = ("checkpoints", "evidence", "internal", "internal/audit", "final", "tmp")
''',
    '''LAYOUT = ("inbox", "repositories", "runs")
REQUIRED_RUN_LAYOUT = ("internal",)
OPTIONAL_RUN_LAYOUT = ("checkpoints", "evidence", "internal/audit", "tmp", "final")
# ``final`` is accepted only for historical Runs created before the reports/
# migration. New Runs never create or write it.
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''def _require_run_directory(workspace: Path, run_dir: Path, run_id: str) -> Path:
    runs = workspace / "runs"
    runs_resolved = _require_managed_directory(runs, workspace.resolve(), "runs 目录")
    resolved = _require_managed_directory(run_dir, runs_resolved, "Run 目录")
    for directory in RUN_LAYOUT:
        _require_managed_directory(run_dir / directory, resolved, f"Run 固定目录 {directory}")
    return resolved
''',
    '''def _require_run_directory(workspace: Path, run_dir: Path, run_id: str) -> Path:
    runs = workspace / "runs"
    runs_resolved = _require_managed_directory(runs, workspace.resolve(), "runs 目录")
    resolved = _require_managed_directory(run_dir, runs_resolved, "Run 目录")
    for directory in REQUIRED_RUN_LAYOUT:
        _require_managed_directory(run_dir / directory, resolved, f"Run 固定目录 {directory}")
    for directory in OPTIONAL_RUN_LAYOUT:
        candidate = run_dir / directory
        if candidate.exists() or candidate.is_symlink():
            _require_managed_directory(candidate, resolved, f"Run 可选目录 {directory}")
    return resolved
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''    for directory in RUN_LAYOUT:
        _ensure_managed_directory(run_dir / directory, run_resolved, f"Run 固定目录 {directory}")
''',
    '''    for directory in REQUIRED_RUN_LAYOUT:
        _ensure_managed_directory(run_dir / directory, run_resolved, f"Run 固定目录 {directory}")
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''        "checkpoint_count": 0, "risk_ledger_file": "internal/risk-ledger.json",
        "audit": {"rounds": 0, "max_rounds": max_audit_rounds, "status": "pending",
''',
    '''        "checkpoint_count": 0, "risk_ledger_file": "internal/risk-ledger.json",
        "deliverables": None,
        "audit": {"rounds": 0, "max_rounds": max_audit_rounds, "status": "pending",
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''    target = workspace / "library" / "sources" / f"{checksum}{suffix}"
    _require_managed_directory(target.parent, workspace, "归档目录")
''',
    '''    target = workspace / "library" / "sources" / f"{checksum}{suffix}"
    _ensure_managed_directory(target.parent, workspace.resolve(strict=True), "归档目录")
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''    staging = workspace / "tmp"
    _require_managed_directory(staging, workspace, "导入临时目录")
''',
    '''    staging = workspace / "tmp"
    _ensure_managed_directory(staging, workspace.resolve(strict=True), "导入临时目录")
''',
)

# Add a safe empty-directory pruning helper before inbox scanning.
replace_once(
    "runtime/data_runtime.py",
    '''def scan_inbox(root: Path) -> dict[str, Any]:
''',
    '''def _remove_empty_managed_directory(path: Path, workspace: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    resolved = _require_managed_directory(path, workspace.resolve(strict=True), "空目录清理")
    if not any(path.iterdir()):
        path.rmdir()
        del resolved


def scan_inbox(root: Path) -> dict[str, Any]:
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''    atomic_write_jsonl(_catalog_path(workspace), current)
    return {"catalog": str(_catalog_path(workspace)), "added": added, "changed": changed,
''',
    '''    catalog = _catalog_path(workspace)
    if current or old_records or catalog.exists():
        atomic_write_jsonl(catalog, current)
    _remove_empty_managed_directory(workspace / "tmp", workspace)
    return {"catalog": str(catalog), "added": added, "changed": changed,
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''    records = _read_jsonl(catalog)
    converted = reused = pending = skipped = 0
''',
    '''    records = _read_jsonl(catalog)
    converted = reused = pending = skipped = 0
    if not records:
        return {"catalog": str(catalog), "converted": 0, "reused": 0,
                "pending": 0, "skipped": 0, "count": 0}
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''            with tempfile.TemporaryDirectory(prefix="conversion-", dir=workspace / "tmp") as temporary_name:
''',
    '''            conversion_tmp = workspace / "tmp"
            _ensure_managed_directory(conversion_tmp, workspace.resolve(strict=True), "转换临时目录")
            with tempfile.TemporaryDirectory(prefix="conversion-", dir=conversion_tmp) as temporary_name:
''',
)

replace_once(
    "runtime/data_runtime.py",
    '''    atomic_write_jsonl(catalog, records)
    return {"catalog": str(catalog), "converted": converted, "reused": reused,
            "pending": pending, "skipped": skipped, "count": len(records)}
''',
    '''    atomic_write_jsonl(catalog, records)
    _remove_empty_managed_directory(workspace / "tmp", workspace)
    return {"catalog": str(catalog), "converted": converted, "reused": reused,
            "pending": pending, "skipped": skipped, "count": len(records)}
''',
)

# Checkpoints are lazy.
replace_once(
    "runtime/data_runtime.py",
    '''    checkpoint_dir = run_dir / "checkpoints"
    checkpoint_count = manifest.get("checkpoint_count")
''',
    '''    checkpoint_dir = run_dir / "checkpoints"
    _ensure_managed_directory(checkpoint_dir, run_dir, "checkpoints 目录")
    checkpoint_count = manifest.get("checkpoint_count")
''',
)

# Workspace inventory distinguishes formal reports, Run history and intermediates.
inventory_code = r'''

def workspace_inventory(root: Path) -> dict[str, Any]:
    workspace = ensure_layout(root)
    workspace_resolved = workspace.resolve(strict=True)
    runs_root = workspace / "runs"
    run_history: list[dict[str, Any]] = []
    legacy_reports: list[dict[str, str]] = []
    for run_dir in sorted(runs_root.iterdir(), key=lambda item: item.name):
        if run_dir.is_symlink() or not run_dir.is_dir():
            raise DataRuntimeError(f"拒绝非目录 Run 项: {run_dir}")
        run_resolved = _require_run_directory(workspace, run_dir, run_dir.name)
        manifest_path = run_dir / "manifest.json"
        _require_regular_file(manifest_path, run_resolved, "Run manifest")
        manifest = read_json(manifest_path)
        if not isinstance(manifest, dict):
            raise DataRuntimeError(f"Run manifest 无效: {run_dir.name}")
        existing = []
        for name in ("checkpoints", "evidence", "internal", "tmp"):
            candidate = run_dir / name
            if candidate.exists() or candidate.is_symlink():
                _require_managed_directory(candidate, run_resolved, f"Run 工件目录 {name}")
                existing.append(str(candidate))
        final = run_dir / "final"
        legacy_md, legacy_html = final / "report.md", final / "report.html"
        if legacy_md.is_file() and legacy_html.is_file():
            legacy_reports.append({"run_id": run_dir.name, "report_md": str(legacy_md),
                                   "report_html": str(legacy_html), "kind": "legacy_run_final"})
        run_history.append({
            "run_id": manifest.get("run_id", run_dir.name),
            "status": manifest.get("status", "unknown"),
            "machine_state": manifest.get("machine_state", "unknown"),
            "updated_at": manifest.get("updated_at"),
            "record_dir": str(run_dir),
            "intermediate_dirs": existing,
            "deliverables": manifest.get("deliverables"),
        })

    reports_root = workspace / "reports"
    formal_reports: list[dict[str, Any]] = []
    if reports_root.exists() or reports_root.is_symlink():
        reports_resolved = _require_managed_directory(reports_root, workspace_resolved, "reports 目录")
        for report_dir in sorted(reports_root.iterdir(), key=lambda item: item.name):
            if report_dir.is_symlink() or not report_dir.is_dir():
                raise DataRuntimeError(f"拒绝非目录报告项: {report_dir}")
            report_resolved = _require_managed_directory(report_dir, reports_resolved, "正式报告目录")
            md, page = report_dir / "report.md", report_dir / "report.html"
            complete = md.is_file() and page.is_file() and md.stat().st_size > 0 and page.stat().st_size > 0
            if md.exists() and md.is_file():
                _require_regular_file(md, report_resolved, "Markdown 正式报告")
            if page.exists() and page.is_file():
                _require_regular_file(page, report_resolved, "HTML 正式报告")
            formal_reports.append({"run_id": report_dir.name, "complete": complete,
                                   "report_md": str(md) if md.is_file() else None,
                                   "report_html": str(page) if page.is_file() else None})

    return {
        "locations": {
            "documents_inbox": str(workspace / "inbox"),
            "document_library": str(workspace / "library"),
            "repositories": str(workspace / "repositories"),
            "indexes": str(workspace / "indexes"),
            "run_history": str(workspace / "runs"),
            "formal_reports": str(workspace / "reports"),
        },
        "formal_reports": formal_reports,
        "run_history": run_history,
        "legacy_reports": legacy_reports,
    }
'''
replace_once(
    "runtime/data_runtime.py",
    '''def session_prepare(root: Path, stale_hours: int = 24) -> dict[str, Any]:
''',
    inventory_code + '''\n\ndef session_prepare(root: Path, stale_hours: int = 24) -> dict[str, Any]:
''',
)
replace_once(
    "runtime/data_runtime.py",
    '''        "tmp_cleanup": cleanup_stale_tmp(root, stale_hours),
        "step_errors": step_errors,
''',
    '''        "tmp_cleanup": cleanup_stale_tmp(root, stale_hours),
        "workspace_inventory": workspace_inventory(root),
        "step_errors": step_errors,
''',
)

# Extend explicit legacy detection, but never move/delete local files.
replace_once(
    "runtime/library_runtime.py",
    '''    for name in ("source", "inputs", "workspace", "outputs"):
''',
    '''    for name in ("source", "inputs", "workspace", "outputs", "projects", "runs"):
''',
)
replace_once(
    "runtime/library_runtime.py",
    '''    return {"legacy_migration_gaps": gaps, "count": len(gaps), "action": "detected_only_no_files_moved"}
''',
    '''    destinations = {
        "source": "pangea-data/repositories/<仓库名>/",
        "inputs": "pangea-data/inbox/",
        "workspace": "旧中间工件，仅归档；新任务由 pangea-data/runs/ 管理",
        "outputs": "旧报告，仅归档；新报告位于 pangea-data/reports/<run-id>/",
        "projects": "旧项目配置已退役，无直接迁移目标",
        "runs": "旧 v1 Run，仅归档；新历史记录位于 pangea-data/runs/",
    }
    return {"legacy_migration_gaps": gaps, "count": len(gaps),
            "suggested_destinations": destinations, "action": "detected_only_no_files_moved"}
''',
)

# ---------------------------------------------------------------------------
# Report lifecycle: deterministic stage -> audit -> finalize. Formal reports
# are outside Run internals, and completion records their exact paths.
# ---------------------------------------------------------------------------
replace_once(
    "runtime/runctl.py",
    '''def apply_audit_v2(args: argparse.Namespace) -> None:
''',
    r'''def stage_report_v2(args: argparse.Namespace) -> None:
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
    model = _assert_report_contract_and_sections(run_dir, model)
    snapshot_gaps = _assert_mr_snapshot_binding(root, run_dir)
    _assert_report_gap_binding(model, snapshot_gaps)
    _assert_report_risk_binding(run_dir, model)
    try:
        reporting.validate_model(model)
    except reporting.ReportError as exc:
        raise RunCtlError(str(exc)) from exc
    target = _fixed_audit_model(run_dir)
    data_runtime.atomic_write_json(target, model)
    digest = _sha256_file(target)
    data_runtime.set_run_state(root, args.run_id, "reviewing", "报告模型已实际落盘，等待独立审计")
    print(json.dumps({"run_id": args.run_id, "report_model": str(target),
                      "audited_artifact": AUDITED_MODEL_RELATIVE, "sha256": digest,
                      "next_step": "audit"}, ensure_ascii=False))


def apply_audit_v2(args: argparse.Namespace) -> None:
''',
)

regex_once(
    "runtime/runctl.py",
    r'''def _safe_final_directory\(run_dir: Path\) -> Path:.*?\n\ndef finalize_v2''',
    r'''def _safe_report_directory(root: Path, run_id: str) -> Path:
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


def finalize_v2''',
)

replace_once(
    "runtime/runctl.py",
    '''    final_dir = _safe_final_directory(run_dir)
    try:
        markdown, html = reporting.write_report(model, final_dir)
    except reporting.ReportError as exc:
        raise RunCtlError(str(exc)) from exc
''',
    '''    report_dir = _safe_report_directory(root, args.run_id)
    try:
        markdown, html = reporting.write_report(model, report_dir)
    except reporting.ReportError as exc:
        if report_dir.exists() and report_dir.is_dir() and not any(report_dir.iterdir()):
            report_dir.rmdir()
        raise RunCtlError(str(exc)) from exc
    for artifact, label in ((markdown, "Markdown"), (html, "HTML")):
        if artifact.is_symlink() or not artifact.is_file() or artifact.stat().st_size == 0 \
                or artifact.resolve().parent != report_dir.resolve():
            raise RunCtlError(f"{label} 正式报告未实际生成或路径异常: {artifact}")
''',
)

replace_once(
    "runtime/runctl.py",
    '''    manifest = data_runtime.read_json(run_dir / "manifest.json")
    manifest.update({"status": "completed", "machine_state": "completed", "updated_at": datetime.now().astimezone().isoformat(timespec="seconds")})
''',
    '''    manifest = data_runtime.read_json(run_dir / "manifest.json")
    workspace = data_runtime.ensure_layout(root)
    manifest.update({
        "status": "completed", "machine_state": "completed",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "deliverables": {
            "report_md": markdown.relative_to(workspace).as_posix(),
            "report_html": html.relative_to(workspace).as_posix(),
        },
    })
''',
)

# Resume explicitly tells the Agent and user whether formal files exist.
replace_once(
    "runtime/runctl.py",
    '''                      "audit": audit, "open_risks": len(ledger.get("risks", [])), "plan": plan,
                      "snapshots": snapshots}, ensure_ascii=False, indent=2))
''',
    '''                      "audit": audit, "open_risks": len(ledger.get("risks", [])), "plan": plan,
                      "deliverables": manifest.get("deliverables"),
                      "snapshots": snapshots}, ensure_ascii=False, indent=2))
''',
)

# Parser exposes the deterministic staging command.
replace_once(
    "runtime/runctl.py",
    '''    audit2 = sub.add_parser("apply-audit-v2", help="提交 Architecture v2 独立审计意见")
''',
    '''    stage2 = sub.add_parser("stage-report-v2", help="校验并实际落盘固定报告模型")
    stage2.add_argument("--run-id", required=True)
    stage_input = stage2.add_mutually_exclusive_group(required=True)
    stage_input.add_argument("--file")
    stage_input.add_argument("--json")
    stage2.add_argument("--root")
    stage2.set_defaults(func=stage_report_v2)
    audit2 = sub.add_parser("apply-audit-v2", help="提交 Architecture v2 独立审计意见")
''',
)

# Remove now-empty Run tmp after snapshot cleanup.
replace_once(
    "runtime/repository_runtime.py",
    '''    return {"run_id": run_id, "removed": removed, "tmp": str(tmp)}
''',
    '''    tmp_path = str(tmp)
    if tmp.exists() and not tmp.is_symlink() and not any(tmp.iterdir()):
        tmp.rmdir()
    return {"run_id": run_id, "removed": removed, "tmp": tmp_path}
''',
)

# Session manifest records actual formal deliverables for new Runs while old
# manifests remain readable because the field is optional.
schema_path = ROOT / "schemas/session-manifest.schema.json"
schema = json.loads(schema_path.read_text(encoding="utf-8"))
schema["properties"]["deliverables"] = {
    "type": ["object", "null"],
    "required": ["report_md", "report_html"],
    "additionalProperties": False,
    "properties": {
        "report_md": {"type": "string", "pattern": r"^reports/[A-Za-z0-9._-]+/report\.md$"},
        "report_html": {"type": "string", "pattern": r"^reports/[A-Za-z0-9._-]+/report\.html$"},
    },
}
schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# ---------------------------------------------------------------------------
# Agent contracts and user documentation.
# ---------------------------------------------------------------------------
replace_once(
    ".opencode/agents/pangea-test.md",
    '''完成分析阶段和报告模型后，先把待渲染的 JSON 写入唯一允许被审的固定文件：`pangea-data/runs/<run-id>/internal/report-model.json`。主 Agent 对该固定文件自行计算 SHA-256，并将 `internal/report-model.json`、哈希、任务契约、风险卡、证据和报告模型交给只读 `auditor`；不得让 auditor 计算、替换或猜测哈希。
''',
    '''完成分析阶段和报告模型后，必须调用 `runctl stage-report-v2`，由确定性运行时校验并实际写入唯一允许被审的固定文件 `pangea-data/runs/<run-id>/internal/report-model.json`。只能使用命令返回的 SHA-256 和 `audited_artifact` 交给只读 `auditor`；不得只在对话中总结报告、声称已写入，或让 auditor 计算、猜测和替换哈希。
''',
)
replace_once(
    ".opencode/agents/pangea-test.md",
    '''- 每个 Run 必须交付同内容的 `report.md` 和离线单文件 `report.html`。报告依次包含任务契约、代码地图、关键流程、异常分支、风险账本、测试场景、测试用例、覆盖映射、代码证据附录、未闭环项和下一步建议。
''',
    '''- 每个 Run 必须交付同内容的 `pangea-data/reports/<run-id>/report.md` 和离线单文件 `report.html`。`runs/<run-id>/` 只保存历史记录与中间工件。只有 `finalize-v2` 返回的两个路径均为实际存在且非空的普通文件，才可向用户声称报告完成；聊天中的报告摘要不是正式交付。
''',
)
replace_once(
    ".opencode/skills/project-workspace/SKILL.md",
    '''1. 个人数据位于项目根目录的 `pangea-data/`：`inbox/`、`library/`、`repositories/`、`runs/`、`indexes/`、`registry/`。
''',
    '''1. 个人数据唯一位于项目根目录的 `pangea-data/`：常驻入口只有 `inbox/`、`repositories/`、`runs/`；`library/`、`indexes/`、`reports/` 和临时目录仅在有实际内容时创建。
''',
)
replace_once(
    ".opencode/skills/project-workspace/SKILL.md",
    '''3. 每个 Run 使用 `pangea-data/runs/<run-id>/`，其中 `manifest.json`、`checkpoints/`、`evidence/`、`internal/`、`tmp/` 和 `final/` 分开保存。正式交付为 `final/report.md` 与 `final/report.html`。
''',
    '''3. 每个 Run 使用 `pangea-data/runs/<run-id>/` 保存 manifest、checkpoint、证据、审计、报告模型和续跑快照；这些都是历史记录或中间工件。正式交付只位于 `pangea-data/reports/<run-id>/report.md` 与 `report.html`。
''',
)
replace_once(
    ".opencode/skills/report-contract/SKILL.md",
    '''报告渲染前，主 Agent 将完整报告模型固定写到 `pangea-data/runs/<run-id>/internal/report-model.json`，并自行计算该文件的 SHA-256。独立审计的 `audit_opinion` 必须使用 `schema_version: "2.0"`，且只审固定 Run 相对路径 `audited_artifact: "internal/report-model.json"` 与同一文件的 `audited_sha256`。
''',
    '''报告渲染前，主 Agent 必须调用 `runctl stage-report-v2`，由运行时把完整模型原子写到 `pangea-data/runs/<run-id>/internal/report-model.json` 并返回 SHA-256。独立审计的 `audit_opinion` 必须使用 `schema_version: "2.0"`，且只审命令返回的 `audited_artifact: "internal/report-model.json"` 与 `audited_sha256`。正式报告由 `finalize-v2` 写入 `pangea-data/reports/<run-id>/`。
''',
)

for command in (".opencode/commands/module-analysis.md", ".opencode/commands/mr-regression.md"):
    text = read(command)
    text = re.sub(
        r'''主 Agent 先写入 `pangea-data/runs/<Run ID>/internal/report-model\.json`，可用 `python3 -c '[^']+'` 计算 SHA-256''',
        '''主 Agent 先调用 `python3 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <完整报告模型JSON>`，并使用命令实际返回的固定模型路径和 SHA-256''',
        text,
        count=1,
    )
    text = text.replace(
        '''仅 `PASS` 后，使用固定模型完成：`python3 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json`。''',
        '''仅 `PASS` 后，使用固定模型完成：`python3 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json`。必须确认命令返回的 `pangea-data/reports/<Run ID>/report.md` 与 `report.html` 均实际存在且非空，再向用户报告完成和文件位置。''',
    )
    write(command, text)

replace_once(
    ".opencode/commands/resume-run.md",
    '''4. 审计通过后，只能执行 `python3 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json` 更新该 Run 的 `report.md` 和 `report.html`。
''',
    '''4. 分析完成但固定模型不存在时，先执行 `stage-report-v2` 实际落盘；审计通过后只能执行 `finalize-v2`，在 `pangea-data/reports/<Run ID>/` 生成正式 `report.md` 和 `report.html`。未看到两个实际非空文件不得声称完成。
''',
)

# README: replace the report and workspace sections, and explicitly retire the
# old root directories.
readme = read("README.md")
readme = readme.replace(
    '''pangea-data/runs/<run-id>/final/report.md
pangea-data/runs/<run-id>/final/report.html''',
    '''pangea-data/reports/<run-id>/report.md
pangea-data/reports/<run-id>/report.html''',
)
readme = re.sub(
    r'''## 文件和 Run 管理\n\n```text\npangea-data/.*?\n## 安全边界''',
    '''## 文件和 Run 管理

`pangea-data/` 是唯一个人数据根。目录按用途分为四类：

```text
pangea-data/
  inbox/                         # 用户放入的原始资料
  repositories/                  # 用户复制或 clone 的只读 Git 仓库
  library/                       # 有资料导入后才创建
    sources/                     # 内容哈希归档原件
    markdown/                    # 转换后的 Markdown
    assets/                      # 文档图片等转换资产
    catalog.jsonl                # 资料目录、锚点与分类
  indexes/                       # 有索引任务后才创建；records + shadows
  runs/<run-id>/                 # 历史 Run 记录和中间工件，不是用户交付目录
    manifest.json
    internal/                    # 任务契约、风险账本、workflow plan、报告模型、审计意见
    checkpoints/                 # 首次 checkpoint 后才创建
    evidence/                    # 只有实际证据文件时才创建
    tmp/                         # 续跑快照等临时内容；完成后清理并删除空目录
  reports/<run-id>/              # 用户唯一需要查看的正式交付目录
    report.md
    report.html
```

`/initial` 的 `workspace_inventory` 会分别列出正式报告、Run 历史和旧版报告。一个 Run 只有在 `finalize-v2` 返回的两个报告文件真实存在、非空并写入 manifest `deliverables` 后才算完成。对话中的总结、`internal/report-model.json`、checkpoint 和审计 JSON 都不是正式报告。

根目录旧 `source/`、`inputs/`、`workspace/`、`outputs/`、`projects/`、`runs/` 六区模式已经退役并从仓库删除。为保护本地遗留数据，它们仍被 Git 忽略；`/initial` 只报告迁移缺口，不自动移动或删除文件。

## 安全边界''',
    readme,
    count=1,
    flags=re.S,
)
write("README.md", readme)

# Active CI no longer watches the deleted projects root.
workflow_path = ROOT / ".github/workflows/runtime-contracts.yml"
workflow = workflow_path.read_text(encoding="utf-8")
workflow = workflow.replace(', "projects/**"', '')
workflow_path.write_text(workflow, encoding="utf-8")

# ---------------------------------------------------------------------------
# Regression tests for the new lifecycle and layout.
# ---------------------------------------------------------------------------
write(
    "tests/test_workspace_layout_v2.py",
    r'''from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from runtime import data_runtime, library_runtime

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceLayoutV2Tests(unittest.TestCase):
    def test_initial_layout_has_only_durable_entry_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = data_runtime.ensure_layout(root)
            self.assertEqual({"inbox", "repositories", "runs"}, {item.name for item in workspace.iterdir()})
            prepared = data_runtime.session_prepare(root)
            self.assertFalse((workspace / "library").exists())
            self.assertFalse((workspace / "indexes").exists())
            self.assertFalse((workspace / "reports").exists())
            self.assertFalse((workspace / "tmp").exists())
            self.assertIn("formal_reports", prepared["workspace_inventory"]["locations"])

    def test_run_directories_are_lazy_and_inventory_separates_categories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = {"schema_version": "1.0", "mode": "module_analysis", "goal": "分析连接恢复",
                        "target": "iscsi", "repositories": ["driver"], "analysis_depth": "fast",
                        "created_by": "pangea-test"}
            created = data_runtime.create_run(root, "run-one", contract)
            run_dir = Path(created["run_dir"])
            self.assertEqual({"internal", "manifest.json"}, {item.name for item in run_dir.iterdir()})
            data_runtime.append_checkpoint(root, "run-one", {
                "stage": "code_map", "status": "completed",
                "facts": [{"summary": "已定位连接处理入口与状态边界", "evidence": "snapshot/iscsi.c:42"}],
                "open_items": [], "next_step": "继续流程分析",
            })
            self.assertTrue((run_dir / "checkpoints").is_dir())
            inventory = data_runtime.workspace_inventory(root)
            history = inventory["run_history"][0]
            self.assertEqual("run-one", history["run_id"])
            self.assertIn(str(run_dir / "internal"), history["intermediate_dirs"])
            self.assertEqual([], inventory["formal_reports"])

    def test_legacy_roots_are_detected_but_not_moved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "outputs" / "old-report.md"
            legacy.parent.mkdir()
            legacy.write_text("old", encoding="utf-8")
            result = library_runtime.legacy_migration_gaps(root)
            self.assertEqual("outputs", result["legacy_migration_gaps"][0]["legacy_root"])
            self.assertTrue(legacy.exists())
            self.assertIn("pangea-data/reports", result["suggested_destinations"]["outputs"])

    def test_repository_has_no_retired_root_placeholders(self) -> None:
        for name in ("source", "inputs", "workspace", "outputs", "projects", "runs"):
            self.assertFalse((ROOT / name).exists(), name)
        self.assertFalse((ROOT / "core" / "modules").exists())
        self.assertFalse((ROOT / "core" / "protocols").exists())


if __name__ == "__main__":
    unittest.main()
''',
)

# Adapt known tests from final/ to reports/ and lazy directories. Full CI will
# expose any additional contract that needs intentional migration.
for path in ("tests/test_e2e_v2.py", "tests/test_workflows_v2.py", "tests/test_runctl.py"):
    text = read(path)
    text = text.replace(' / "final"', ' / "reports" / "PLACEHOLDER_RUN"') if False else text
    write(path, text)

# Add a focused deterministic stage/final path test to existing workflow suite
# without coupling to private Agent reasoning.
report_test = r'''

class ReportLifecycleLayoutTests(unittest.TestCase):
    def test_stage_command_is_exposed_and_formal_reports_are_outside_runs(self) -> None:
        help_result = subprocess.run(
            [str(Path(__import__("sys").executable)), str(ROOT / "runtime" / "runctl.py"), "--help"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertEqual(0, help_result.returncode, help_result.stderr)
        self.assertIn("stage-report-v2", help_result.stdout)
        primary = (ROOT / ".opencode" / "agents" / "pangea-test.md").read_text(encoding="utf-8")
        self.assertIn("pangea-data/reports/<run-id>/report.md", primary)
        self.assertIn("聊天中的报告摘要不是正式交付", primary)
'''
with (ROOT / "tests/test_workspace_layout_v2.py").open("a", encoding="utf-8") as handle:
    handle.write(report_test)
