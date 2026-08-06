from pathlib import Path

root = Path(__file__).resolve().parents[2]
path = root / ".opencode/commands/mr-regression.md"
text = path.read_text(encoding="utf-8")
canonical = "MR 的 workflow 阶段依次为 `code_map`、`impact_chain`、`mr_baseline`、`dfx_route`、`branches`、`risk_ledger`、`sfmea`、`test_design`、`report`，必须与 `registry/scenarios.json` 及 runctl canonical plan 完全一致。"
if canonical not in text:
    marker = "禁止直接调用 `create-v2`。未激活任务契约前不得开始 MR 影响链分析或创建快照。"
    if text.count(marker) != 1:
        raise SystemExit(f"MR lifecycle marker count={text.count(marker)}")
    text = text.replace(marker, marker + "\n\n" + canonical, 1)
path.write_text(text, encoding="utf-8")
