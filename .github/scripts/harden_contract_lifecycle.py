from __future__ import annotations

import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]

# Contract records carry a revision, and confirmations bind the exact revision.
schema_path = root / "schemas/contract-record.schema.json"
schema = json.loads(schema_path.read_text(encoding="utf-8"))
if "revision" not in schema["required"]:
    schema["required"].insert(schema["required"].index("status"), "revision")
schema["properties"]["revision"] = {"type": "integer", "minimum": 1}
confirmation = schema["properties"]["confirmation"]
if "confirmed_revision" not in confirmation["required"]:
    confirmation["required"].insert(0, "confirmed_revision")
confirmation["properties"]["confirmed_revision"] = {"type": "integer", "minimum": 1}
schema_path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

path = root / "runtime/runctl.py"
text = path.read_text(encoding="utf-8")
if "import shutil\n" not in text:
    text = text.replace("import re\nimport stat\n", "import re\nimport shutil\nimport stat\n", 1)

old = '''        "artifact_type": "task_contract_record", "schema_version": "1.0",
        "contract_id": contract_id, "status": "draft", "confirmation_required": required,
'''
new = '''        "artifact_type": "task_contract_record", "schema_version": "1.0",
        "contract_id": contract_id, "revision": 1, "status": "draft", "confirmation_required": required,
'''
if text.count(old) != 1:
    raise SystemExit(f"draft revision block count={text.count(old)}")
text = text.replace(old, new, 1)

revise_function = r'''

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
'''
marker = "\ndef confirm_contract_v2(args: argparse.Namespace) -> None:\n"
if text.count(marker) != 1:
    raise SystemExit(f"confirm function marker count={text.count(marker)}")
text = text.replace(marker, revise_function + marker, 1)

old = '''    if record["status"] != "draft":
        raise RunCtlError("只有 draft 任务契约可以确认")
    if record["confirmation_required"] and args.source not in {"user_reply", "user_explicit_bypass"}:
'''
new = '''    if record["status"] != "draft":
        raise RunCtlError("只有 draft 任务契约可以确认")
    if record["revision"] != args.revision:
        raise RunCtlError(
            f"任务契约 revision 已变化: requested={args.revision}, current={record['revision']}"
        )
    if record["confirmation_required"] and args.source not in {"user_reply", "user_explicit_bypass"}:
'''
if text.count(old) != 1:
    raise SystemExit(f"confirm revision check count={text.count(old)}")
text = text.replace(old, new, 1)
old = '''    confirmation = {"source": args.source, "materials_status": args.materials_status,
                    "note": args.note, "confirmed_at": now}
'''
new = '''    confirmation = {"confirmed_revision": record["revision"], "source": args.source,
                    "materials_status": args.materials_status, "note": args.note, "confirmed_at": now}
'''
if text.count(old) != 1:
    raise SystemExit(f"confirmation payload count={text.count(old)}")
text = text.replace(old, new, 1)

