---
description: 基于 MR、diff 与只读源码生成回归风险和黑盒测试建议
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前必须已有本会话成功的 portable preflight。禁止 `cd`、`cd /d`、`&&`、`||`、`;` 和手工盘符转换；一次工具调用只启动一个进程，并使用 preflight 返回的 `project_root` 作为结构化 workdir。preflight 未解析出唯一项目根时停止并询问用户，不得扫描盘符或猜测目录。


先生成并展示任务契约草稿：

```text
<preflight.python_executable> runtime/runctl.py draft-contract-v2 --scenario mr-regression --target <模块> --repository <仓名> --repository-commit <仓名>=<40位SHA> --mr-url <MR> --analysis-depth focused
```

若 MR、commit、仓库和目标范围无歧义，可在展示契约后使用 `auto_unambiguous` 确认；存在原问题背景、关联仓、版本或范围歧义时必须等待用户确认：

若用户补充原问题、材料、关联仓或调整范围，必须先执行 `revise-contract-v2 --contract-id <ID> --expected-revision <当前revision> --file <revised-task-contract.json>`，展示新 revision 后再确认。

```text
<preflight.python_executable> runtime/runctl.py confirm-contract-v2 --contract-id <ID> --revision <当前revision> --source <auto_unambiguous|user_reply> --materials-status <provided|confirmed_none|unchanged>
<preflight.python_executable> runtime/runctl.py activate-contract-v2 --contract-id <ID> --run-id <Run-ID>
```

禁止直接调用 `create-v2`。未激活任务契约前不得开始 MR 影响链分析或创建快照。

MR 的 workflow 阶段依次为 `code_map`、`impact_chain`、`mr_baseline`、`dfx_route`、`branches`、`risk_ledger`、`sfmea`、`test_design`、`report`，必须与 `registry/scenarios.json` 及 runctl canonical plan 完全一致。

证据门禁：MR facts、diff、changed hunks、材料选择、搜索过程和源码行证据必须先写入 `<evidence-provenance.json>`，并调用 `<preflight.python_executable> runtime/runctl.py stage-evidence-v2 --run-id <Run ID> --file <evidence-provenance.json>`。失败时不得进入报告审计。

审计门禁：主 Agent 先调用 `<preflight.python_executable> runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <完整报告模型JSON>`，并使用命令实际返回的固定模型路径和 SHA-256，将固定相对路径 `internal/report-model.json` 和哈希交给只读 auditor。auditor 仅核对绑定并输出 `audit_opinion` 2.0。每项 `required_actions` 必须包含 `action_type`、不少于 8 字符的具体 `reason`、可定位的 `anchor` 和可闭环复核的 `verification`。将意见文件提交为 `<preflight.python_executable> runtime/runctl.py apply-audit-v2 --run-id <Run ID> --file <audit-opinion.json>`。若为 `FAIL` 或 `CONCERNS`，每个 rework closure 使用具体 `closure` 和 `evidence: {artifact, location, verification}`；artifact 必须是无 `..` 的 Run 相对路径，location 是具体锚点，verification 是独立的复核结论。随后实际更新固定报告模型并重新计算 SHA-256。下一轮审计的模型哈希必须不同于上一失败轮，同一哈希的 PASS 或再次意见都会被拒绝。仅 `PASS` 后，使用固定模型完成：`<preflight.python_executable> runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json`。必须确认命令返回的 `pangea-data/reports/<Run ID>/report.md` 与 `report.html` 均实际存在且非空，再向用户报告完成和文件位置。

1. 显示 `[梳理中 (._.)]`，读取 MR 链接或输入材料，生成任务契约：目标模块、仓库与版本、MR、组网、重点、材料、排除范围、缺口。
2. 显示 `[分析中 (｀・ω・´)]`，读取 MR 描述、diff、分支、commit 与源码；MR MCP 返回 commit/ref 后，对主仓必须执行 `<preflight.python_executable> -m tooling.pangea_cli repo snapshot --run-id <Run ID> --repository <已登记仓名> --ref <commit> --snapshot-id <安全快照 ID>`。后续源码分析只读取 Run `tmp/snapshots/<安全快照 ID>` 下的只读快照，禁止 checkout、reset、切换源仓或以源仓工作区代替快照。关联仓以 JSON 对象数组执行 `<preflight.python_executable> -m tooling.pangea_cli repo snapshots --run-id <Run ID> --file <snapshots.json>`；无法取得关联仓时完成当前仓分析，在任务契约、风险账本和报告写入覆盖缺口与下一步建议。背景缺失时从证据反推，明确标记为推断。
3. 固定执行原场景回归、改动功能验证、影响链回归、异常与恢复验证；仅按证据调用相关 DFX 子 Agent。
4. 显示 `[审核中 (¬_¬)]`，汇总风险卡、去重、审计黑盒/灰盒可执行性，并交付 `report.md` 与离线单文件 `report.html`。
