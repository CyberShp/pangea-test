---
description: 基于 MR、diff 与只读源码生成回归风险和黑盒测试建议
agent: pangea-test
---

用户参数：`$ARGUMENTS`

确认任务契约后，使用真实入口创建 Run：`python3 runtime/runctl.py create-v2 --scenario mr-regression --target <模块> --repository <已登记仓名> --repository-commit <仓名>=<40位小写SHA> --mr-url <MR> --analysis-depth focused`。每个 `--repository` 必须且只能有一个同名 `--repository-commit`；任务契约与只读快照的仓名和 commit 须精确匹配，旧 commit 不能审计或完成。`--repository` 只能是 `pangea-data/repositories/` 下已登记只读仓的目录名，不接受任意路径。将版本、组网、重点、材料、排除范围、工具缺口和路由信号以对应参数补充。MR 的 workflow 阶段依次为 `code_map`、`impact_chain`、`mr_baseline`、`dfx_route`、`branches`、`risk_ledger`、`sfmea`、`test_design`、`report`，必须与 `registry/scenarios.json` 及 runctl canonical plan 完全一致；`task_contract` 和 `audit` 是运行时隐式状态，绝不可伪造为 checkpoint。除 `mr_baseline` 外，每个 completed fact 必须带具体、非占位、非机械重复的 `summary` 与 `evidence`。`mr_baseline` 必须写四项结构化事实，分别使用 `baseline: 原场景回归|改动功能验证|影响链回归|异常与恢复验证`，且每项均包含具体 `verification` 和 `evidence`。分析阶段通过 `python3 -m tooling.pangea_cli data checkpoint` 写入完成或带原因的跳过状态；`report` 仅在审计 PASS 后由 finalize 流程落盘。

审计门禁：主 Agent 先写入 `pangea-data/runs/<Run ID>/internal/report-model.json`，可用 `python3 -c 'import hashlib,pathlib; print(hashlib.sha256(pathlib.Path("pangea-data/runs/<Run ID>/internal/report-model.json").read_bytes()).hexdigest())'` 计算 SHA-256，将固定相对路径 `internal/report-model.json` 和哈希交给只读 auditor。auditor 仅核对绑定并输出 `audit_opinion` 2.0。每项 `required_actions` 必须包含 `action_type`、不少于 8 字符的具体 `reason`、可定位的 `anchor` 和可闭环复核的 `verification`。将意见文件提交为 `python3 runtime/runctl.py apply-audit-v2 --run-id <Run ID> --file <audit-opinion.json>`。若为 `FAIL` 或 `CONCERNS`，每个 rework closure 使用具体 `closure` 和 `evidence: {artifact, location, verification}`；artifact 必须是无 `..` 的 Run 相对路径，location 是具体锚点，verification 是独立的复核结论。随后实际更新固定报告模型并重新计算 SHA-256。下一轮审计的模型哈希必须不同于上一失败轮，同一哈希的 PASS 或再次意见都会被拒绝。仅 `PASS` 后，使用固定模型完成：`python3 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json`。

1. 显示 `[梳理中 (._.)]`，读取 MR 链接或输入材料，生成任务契约：目标模块、仓库与版本、MR、组网、重点、材料、排除范围、缺口。
2. 显示 `[分析中 (｀・ω・´)]`，读取 MR 描述、diff、分支、commit 与源码；MR MCP 返回 commit/ref 后，对主仓必须执行 `python3 -m tooling.pangea_cli repo snapshot --run-id <Run ID> --repository <已登记仓名> --ref <commit> --snapshot-id <安全快照 ID>`。后续源码分析只读取 Run `tmp/snapshots/<安全快照 ID>` 下的只读快照，禁止 checkout、reset、切换源仓或以源仓工作区代替快照。关联仓以 JSON 对象数组执行 `python3 -m tooling.pangea_cli repo snapshots --run-id <Run ID> --file <snapshots.json>`；无法取得关联仓时完成当前仓分析，在任务契约、风险账本和报告写入覆盖缺口与下一步建议。背景缺失时从证据反推，明确标记为推断。
3. 固定执行原场景回归、改动功能验证、影响链回归、异常与恢复验证；仅按证据调用相关 DFX 子 Agent。
4. 显示 `[审核中 (¬_¬)]`，汇总风险卡、去重、审计黑盒/灰盒可执行性，并交付 `report.md` 与离线单文件 `report.html`。