start = text.index("def activate_contract_v2(args: argparse.Namespace) -> None:\n")
end = text.index("\n\ndef _assert_report_contract_and_sections", start)
activation = r'''def _rollback_activation_run(root: Path, run_id: str) -> None:
    """Remove only a newly-created, checkpoint-free activation Run."""
    from runtime import data_runtime
    workspace = data_runtime.ensure_layout(root)
    run_dir = workspace / "runs" / run_id
    if not run_dir.exists() and not run_dir.is_symlink():
        return
    if run_dir.is_symlink() or not run_dir.is_dir() or run_dir.resolve().parent != (workspace / "runs").resolve():
        raise RunCtlError(f"拒绝回滚不安全的激活 Run: {run_dir}")
    manifest_path = run_dir / "manifest.json"
    manifest = data_runtime.read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
        raise RunCtlError(f"拒绝回滚 manifest 不匹配的激活 Run: {run_id}")
    if manifest.get("status") != "active" or manifest.get("checkpoint_count") != 0 or manifest.get("deliverables") is not None:
        raise RunCtlError(f"拒绝回滚已有分析工件或已结束的 Run: {run_id}")
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
    current_binding = _preflight_binding(root, record["task_contract"]["repositories"])
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
                print(json.dumps(_activation_payload(run_dir, args.contract_id), ensure_ascii=False))
                return
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        canonical = data_runtime.read_json(run_dir / "internal" / "task-contract.json")
        if (isinstance(manifest, dict) and manifest.get("run_id") == run_id
                and manifest.get("status") == "active" and manifest.get("checkpoint_count") == 0
                and manifest.get("deliverables") is None and canonical == record["task_contract"]):
            _rollback_activation_run(root, run_id)
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
    )
    try:
        payload = create_v2_run(namespace)
        run_dir = Path(payload["run_dir"])
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        activated = json.loads(json.dumps(record, ensure_ascii=False))
        activated.update({"status": "activated", "updated_at": now,
                          "activation": {"run_id": payload["run_id"], "activated_at": now}})
        validate(activated, "contract-record.schema.json")
        data_runtime.atomic_write_json(run_dir / CONTRACT_RECORD_RELATIVE, activated)
        data_runtime.atomic_write_json(run_dir / CONTRACT_CONFIRMATION_RELATIVE, activated["confirmation"])
        manifest = data_runtime.read_json(run_dir / "manifest.json")
        manifest["contract_record_file"] = CONTRACT_RECORD_RELATIVE
        manifest["contract_confirmation_file"] = CONTRACT_CONFIRMATION_RELATIVE
        validate(manifest, "session-manifest.schema.json")
        data_runtime.atomic_write_json(run_dir / "manifest.json", manifest)
        # Durable contract state is published last. A retry can recover from the Run copy.
        data_runtime.atomic_write_json(path, activated)
    except BaseException as exc:
        try:
            _rollback_activation_run(root, run_id)
        except BaseException as rollback_exc:
            raise RunCtlError(f"任务契约激活失败且安全回滚失败: {exc}; rollback: {rollback_exc}") from exc
        raise
    print(json.dumps({**payload, "contract_id": args.contract_id, "contract_status": "activated",
                      "contract_record": str(run_dir / CONTRACT_RECORD_RELATIVE)}, ensure_ascii=False))
'''
text = text[:start] + activation + text[end:]

# Parser: revision command and revision-bound confirmation.
anchor = '    confirm2 = sub.add_parser("confirm-contract-v2", help="持久化任务契约确认")\n'
revise_parser = '''    revise2 = sub.add_parser("revise-contract-v2", help="按用户反馈修订 draft 任务契约")
    revise2.add_argument("--contract-id", required=True)
    revise2.add_argument("--expected-revision", required=True, type=int)
    revise2.add_argument("--file", required=True)
    revise2.add_argument("--root")
    revise2.set_defaults(func=revise_contract_v2)
'''
if text.count(anchor) != 1:
    raise SystemExit(f"confirm parser anchor count={text.count(anchor)}")
