---
description: 识别并恢复 PANGEA-TEST 未完成分析 Run
agent: pangea-test
---

用户参数：`$ARGUMENTS`

复用本会话已经成功的 portable preflight，并使用 `project_root` 作为结构化 workdir；不要通过 `cd`、CMD 或 PowerShell 包装切目录。

提供 Run ID 时执行：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py resume-v2 --run-id <Run ID>
```

未提供时使用 `<preflight.python_executable> -m tooling.pangea_cli data incomplete-runs` 获取候选；当前请求能唯一对应某个 Run 时直接恢复，只有存在歧义时才让用户选择。

恢复后必须读取 `resume-v2` 返回的 `last_checkpoint` 对应 checkpoint 文件（若存在）和当前 Run `internal/risk-ledger.json`，再从 `next_stage` 继续；不得依赖聊天记忆重新构造已经落盘的事实、风险或阶段状态。

1. 默认 `semantic` Run 读取冻结 `internal/semantic-analysis/plan.json`：计划尚未生成时从 `prepare-semantic-analysis-v2` 继续；已有计划时只处理缺失 unit。每个缺失 unit 必须用 `<preflight.python_executable> -m tooling.pangea_cli semantic unit-context --run-id <Run ID> --unit-id <Unit ID>` 生成带标准路径和绝对行号的 context，再调用 analysis-worker 和 `stage-semantic-unit-v2`；全部 unit 齐全后执行 `assemble-semantic-analysis-v2`。
2. semantic stage/assemble 失败时先执行 `semantic diagnose --run-id <Run ID>`；plan 被改写时执行 `semantic restore-plan`，单元无效时执行 `semantic reset-unit` 后只重做该单元。禁止临时修复脚本、直接编辑 plan/unit JSON、同步旧 `plan_sha256` 或自动改 evidence。
3. `line_obligation` Run 继续读取 inventory、obligation ledger 和 fragments；有 issued assignments 时执行 `execute-analysis-batches-v2`，不切换分析模式。
4. 继续使用当前 Run 已有快照，不重新定位或重建源仓。快照缺失或版本无效时记录覆盖缺口。
5. audit gate 有未闭环项时按上一轮 `required_actions` 完成 rework，再重新生成固定模型并审计。
6. 分析完成但固定模型不存在时执行 `stage-report-v2`；审计 PASS 后执行 `finalize-v2`，确认 `report.md` 和 `report.html` 均实际存在且非空后再报告完成。

恢复 Run 时读取 manifest 中已有的任务契约生命周期记录；历史 Run 没有这些字段时按 legacy 状态继续，不反向伪造记录。