from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / "runtime/runctl.py"
text = path.read_text(encoding="utf-8")

old = 'CONTRACT_CONFIRMATION_RELATIVE = "internal/contract-confirmation.json"\n'
new = old + 'ACTIVATION_PENDING_RELATIVE = "internal/activation-pending.json"\n'
if text.count(old) != 1 or "ACTIVATION_PENDING_RELATIVE" in text:
    raise SystemExit("activation constant insertion mismatch")
text = text.replace(old, new, 1)

start = text.index("def _rollback_activation_run(root: Path, run_id: str) -> None:\n")
end = text.index("\n\ndef _activation_payload", start)
rollback = r'''def _activation_marker(run_dir: Path, contract_id: str, revision: int) -> dict[str, Any]:
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
    run_dir = workspace / "runs" / run_id
    if not run_dir.exists() and not run_dir.is_symlink():
        return
    if run_dir.is_symlink() or not run_dir.is_dir() or run_dir.resolve().parent != (workspace / "runs").resolve():
        raise RunCtlError(f"拒绝回滚不安全的激活 Run: {run_dir}")
    _activation_marker(run_dir, contract_id, revision)
    manifest = data_runtime.read_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("run_id") != run_id:
        raise RunCtlError(f"拒绝回滚 manifest 不匹配的激活 Run: {run_id}")
    if manifest.get("status") != "active" or manifest.get("checkpoint_count") != 0 or manifest.get("deliverables") is not None:
        raise RunCtlError(f"拒绝回滚已有分析工件或已结束的 Run: {run_id}")
    shutil.rmtree(run_dir)
'''
text = text[:start] + rollback + text[end:]

# Existing partial Runs may be rolled back only with a matching marker.
old = '''        if (isinstance(manifest, dict) and manifest.get("run_id") == run_id
                and manifest.get("status") == "active" and manifest.get("checkpoint_count") == 0
                and manifest.get("deliverables") is None and canonical == record["task_contract"]):
            _rollback_activation_run(root, run_id)
'''
new = '''        if (isinstance(manifest, dict) and manifest.get("run_id") == run_id
                and manifest.get("status") == "active" and manifest.get("checkpoint_count") == 0
                and manifest.get("deliverables") is None and canonical == record["task_contract"]):
            _rollback_activation_run(root, run_id, args.contract_id, record["revision"])
'''
if text.count(old) != 1:
    raise SystemExit(f"partial rollback call count={text.count(old)}")
text = text.replace(old, new, 1)

# Pass the activation marker into Run creation.
old = '''        max_audit_rounds=args.max_audit_rounds, _canonical_contract=contract, _return_payload=True,
    )
'''
new = '''        max_audit_rounds=args.max_audit_rounds, _canonical_contract=contract, _return_payload=True,
        _activation_pending={"artifact_type": "activation_pending", "contract_id": args.contract_id,
                             "revision": record["revision"]},
    )
'''
if text.count(old) != 1:
    raise SystemExit(f"activation namespace count={text.count(old)}")
text = text.replace(old, new, 1)

# Replace the activation transaction. The durable contract publishes after the Run binding;
# failures before that point are rollback-safe because the marker proves ownership.
old_start = text.index("    try:\n        payload = create_v2_run(namespace)\n", text.index("def activate_contract_v2"))
old_end = text.index("    print(json.dumps({**payload", old_start)
transaction = r'''    try:
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
'''
text = text[:old_start] + transaction + text[old_end:]

# Run creation writes the ownership marker immediately after the managed Run exists.
old = '''    created = data_runtime.create_run(root, run_id, contract, args.max_audit_rounds)
    plan = v2_plan(contract)
    run_dir = Path(created["run_dir"])
'''
new = '''    created = data_runtime.create_run(root, run_id, contract, args.max_audit_rounds)
    run_dir = Path(created["run_dir"])
    activation_pending = getattr(args, "_activation_pending", None)
    if activation_pending is not None:
        if not isinstance(activation_pending, dict):
            raise RunCtlError("activation pending marker 必须是 JSON 对象")
        atomic_write(run_dir / ACTIVATION_PENDING_RELATIVE, activation_pending)
    plan = v2_plan(contract)
'''
if text.count(old) != 1:
    raise SystemExit(f"create marker insertion count={text.count(old)}")
text = text.replace(old, new, 1)

# Idempotent activation cleans a stale marker only after synchronizing durable state.
old = '''                data_runtime.atomic_write_json(path, run_record)
                print(json.dumps(_activation_payload(run_dir, args.contract_id), ensure_ascii=False))
                return
'''
new = '''                data_runtime.atomic_write_json(path, run_record)
                try:
                    (run_dir / ACTIVATION_PENDING_RELATIVE).unlink()
                except FileNotFoundError:
                    pass
                print(json.dumps(_activation_payload(run_dir, args.contract_id), ensure_ascii=False))
                return
'''
if text.count(old) != 1:
    raise SystemExit(f"idempotent marker cleanup count={text.count(old)}")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")

# Successful activation leaves no marker; unmarked Runs cannot be deleted by rollback.
test_path = root / "tests/test_contract_lifecycle.py"
test = test_path.read_text(encoding="utf-8")
old = '''            self.assertTrue((run_dir / "internal/contract-confirmation.json").is_file())
            record = json.loads((run_dir / "internal/contract-record.json").read_text())
'''
new = '''            self.assertTrue((run_dir / "internal/contract-confirmation.json").is_file())
            self.assertFalse((run_dir / "internal/activation-pending.json").exists())
            record = json.loads((run_dir / "internal/contract-record.json").read_text())
'''
if test.count(old) != 1:
    raise SystemExit(f"success marker assertion count={test.count(old)}")
test = test.replace(old, new, 1)
marker = '    def test_direct_create_is_rejected_on_marked_project_root(self) -> None:\n'
new_test = r'''    def test_rollback_refuses_unmarked_same_contract_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); self.prepare(root)
            draft = self.cli(root, "draft-contract-v2", "--scenario", "module-analysis", "--target", "chap",
                             "--repository", "driver", "--analysis-depth", "complete", "--contract-id", "owner")
            data_runtime.create_run(root, "foreign-run", draft["task_contract"])
            foreign = root / "pangea-data/runs/foreign-run"
            (foreign / "internal/manual-note.txt").write_text("do not delete", encoding="utf-8")
            with self.assertRaises(runctl.RunCtlError):
                runctl._rollback_activation_run(root, "foreign-run", "owner", 1)
            self.assertTrue(foreign.is_dir())
            self.assertTrue((foreign / "internal/manual-note.txt").is_file())

'''
if test.count(marker) != 1:
    raise SystemExit(f"rollback ownership test marker count={test.count(marker)}")
test_path.write_text(test.replace(marker, new_test + marker, 1), encoding="utf-8")
