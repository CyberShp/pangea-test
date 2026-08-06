from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / "runtime/runctl.py"
text = path.read_text(encoding="utf-8")
old = '''                elif field == "status":
                    if value not in ANALYSIS_OUTCOMES:
                        raise RunCtlError(f"分析模型 disposition 非法: {label}={value}")
'''
new = '''                elif field == "status":
                    allowed = {"parsed", "partially_parsed", "blocked", "out_of_scope", "unreadable"} \\
                        if collection == "evidence_consumption" else ANALYSIS_OUTCOMES
                    if value not in allowed:
                        raise RunCtlError(f"分析模型 disposition 非法: {label}={value}")
'''
if text.count(old) != 1:
    raise SystemExit(f"status block count={text.count(old)}")
text = text.replace(old, new, 1)
old = '''    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))
    if binding is not None and model.get("analysis_artifact") != binding:
        raise RunCtlError("report-model 未精确绑定当前固定分析模型")
    required_sections = ("code_map", "flows", "branches", "risks")
    empty_sections = [name for name in required_sections if not model.get(name)]
    if empty_sections:
        raise RunCtlError(f"报告模型缺少有效内容: {', '.join(empty_sections)}")
    return model
'''
new = '''    required_sections = ("code_map", "flows", "branches", "risks")
    empty_sections = [name for name in required_sections if not model.get(name)]
    if empty_sections:
        raise RunCtlError(f"报告模型缺少有效内容: {', '.join(empty_sections)}")
    binding = _analysis_model_binding(run_dir, canonical, required=_requires_complete_analysis_model(canonical))
    if binding is not None and model.get("analysis_artifact") != binding:
        raise RunCtlError("report-model 未精确绑定当前固定分析模型")
    return model
'''
if text.count(old) != 1:
    raise SystemExit(f"binding order block count={text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
