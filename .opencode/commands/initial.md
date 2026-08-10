---
description: 初始化 PANGEA-TEST 工作空间并恢复未完成任务
agent: pangea-test
---

用户参数：`$ARGUMENTS`

本会话没有成功 preflight 时，只运行一个入口：

```text
<当前 Python 解释器> -m tooling.pangea_cli preflight $ARGUMENTS
```

同一会话已经有成功 preflight 时直接复用，不得再次执行。已有未完成 Run 时，preflight 会复用 24 小时内的 ready receipt，避免重复执行 session-prepare、资料刷新、工具探测和索引；需要显式刷新时使用 `preflight --force`。

所有命令直接使用工具的结构化 `cwd/workdir=<project_root>`，不要通过 `cd`、`cd /d`、`&&`、`;` 或 PowerShell/CMD 包装命令切换目录。一次调用只执行一个进程。

以 preflight JSON 为事实源：

- `project_root` 是项目根目录，`python_executable` 是后续 Python 命令使用的解释器。
- `repository_root` 和 `known_repositories` 用于定位登记仓库。
- `status: workspace_unresolved` 时请用户提供项目根目录。
- `status: degraded` 时直接报告 `step_errors`。
- `reused_preflight: true` 表示本次复用了已有准备状态，不得再补跑 prepare/probe/index。

## 自动恢复未完成 Run

读取 `step_results.session_prepare.incomplete_runs`：

1. 只有一个未完成 Run 时，直接执行：

```text
<preflight.python_executable> runtime/runctl.py resume-v2 --run-id <run-id>
```

然后读取该 Run 的 `last_checkpoint` 对应 checkpoint 文件（若存在）以及 `internal/risk-ledger.json`，按 `resume-v2` 返回的 `next_stage` 继续。不得重新从代码地图或首阶段开始，也不得用聊天记忆代替 checkpoint/risk ledger。

2. 有多个未完成 Run 时，若当前请求中的 Run ID、目标或仓库能唯一对应其中一个，则直接恢复；只有无法唯一判断时才列出候选让用户选择。

3. 用户明确开始新任务时，不自动合并进旧 Run。

## 新增资料

只有本次完整 preflight 的 `step_results.session_prepare.inbox.added` 或 `changed` 大于 0 时，才处理新增/变化资料；`reused_preflight: true` 时不重复扫描或重分类。需要重新检查新放入的资料时执行 `/initial --force`。

分类结果仍按 source_path 串行写入：

```text
<preflight.python_executable> -m tooling.pangea_cli library classify --source-path "<catalog.source_path>" --file <classification.json>
```

preflight receipt 保存在 `pangea-data/session/preflight-receipt.json`。后续任务契约继续绑定该 receipt；恢复已有 Run 时优先使用 Run 内已经持久化的任务契约、checkpoint、risk ledger 和快照。