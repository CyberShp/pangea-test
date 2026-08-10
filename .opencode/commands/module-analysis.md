---
description: 对指定模块执行完整或快速的语义化 DFX 全量测试分析
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前复用本会话已经成功的 portable preflight，并使用 `project_root` 作为结构化 workdir。可使用 `--fast` 选择速度型。Preflight 中与当前目标无关的仓库 blocked 或 clang-tidy/cppcheck/codeql 等可选工具缺失只记录为降级信息，不排障、不安装、不重试。

## 任务范围定位

在生成任务契约前先确定源码范围，不做人工代码地图。

1. 只使用正式 repo CLI 直接定位模块；`locate` 不是 runctl 子命令：

```text
<preflight.python_executable> -m tooling.pangea_cli repo locate --repository <已登记仓名> --query <模块关键词>
```

优先使用返回的 `suggested_scopes` / `source_scope_args`。禁止尝试 `runctl.py locate`；locate 有结果后禁止再用 list/glob/Python walk 重复定位。只有 locate 无结果时允许一次补充搜索。

2. 对选中的 scope 执行一次关联扫描：

```text
<preflight.python_executable> -m tooling.pangea_cli repo related --repository <已登记仓名> --scope <scope>
```

`related` 只提供 include、registration、shared_symbol 三类跨模块候选。存在候选时，在任务契约生成前使用 question 工具统一询问用户：

- 仅分析当前模块
- 纳入建议关联模块
- 自定义分析范围

没有候选时直接使用当前 scope。

3. 范围确定后不得为了“了解核心逻辑”手工 read/grep 一遍源码。正式 code map 由契约激活后的 Runtime 完整读取确认 scope 生成。只有 locate 候选无法区分，或 Runtime 明确报告证据缺口时，才允许定点补读。

## 任务契约

模块分析命令必须复制下面的正式模板，不自行摸索参数，不为正式模板先跑 `runctl.py --help`：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py draft-contract-v2 --scenario module-analysis --target <模块> --repository <已登记仓名> --source-scope <仓名>=<路径> --analysis-depth <complete|fast>
```

固定约束：

- 必须有 `--scenario module-analysis`、`--target`、`--repository`；
- 禁止传 `--repository-commit`，commit 由 Runtime 自动绑定 HEAD；
- 禁止传模板未声明的 `--capability-pack` 等参数；
- 每个 source scope 使用独立参数，禁止逗号拼接：

```text
--source-scope <仓名>=<路径1> --source-scope <仓名>=<路径2>
```

展示完整任务契约，包含目标、仓库与 commit、输入材料、source scope、排除范围、深度、关联模块决定和已知缺口。

`complete` 必须使用 question 工具统一确认：

- 按当前范围开始
- 我还有材料需要补充
- 我需要调整分析范围

用户补充材料或调整范围时，以 `draft-contract-v2` 返回的 `task_contract` 对象为基准修改并保存为 JSON。`revise-contract-v2 --file` 只接受修改后的 `task_contract` 对象本身，不得把包含 `contract_id`、`revision`、`activation` 等字段的外层 contract record 作为输入：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py revise-contract-v2 --contract-id <ID> --expected-revision <当前revision> --file <revised-task-contract.json>
<preflight.python_executable> -X utf8 runtime/runctl.py confirm-contract-v2 --contract-id <ID> --revision <当前revision> --source <user_reply|user_explicit_bypass|auto_unambiguous> --materials-status <provided|confirmed_none|unchanged>
<preflight.python_executable> -X utf8 runtime/runctl.py activate-contract-v2 --contract-id <ID> --run-id <Run-ID>
```

用户已在同一请求中明确要求按当前资料直接开始时，可记录 `user_explicit_bypass`。`fast` 在任务无歧义时可在展示契约后使用 `auto_unambiguous`。未确认契约时不创建 Run、快照、checkpoint 或调用 analysis-worker。

## 语义分析

契约激活后直接进入正式语义流程；主 Agent 不再手工建立代码地图：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py prepare-semantic-analysis-v2 --run-id <Run ID>
<preflight.python_executable> -X utf8 runtime/runctl.py stage-semantic-plan-v2 --run-id <Run ID> --file <系统临时目录/semantic-plan.json> --check-only
<preflight.python_executable> -X utf8 runtime/runctl.py stage-semantic-plan-v2 --run-id <Run ID> --file <系统临时目录/semantic-plan.json>
<preflight.python_executable> -m tooling.pangea_cli semantic unit-context --run-id <Run ID> --unit-id <Unit ID>
<preflight.python_executable> -X utf8 runtime/runctl.py stage-semantic-unit-v2 --run-id <Run ID> --file <系统临时目录/semantic-unit.json>
<preflight.python_executable> -X utf8 runtime/runctl.py assemble-semantic-analysis-v2 --run-id <Run ID>
```

正式流程中的实际分析步骤是 analysis-worker，不是另一个 CLI：

1. `prepare-semantic-analysis-v2` 生成 planner context，pangea-test 将该 context 交给 analysis-worker；
2. analysis-worker 返回 strict JSON plan，pangea-test 将原样 JSON 暂存到系统临时目录；先用同一个 `stage-semantic-plan-v2 --check-only --file` 查看各 unit 字节数和覆盖结果，通过后去掉 `--check-only` 正式冻结；
3. 对每个 unit 运行 `semantic unit-context`，将生成的 context 交给 analysis-worker；
4. analysis-worker 返回完整 `semantic_analysis_unit` JSON，使用 `stage-semantic-unit-v2 --file` 冻结；
5. 全部 unit 完成后运行 assemble。

计划中一个 unit 的 `focus` / `dfx` 都可以包含多个值。所有 unit 的 focus 并集必须覆盖 `code_map、flows、branches、dfx、specialist、sfmea、scenarios、test_cases`，DFX 并集必须覆盖全部六类；不要求为了每种类型单独创建一个 unit。

`prepare-semantic-analysis-v2` / Runtime 对确认 source scope 完整读取并建立 code map。`semantic unit-context` 把源码输出为带真实绝对行号的 `sources[].lines[]`；analysis-worker 的 `source_evidence.path` 必须逐字复制 `sources[].path`，`line` 必须直接取 `sources[].lines[].line`。

正式调用统一使用 `--file`，不把完整 JSON 放在 PowerShell/CMD/bash 命令行中。每个命令独立执行，不使用 `&&`、`;` 或 shell 包装串联。Run 内的 `tmp/` 由 Runtime 管理，其中 `tmp/snapshots/` 是冻结源码；辅助 JSON 使用系统临时目录并在提交后删除，不得在 Run `tmp/` 下创建修复脚本或把它当成正式产出目录。

assemble 或 stage unit 失败时固定使用：

```text
<preflight.python_executable> -m tooling.pangea_cli semantic diagnose --run-id <Run ID>
```

- plan 被外部改写：`semantic restore-plan --run-id <Run ID>`；
- unit evidence 无效：`semantic reset-unit --run-id <Run ID> --unit-id <Unit ID>`，随后只重做该 unit；
- 禁止写临时修复脚本或直接修改 `plan.json` / `units/*.json`。

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

Judge 非 PASS 时不调用 auditor。`FAIL` 或 `CONCERNS` 时完成整改并重新审计；仅 PASS 后 finalize，并确认 `report.md` 与 `report.html` 均实际存在且非空。
