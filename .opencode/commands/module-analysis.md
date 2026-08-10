---
description: 对指定模块执行完整或快速的语义化 DFX 全量测试分析
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前复用本会话已经成功的 portable preflight。一次工具调用只启动一个进程，并使用 preflight 返回的 `project_root` 作为结构化 workdir；不要通过 `cd`、CMD 或 PowerShell 包装切目录。可使用 `--fast` 选择速度型。

模块分析创建 Run 时，运行时自动解析每个已登记仓库的 `HEAD^{commit}`、写入 `repository_commits`，并创建当前 Run 专属 commit 快照。后续代码地图、流程、分支和 analysis-worker 只读取这些快照。

先生成任务契约草稿：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py draft-contract-v2 --scenario module-analysis --target <模块> --repository <已登记仓名> --analysis-depth <complete|fast>
```

展示命令返回的完整任务契约，包含目标、仓库与 commit、输入材料、排除范围、深度和已知缺口。`complete` 必须询问是否还有补充材料；用户已明确要求按当前资料直接开始时，可记录 `user_explicit_bypass`。用户补充材料或调整范围时执行：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py revise-contract-v2 --contract-id <ID> --expected-revision <当前revision> --file <revised-task-contract.json>
<preflight.python_executable> -X utf8 runtime/runctl.py confirm-contract-v2 --contract-id <ID> --revision <当前revision> --source <user_reply|user_explicit_bypass|auto_unambiguous> --materials-status <provided|confirmed_none|unchanged>
<preflight.python_executable> -X utf8 runtime/runctl.py activate-contract-v2 --contract-id <ID> --run-id <Run-ID>
```

`fast` 在任务无歧义时可在展示契约后使用 `auto_unambiguous`。未确认契约时不创建 Run、快照、checkpoint 或调用 analysis-worker。

## 语义分析

默认语义模式依次执行：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py prepare-semantic-analysis-v2 --run-id <Run ID>
<preflight.python_executable> -X utf8 runtime/runctl.py stage-semantic-plan-v2 ...
<preflight.python_executable> -m tooling.pangea_cli semantic unit-context --run-id <Run ID> --unit-id <Unit ID>
<preflight.python_executable> -X utf8 runtime/runctl.py stage-semantic-unit-v2 ...
<preflight.python_executable> -X utf8 runtime/runctl.py assemble-semantic-analysis-v2 --run-id <Run ID>
```

`semantic unit-context` 会冻结 `plan.original.json`，并把源码输出为带真实绝对行号的 `sources[].lines[]`；analysis-worker 的 `source_evidence.path` 必须逐字复制 `sources[].path`，`line` 必须直接取 `sources[].lines[].line`。

assemble 或 stage unit 失败时，不得写临时脚本或直接修改 `plan.json` / `units/*.json`。固定使用：

```text
<preflight.python_executable> -m tooling.pangea_cli semantic diagnose --run-id <Run ID>
```

- plan 被外部改写：`semantic restore-plan --run-id <Run ID>`；
- 某个 unit evidence 无效：`semantic reset-unit --run-id <Run ID> --unit-id <Unit ID>`，随后重新生成该 unit context、重新调用 analysis-worker、重新 stage；
- 不提供 `sync-plan-sha` 或 `fix-evidence`，禁止把旧结果改到“能过校验”。

`complete` 覆盖全部确认源码范围；`fast` 仅降低非关键范围深度并明确 `depth_limitations`。逐行 obligation 问答只在用户明确要求“逐行问答模式”时启用。

## 深度与审计门禁

完成分析后执行：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py stage-analysis-v2 --run-id <Run ID> --file <完整分析模型JSON>
<preflight.python_executable> -X utf8 runtime/runctl.py stage-report-v2 --run-id <Run ID> --file <报告外壳JSON>
<preflight.python_executable> -X utf8 runtime/runctl.py judge-analysis-v2 --run-id <Run ID>
<preflight.python_executable> -X utf8 runtime/runctl.py apply-audit-v2 --run-id <Run ID> --file <audit-opinion.json>
<preflight.python_executable> -X utf8 runtime/runctl.py finalize-v2 --run-id <Run ID> --model pangea-data/runs/<Run ID>/internal/report-model.json
```

完整分析模型覆盖输入消费、入口、Flow Card、分支、状态、资源、并发、错误传播、六维适用性、场景候选、SFMEA、测试流程、用例、追溯与 Coverage disposition。Judge 非 PASS 时不调用 auditor。`FAIL` 或 `CONCERNS` 时完成整改、更新固定模型并重新审计；仅 PASS 后 finalize，并确认 `report.md` 与 `report.html` 均实际存在且非空。

1. 显示 `[梳理中 (._.)]`，生成任务契约：目标模块、仓库与版本、组网、测试重点、可选材料、排除范围和分析深度。
2. 显示 `[分析中 (｀・ω・´)]`，建立语义计划并逐 unit 分析，运行时负责源码范围覆盖、中文内容、六维 DFX、引用行号和模型闭包。
3. 完整型展开关键流程、异常分支、状态、资源、并发、错误传播、相关专项、SFMEA、场景和用例；`--fast` 保留代码地图、关键流程和六维 DFX，并明确深度边界。
4. 资源与规格先轻量扫描；命中资源信号或用户强调时深挖规格、泄漏、过载回落和长稳风险。
5. 显示 `[审核中 (¬_¬)]`，生成同内容的 `report.md` 与离线单文件 `report.html`。