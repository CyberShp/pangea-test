from pathlib import Path

path = Path(__file__).with_name("apply_analysis_depth_contract.py")
text = path.read_text(encoding="utf-8")
old = '''runctl = replace_once(
    runctl,
    '    plan = _load_v2_workflow_plan(run_dir)\\n    _assert_analysis_stages_complete(run_dir, plan)\\n    if args.json is not None:\\n',
    '    plan = _load_v2_workflow_plan(run_dir)\\n'
    '    _assert_analysis_stages_complete(run_dir, plan)\\n'
    '    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))\\n'
    '    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))\\n'
    '    if args.json is not None:\\n',
    "stage report load analysis",
)
'''
new = '''runctl = replace_once(
    runctl,
    'def stage_report_v2(args: argparse.Namespace) -> None:\\n'
    '    """Validate and atomically stage the sole report model accepted by audit."""\\n'
    '    from runtime import data_runtime, reporting\\n\\n'
    '    root = Path(args.root).resolve() if args.root else ROOT\\n'
    '    run_dir, manifest = data_runtime._load_run(root, args.run_id)\\n'
    '    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:\\n'
    '        raise RunCtlError("已结束 Run 不可写入报告模型")\\n'
    '    if manifest.get("audit", {}).get("status") == "PASS":\\n'
    '        raise RunCtlError("报告模型已经 PASS；修改前必须开启新的审计流程")\\n'
    '    plan = _load_v2_workflow_plan(run_dir)\\n'
    '    _assert_analysis_stages_complete(run_dir, plan)\\n'
    '    if args.json is not None:\\n',
    'def stage_report_v2(args: argparse.Namespace) -> None:\\n'
    '    """Validate and atomically stage the sole report model accepted by audit."""\\n'
    '    from runtime import data_runtime, reporting\\n\\n'
    '    root = Path(args.root).resolve() if args.root else ROOT\\n'
    '    run_dir, manifest = data_runtime._load_run(root, args.run_id)\\n'
    '    if manifest.get("status") in data_runtime.TERMINAL_RUN_STATUSES:\\n'
    '        raise RunCtlError("已结束 Run 不可写入报告模型")\\n'
    '    if manifest.get("audit", {}).get("status") == "PASS":\\n'
    '        raise RunCtlError("报告模型已经 PASS；修改前必须开启新的审计流程")\\n'
    '    plan = _load_v2_workflow_plan(run_dir)\\n'
    '    _assert_analysis_stages_complete(run_dir, plan)\\n'
    '    contract = _assert_formal_task_contract(data_runtime.read_json(run_dir / "internal" / "task-contract.json"))\\n'
    '    analysis_binding = _analysis_model_binding(run_dir, contract, required=_requires_complete_analysis_model(contract))\\n'
    '    if args.json is not None:\\n',
    "stage report load analysis",
)
'''
if text.count(old) != 1:
    raise SystemExit(f"patch block count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
