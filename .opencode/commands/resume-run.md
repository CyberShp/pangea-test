---
description: 识别并恢复 PANGEA-TEST 未完成分析 Run
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前必须已有本会话成功的 portable preflight。禁止 `cd`、`cd /d`、`&&`、`||`、`;` 和手工盘符转换；一次工具调用只启动一个进程，并使用 preflight 返回的 `project_root` 作为结构化 workdir。preflight 未解析出唯一项目根时停止并询问用户，不得扫描盘符或猜测目录。


当参数提供 Run ID 时，先运行 `<preflight.python_executable> runtime/runctl.py resume-v2 --run-id <Run ID>`；未提供时运行 `<preflight.python_executable> -m tooling.pangea_cli data incomplete-runs` 并请用户选择。后续检查点、风险卡和报告均写回该 Run。恢复时仓库只能引用任务契约中 `pangea-data/repositories/` 下的已登记仓名。

1. 扫描 `pangea-data/runs/`，列出未完成 Run 的目标、模式、最后阶段、未完成项与缺口。
2. 若参数指定 Run，或用户从候选中选择后，读取其任务契约、inventory、obligation ledger、已验证 fragments、检查点、风险账本和临时目录状态，仅继续未完成阶段。先检查 `resume-v2` 返回的 snapshot manifest、仓名与 `commit_sha`；已有有效快照必须继续读取该 Run `tmp/snapshots/` 下的只读内容，不得 checkout、reset、切换或重新定位源仓。R2 运行时接线完成后，恢复必须重新校验 context-pack 和 receipt 哈希，且只重派失败 obligations 给同一个 analysis-worker。快照缺失或清单无效时标记覆盖缺口，只有用户提供新的 MR commit/ref 后才能创建新的快照。若 audit gate 指出未闭环项，按上一轮 `required_actions` 数组从 `1` 起始的位置创建 `action_closures`；每项包含 `action_index`、具体 `closure` 和 `evidence: {artifact, location, verification}`，其中 artifact 必须是真实存在的 Run 内相对文件。写入 `<rework.json>` 后执行 `<preflight.python_executable> runtime/runctl.py record-rework-v2 --run-id <Run ID> --file <rework.json>`。整改更新后必须重写并重新计算固定模型 `internal/report-model.json` 的 SHA-256，再提交新的 `audit_opinion` 2.0；不得跳过恢复或以新的 PASS 意见覆盖未整改项。
3. 不把新任务自动合并进旧 Run。Run 已失去必要仓库、版本或输入时，显示 `[难过中 (；へ：)]` 并说明阻塞。
4. 分析完成但固定模型不存在时，先执行 `stage-report-v2` 实际落盘；审计通过后只能执行 `finalize-v2`，在 `pangea-data/reports/<Run ID>/` 生成正式 `report.md` 和 `report.html`。未看到两个实际非空文件不得声称完成。


恢复 Run 时必须读取 manifest 中的 `contract_record_file` 和 `contract_confirmation_file`。存在生命周期文件时，两者必须有效且契约状态为 activated；缺失确认不得继续。历史 Run 未包含这两个字段时按 legacy 只读兼容，不反向伪造确认记录。
