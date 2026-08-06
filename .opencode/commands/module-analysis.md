---
description: 对指定模块执行完整或快速的 DFX 全量测试分析
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前必须已有本会话成功的 portable preflight。禁止 `cd`、`cd /d`、`&&`、`||`、`;` 和手工盘符转换；一次工具调用只启动一个进程，并使用 preflight 返回的 `project_root` 作为结构化 workdir。preflight 未解析出唯一项目根时停止并询问用户，不得扫描盘符或猜测目录。
。可使用 `--fast` 选择速度型。

创建 Run 时，运行时会自动解析每个已登记仓库的 `HEAD^{commit}`、写入任务契约 `repository_commits`，并在当前 Run 的 `tmp/snapshots/<仓名>/` 创建只读 commit 快照。必须检查 `create-v2` 输出的 `source_snapshots` 和 `internal/source-snapshots.json`；后续代码地图、流程、分支和 DFX 分析只读取这些快照，不直接读取用户源工作区。源仓中的 `M/A/D/??` 只影响自动 pull，不影响从已提交 commit 创建快照。

先生成任务契约草稿：

```text
<preflight.python_executable> runtime/runctl.py draft-contract-v2 --scenario module-analysis --target <模块> --repository <已登记仓名> --analysis-depth <complete|fast>
```

必须把命令返回的完整任务契约矩阵展示给用户，包含目标、仓库与 commit、输入材料、排除范围、深度和已知缺口。`complete` 必须询问“是否有其他材料需要补充？”并等待用户回复；用户已在同一请求中明确要求按当前资料直接开始时，可记录 `user_explicit_bypass`，但仍须展示契约。用户补充材料、调整范围或修正假设时，先将完整修订后的 `task_contract` 写入 JSON 文件，再执行：

```text
<preflight.python_executable> runtime/runctl.py revise-contract-v2 --contract-id <ID> --expected-revision <当前revision> --file <revised-task-contract.json>
```

必须展示新的 revision，确认只能绑定最新 revision。

确认后执行：

```text
<preflight.python_executable> runtime/runctl.py confirm-contract-v2 --contract-id <ID> --revision <当前revision> --source <user_reply|user_explicit_bypass> --materials-status <provided|confirmed_none|unchanged>
<preflight.python_executable> runtime/runctl.py activate-contract-v2 --contract-id <ID> --run-id <Run-ID>
```

`fast` 在任务无歧义时可在展示契约后使用 `auto_unambiguous` 确认。禁止直接调用 `create-v2`；未确认契约时不得创建 Run、快照、checkpoint 或调用任何代码/DFX 子 Agent。

深度门禁：完成分析阶段后，先调用 `<preflight.python_executable> runtime/runctl.py stage-analysis-v2 --run-id <Run ID> --file <完整分析模型JSON>`。完整分析模型必须覆盖输入消费、入口、Flow Card、分支、状态、资源、并发、错误传播、六维适用性、场景候选、SFMEA、测试流程、用例、追溯与 Coverage disposition。命令失败时不得继续。然后进入审计门禁：主 Agent 调用 `<preflight.python_executable> runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <报告外壳JSON>`；完整型的代码地图、Flow、分支、场景、用例和全部深度章节由运行时从固定分析模型确定性覆盖生成，Agent 不得手工压缩或删减。 `stage-report-v2` 会自动执行独立 Coverage Judge；也可用 `<preflight.python_executable> runtime/runctl.py judge-analysis-v2 --run-id <Run ID>` 重跑。Judge 非 PASS 时禁止调用 auditor。并使用命令实际返回的固定模型路径和 SHA-256，将固定相对路径 `internal/report-model.json` 和哈希交给只读 auditor。auditor 仅核对绑定并输出 `audit_opinion` 2.0。将意见文件提交为 `<preflight.python_executable> runtime/runctl.py apply-audit-v2 --run-id <Run ID> --file <audit-opinion.json>`。若为 `FAIL` 或 `CONCERNS`，每项整改使用具体 `closure` 与 `evidence: {artifact, location, verification}`，其中 artifact 是无 `..` 的 Run 相对路径，location 是具体锚点，verification 是独立复核结论；可选 facts 使用 `rework_summary`。更新固定模型并重新审计。仅 `PASS` 后，使用固定模型完成：`<preflight.python_executable> runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json`。必须确认命令返回的 `pangea-data/reports/<Run ID>/report.md` 与 `report.html` 均实际存在且非空，再向用户报告完成和文件位置。

1. 显示 `[梳理中 (._.)]`，生成任务契约：目标模块、仓库与版本、组网、测试重点、可选材料、排除范围和分析深度。
2. 默认完整型依次执行代码地图、关键流程、异常分支、六个 DFX 扫描、命中专项深挖、内部 SFMEA、场景和用例；不在中间阶段等待确认。
3. `--fast` 保留相同阶段与六个 DFX，但限制调用链展开和分支深度，报告中必须写明边界。
4. 资源与规格先轻量扫描；命中资源信号或用户强调时深挖规格、泄漏、过载回落和长稳风险。
5. 显示 `[审核中 (¬_¬)]`，生成同内容的 `report.md` 与离线单文件 `report.html`。