text = text.replace(anchor, revise_parser + anchor, 1)
old = '    confirm2.add_argument("--contract-id", required=True)\n'
new = '    confirm2.add_argument("--contract-id", required=True)\n    confirm2.add_argument("--revision", required=True, type=int)\n'
if text.count(old) != 1:
    raise SystemExit(f"confirm parser contract count={text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

# Commands and primary policy describe revision before confirmation.
for relative in (".opencode/commands/module-analysis.md", ".opencode/commands/mr-regression.md"):
    doc_path = root / relative
    doc = doc_path.read_text(encoding="utf-8")
    if "revise-contract-v2" not in doc:
        marker = "确认后执行：" if "module-analysis" in relative else "```text\n<preflight.python_executable> runtime/runctl.py confirm-contract-v2"
        if "module-analysis" in relative:
            addition = '''用户补充材料、调整范围或修正假设时，先将完整修订后的 `task_contract` 写入 JSON 文件，再执行：

```text
<preflight.python_executable> runtime/runctl.py revise-contract-v2 --contract-id <ID> --expected-revision <当前revision> --file <revised-task-contract.json>
```

必须展示新的 revision，确认只能绑定最新 revision。'''
            if doc.count(marker) != 1:
                raise SystemExit(f"module confirmation marker count={doc.count(marker)}")
            doc = doc.replace(marker, addition + "\n\n" + marker, 1)
        else:
            addition = '''若用户补充原问题、材料、关联仓或调整范围，必须先执行 `revise-contract-v2 --contract-id <ID> --expected-revision <当前revision> --file <revised-task-contract.json>`，展示新 revision 后再确认。\n\n'''
            if doc.count(marker) != 1:
                raise SystemExit(f"MR confirmation block marker count={doc.count(marker)}")
            doc = doc.replace(marker, addition + marker, 1)
    doc = doc.replace("confirm-contract-v2 --contract-id <ID> --source", "confirm-contract-v2 --contract-id <ID> --revision <当前revision> --source")
    doc_path.write_text(doc, encoding="utf-8")

agent_path = root / ".opencode/agents/pangea-test.md"
agent = agent_path.read_text(encoding="utf-8")
agent = agent.replace(
    "必须依次执行 `draft-contract-v2`、展示 canonical 契约、`confirm-contract-v2`、`activate-contract-v2`；",
    "必须依次执行 `draft-contract-v2`、展示 canonical 契约、按用户反馈执行零次或多次 `revise-contract-v2`、以最新 revision 执行 `confirm-contract-v2`、再执行 `activate-contract-v2`；",
    1,
)
agent_path.write_text(agent, encoding="utf-8")

readme_path = root / "README.md"
readme = readme_path.read_text(encoding="utf-8")
readme = readme.replace(
    "draft-contract-v2 -> confirmed-contract-v2 -> activate-contract-v2 -> Run/只读快照",
    "draft-contract-v2 -> revise-contract-v2(可选，多次) -> confirm-contract-v2 -> activate-contract-v2 -> Run/只读快照",
    1,
)
readme_path.write_text(readme, encoding="utf-8")

# Update lifecycle tests and add revision coverage.
test_path = root / "tests/test_contract_lifecycle.py"
test = test_path.read_text(encoding="utf-8")
replacements = {
    '"--source", "auto_unambiguous", "--materials-status", "confirmed_none"': '"--revision", "1", "--source", "auto_unambiguous", "--materials-status", "confirmed_none"',
    '"--source", "user_reply", "--materials-status", "confirmed_none"': '"--revision", "1", "--source", "user_reply", "--materials-status", "confirmed_none"',
    '"--source", "auto_unambiguous", "--materials-status", "unchanged"': '"--revision", "1", "--source", "auto_unambiguous", "--materials-status", "unchanged"',
}
for old, new in replacements.items():
    test = test.replace(old, new)
insert_marker = '    def test_direct_create_is_rejected_on_marked_project_root(self) -> None:\n'
revision_test = r'''    def test_user_feedback_revises_canonical_contract_and_stale_confirmation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                             "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "revised")
            contract = draft["task_contract"]
            contract["input_refs"] = ["pangea-data/inbox/chap-design.docx"]
            contract["test_focus"] = ["双向 CHAP 异常恢复"]
            revised_file = root / "revised-contract.json"
            revised_file.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            revised = self.cli(root, "revise-contract-v2", "--contract-id", "revised",
                               "--expected-revision", "1", "--file", str(revised_file))
            self.assertEqual(2, revised["revision"])
            stale = self.cli(root, "confirm-contract-v2", "--contract-id", "revised", "--revision", "1",
                             "--source", "user_reply", "--materials-status", "provided", expected=2)
            self.assertIn("revision 已变化", stale["stderr"])
            self.cli(root, "confirm-contract-v2", "--contract-id", "revised", "--revision", "2",
                     "--source", "user_reply", "--materials-status", "provided")
            activated = self.cli(root, "activate-contract-v2", "--contract-id", "revised", "--run-id", "revised-run")
            canonical = json.loads((Path(activated["run_dir"]) / "internal/task-contract.json").read_text())
            self.assertEqual(["pangea-data/inbox/chap-design.docx"], canonical["input_refs"])
            self.assertEqual(["双向 CHAP 异常恢复"], canonical["test_focus"])

'''
if test.count(insert_marker) != 1:
    raise SystemExit(f"revision test marker count={test.count(insert_marker)}")
test = test.replace(insert_marker, revision_test + insert_marker, 1)
test_path.write_text(test, encoding="utf-8")

agent_test_path = root / "tests/test_agent_v2.py"
agent_test = agent_test_path.read_text(encoding="utf-8")
old = '            for command in ("draft-contract-v2", "confirm-contract-v2", "activate-contract-v2"):\n'
new = '            for command in ("draft-contract-v2", "revise-contract-v2", "confirm-contract-v2", "activate-contract-v2"):\n'
if agent_test.count(old) != 1:
    raise SystemExit(f"agent lifecycle command tuple count={agent_test.count(old)}")
agent_test_path.write_text(agent_test.replace(old, new, 1), encoding="utf-8")
