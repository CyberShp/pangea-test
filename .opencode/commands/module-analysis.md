---
description: 对指定模块执行完整或快速的 DFX 全量测试分析
agent: pangea-test
---

用户参数：`$ARGUMENTS`。可使用 `--fast` 选择速度型。

创建 Run 时，运行时会自动解析每个已登记仓库的 `HEAD^{commit}`、写入任务契约 `repository_commits`，并在当前 Run 的 `tmp/snapshots/<仓名>/` 创建只读 commit 快照。必须检查 `create-v2` 输出的 `source_snapshots` 和 `internal/source-snapshots.json`；后续代码地图、流程、分支和 DFX 分析只读取这些快照，不直接读取用户源工作区。源仓中的 `M/A/D/??` 只影响自动 pull，不影响从已提交 commit 创建快照。

确认任务契约后，使用真实入口创建 Run：`python3 runtime/runctl.py create-v2 --scenario module-analysis --target <模块> --repository <已登记仓名> --analysis-depth complete`；模块模式不得携带 `--repository-commit`。`--repository` 只能是 `pangea-data/repositories/` 下已登记只读仓的目录名，不接受任意路径；`--fast` 映射为 `--analysis-depth fast`。将版本、组网、重点、材料、排除范围、工具缺口和资源信号以对应参数补充。`code_map`、`flow`、`branches`、`specialist`、`sfmea`、`test_design` 的每个 completed fact 必须带具体、非占位、非机械重复的 `summary` 与 `evidence`。`dfx_scan` 在 complete 和 fast 都必须恰好写六个 canonical DFX fact，每项包含 `dfx`、具体 `conclusion` 与可复核 `evidence`，包括命中和未发现风险的结论。每阶段通过 `python3 -m tooling.pangea_cli data checkpoint` 写入完成或带原因的跳过状态。

深度门禁：完成分析阶段后，先调用 `python3 runtime/runctl.py stage-analysis-v2 --run-id <Run ID> --file <完整分析模型JSON>`。完整分析模型必须覆盖输入消费、入口、Flow Card、分支、状态、资源、并发、错误传播、六维适用性、场景候选、SFMEA、测试流程、用例、追溯与 Coverage disposition。命令失败时不得继续。然后进入审计门禁：主 Agent 调用 `python3 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <报告外壳JSON>`；完整型的代码地图、Flow、分支、场景、用例和全部深度章节由运行时从固定分析模型确定性覆盖生成，Agent 不得手工压缩或删减。 `stage-report-v2` 会自动执行独立 Coverage Judge；也可用 `python3 runtime/runctl.py judge-analysis-v2 --run-id <Run ID>` 重跑。Judge 非 PASS 时禁止调用 auditor。并使用命令实际返回的固定模型路径和 SHA-256，将固定相对路径 `internal/report-model.json` 和哈希交给只读 auditor。auditor 仅核对绑定并输出 `audit_opinion` 2.0。将意见文件提交为 `python3 runtime/runctl.py apply-audit-v2 --run-id <Run ID> --file <audit-opinion.json>`。若为 `FAIL` 或 `CONCERNS`，每项整改使用具体 `closure` 与 `evidence: {artifact, location, verification}`，其中 artifact 是无 `..` 的 Run 相对路径，location 是具体锚点，verification 是独立复核结论；可选 facts 使用 `rework_summary`。更新固定模型并重新审计。仅 `PASS` 后，使用固定模型完成：`python3 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json`。必须确认命令返回的 `pangea-data/reports/<Run ID>/report.md` 与 `report.html` 均实际存在且非空，再向用户报告完成和文件位置。

1. 显示 `[梳理中 (._.)]`，生成任务契约：目标模块、仓库与版本、组网、测试重点、可选材料、排除范围和分析深度。
2. 默认完整型依次执行代码地图、关键流程、异常分支、六个 DFX 扫描、命中专项深挖、内部 SFMEA、场景和用例；不在中间阶段等待确认。
3. `--fast` 保留相同阶段与六个 DFX，但限制调用链展开和分支深度，报告中必须写明边界。
4. 资源与规格先轻量扫描；命中资源信号或用户强调时深挖规格、泄漏、过载回落和长稳风险。
5. 显示 `[审核中 (¬_¬)]`，生成同内容的 `report.md` 与离线单文件 `report.html`。
