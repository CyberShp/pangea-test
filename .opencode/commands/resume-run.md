---
description: 识别并恢复 PANGEA-TEST 未完成分析 Run
agent: pangea-test
---

用户参数：`$ARGUMENTS`

当参数提供 Run ID 时，先运行 `python3 runtime/runctl.py resume-v2 --run-id <Run ID>`；未提供时运行 `python3 -m tooling.pangea_cli data incomplete-runs` 并请用户选择。后续检查点、风险卡和报告均写回该 Run。恢复时仓库只能引用任务契约中 `pangea-data/repositories/` 下的已登记仓名。

1. 扫描 `pangea-data/runs/`，列出未完成 Run 的目标、模式、最后阶段、未完成项与缺口。
2. 若参数指定 Run，或用户从候选中选择后，读取其任务契约、检查点、风险账本和临时目录状态，仅继续未完成阶段。先检查 `resume-v2` 返回的 snapshot manifest、仓名与 `commit_sha`；已有有效快照必须继续读取该 Run `tmp/snapshots/` 下的只读内容，不得 checkout、reset、切换或重新定位源仓。快照缺失或清单无效时标记覆盖缺口，只有用户提供新的 MR commit/ref 后才能创建新的快照。若 audit gate 指出未闭环项，按上一轮 `required_actions` 数组从 `1` 起始的位置创建 `action_closures`；每项包含 `action_index`、具体 `closure` 和 `evidence: {artifact, location, verification}`，其中 artifact 必须是真实存在的 Run 内相对文件。写入 `<rework.json>` 后执行 `python3 runtime/runctl.py record-rework-v2 --run-id <Run ID> --file <rework.json>`。整改更新后必须重写并重新计算固定模型 `internal/report-model.json` 的 SHA-256，再提交新的 `audit_opinion` 2.0；不得跳过恢复或以新的 PASS 意见覆盖未整改项。
3. 不把新任务自动合并进旧 Run。Run 已失去必要仓库、版本或输入时，显示 `[难过中 (；へ：)]` 并说明阻塞。
4. 审计通过后，只能执行 `python3 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json` 更新该 Run 的 `report.md` 和 `report.html`。
