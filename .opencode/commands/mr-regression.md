---
description: 基于 MR、diff 与只读源码生成回归风险和黑盒测试建议
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前复用本会话已经成功的 portable preflight。一次工具调用只启动一个进程，并使用 preflight 返回的 `project_root` 作为结构化 workdir；不要通过 `cd`、CMD 或 PowerShell 包装切目录。

先生成并展示任务契约草稿：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py draft-contract-v2 --scenario mr-regression --target <模块> --repository <仓名> --repository-commit <仓名>=<40位SHA> --mr-url <MR> --analysis-depth focused
```

若 MR、commit、仓库和目标范围无歧义，可在展示契约后使用 `auto_unambiguous` 确认；存在原问题背景、关联仓、版本或范围歧义时等待用户确认。用户补充材料或调整范围时，先执行：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py revise-contract-v2 --contract-id <ID> --expected-revision <当前revision> --file <revised-task-contract.json>
<preflight.python_executable> -X utf8 runtime/runctl.py confirm-contract-v2 --contract-id <ID> --revision <当前revision> --source <auto_unambiguous|user_reply> --materials-status <provided|confirmed_none|unchanged>
<preflight.python_executable> -X utf8 runtime/runctl.py activate-contract-v2 --contract-id <ID> --run-id <Run-ID>
```

未激活任务契约前不开始 MR 影响链分析或创建快照。

MR workflow 阶段依次为 `code_map`、`impact_chain`、`mr_baseline`、`dfx_route`、`branches`、`risk_ledger`、`sfmea`、`test_design`、`report`，与 `registry/scenarios.json` 及 runctl canonical plan 保持一致。

审计门禁统一使用 UTF-8 Runtime：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <完整报告模型JSON>
<preflight.python_executable> -X utf8 runtime/runctl.py apply-audit-v2 --run-id <Run ID> --file <audit-opinion.json>
<preflight.python_executable> -X utf8 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json
```

`FAIL` 或 `CONCERNS` 时按 `required_actions` 完成 rework，更新固定报告模型并重新计算 SHA-256 后再审计。仅 `PASS` 后执行 `finalize-v2`，并确认 `report.md` 与 `report.html` 实际存在且非空。

1. 显示 `[梳理中 (._.)]`，读取 MR 链接或输入材料，生成任务契约：目标模块、仓库与版本、MR、组网、重点、材料、排除范围、缺口。
2. 显示 `[分析中 (｀・ω・´)]`，读取 MR 描述、diff、分支、commit 与源码；MR MCP 返回 commit/ref 后，对主仓执行 `<preflight.python_executable> -m tooling.pangea_cli repo snapshot --run-id <Run ID> --repository <已登记仓名> --ref <commit> --snapshot-id <快照 ID>`。后续源码分析只读取 Run `tmp/snapshots/<快照 ID>`。关联仓以 JSON 对象数组执行 `<preflight.python_executable> -m tooling.pangea_cli repo snapshots --run-id <Run ID> --file <snapshots.json>`；无法取得关联仓时完成当前仓分析并记录覆盖缺口。
3. 固定执行原场景回归、改动功能验证、影响链回归、异常与恢复验证；MR reader 读取 MR 事实，analysis-worker 处理运行时生成的 context pack。
4. 显示 `[审核中 (¬_¬)]`，汇总风险卡、去重、审计黑盒/灰盒可执行性，并交付 `report.md` 与离线单文件 `report.html`。