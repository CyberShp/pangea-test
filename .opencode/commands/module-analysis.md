---
description: 对指定模块执行完整或快速的语义化 DFX 全量测试分析
agent: pangea-test
---

用户参数：`$ARGUMENTS`

执行命令前复用本会话已经成功的 portable preflight，并使用 `project_root` 作为结构化 workdir。可使用 `--fast` 选择速度型。

## 任务范围定位

在生成任务契约前先确定源码范围，禁止逐层目录遍历猜模块路径。

1. 使用一次直接定位：

```text
<preflight.python_executable> -m tooling.pangea_cli repo locate --repository <已登记仓名> --query <模块关键词>
```

优先使用返回的 `suggested_scopes` / `source_scope_args`，不要重新用 list/glob 逐层寻找。只有 locate 无结果时才进行一次补充搜索并向用户说明未直接定位成功。

2. 对选中的 scope 执行一次关联扫描：

```text
<preflight.python_executable> -m tooling.pangea_cli repo related --repository <已登记仓名> --scope <scope>
```

`related` 只提供 include、registration、shared_symbol 三类跨模块候选。存在候选时，在任务契约生成前使用 question 工具统一询问用户：

- 仅分析当前模块
- 纳入建议关联模块
- 自定义分析范围

没有候选时直接使用当前 scope。

3. 模块分析的 `draft-contract-v2` 禁止传 `--repository-commit`；commit 由 Runtime 自动绑定 HEAD。每个 source scope 使用独立参数，不得逗号拼接：

```text
--source-scope <仓名>=<路径1> --source-scope <仓名>=<路径2>
```

## 任务契约

范围确定后只执行一次正确参数的 draft：

```text
<preflight.python_executable> -X utf8 runtime/runctl.py draft-contract-v2 --scenario module-analysis --target <模块> --repository <已登记仓名> --source-scope <仓名>=<路径> --analysis-depth <complete|fast>
```

展示完整任务契约，包含目标、仓库与 commit、输入材料、source scope、排除范围、深度、关联模块决定和已知缺口。

`complete` 必须使用 question 工具统一确认：

- 按当前范围开始
- 我还有材料需要补充
- 我需要调整分析范围

用户已在同一请求中明确要求按当前资料直接开始时，可记录 `user_explicit_bypass`。用户补充材料或调整范围时先 revise，再确认：

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

`semantic unit-context` 把源码输出为带真实绝对行号的 `sources[].lines[]`；analysis-worker 的 `source_evidence.path` 必须逐字复制 `sources[].path`，`line` 必须直接取 `sources[].lines[].line`。

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