---
description: 初始化 PANGEA-TEST 个人工作空间并探测只读分析能力
agent: pangea-test
---

用户参数：`$ARGUMENTS`

只运行一个真实入口：

```text
<当前 Python 解释器> -m tooling.pangea_cli preflight $ARGUMENTS
```

不得先执行 `cd`，不得使用 `&&`、`||`、`;` 拼接命令，不得把 `/d/...`、`/c/...` 等 MSYS 路径手工转换为 Windows 路径。工具调用必须通过结构化 `cwd/workdir` 保持在当前项目上下文中，一次调用只启动一个进程。

以 preflight JSON 为唯一事实源：

- `project_root` 是经项目标记验证的根目录；`python_executable` 是后续命令唯一允许使用的解释器。
- `repository_root` 和 `known_repositories` 是唯一可用的仓库定位依据。
- `status: workspace_unresolved` 时停止全部仓库搜索、索引、Run 创建和源码分析；只向用户请求真实项目根目录。
- `status: degraded` 时读取 `step_errors`，不得把失败步骤解释成仓库不存在，也不得猜测其他盘符目录。
- 禁止枚举 `C:\`、`D:\`、`/` 等盘符或文件系统根目录寻找“看起来像”的项目；根目录恢复只允许当前目录、其父目录、显式 `--root` 或 `PANGEA_ROOT`。
- `step_results.session_prepare.workspace_inventory` 中：`formal_reports` 是正式交付，`run_history` 是历史 Run，`legacy_reports` 是旧报告。

preflight 已按独立子进程顺序执行 session prepare、资料提示刷新、工具探测和索引；不得重复拼接运行这四条命令。只报告 JSON 中真实成功的结果。


## 仓库访问、更新、索引与快照判定

只以 `step_results.session_prepare.repositories[].access_status` 判断仓库是否可访问。`access_status: ready` 表示仓库、Git 元数据和 HEAD commit 可读取；`worktree_status: dirty`、`update_status: skipped`、detached HEAD、无 upstream、认证失败或 `pull --ff-only` 失败只限制自动更新，不得解释为仓库不存在或没有权限。

当 `index_eligible: true` 时，preflight 仍会执行 index all；索引是否成功只以 `index all` 自身记录为准。当 `snapshot_eligible: true` 时，后续任务可从已提交的 `head_commit` 创建只读快照，源工作区中的 M/A/D/?? 不得阻止读取 Git 对象。


## 新增资料的增量语义分类

portable preflight 完成后，读取 `step_results.session_prepare` 的 `inbox.added`、`inbox.changed` 和 `catalog`。只有新增与变化数量大于零时才进入分类；两者都为 `0` 时不得读取全部 Markdown 或重分类。

1. 从 catalog 关联本次新增或变化记录，只处理存在 `markdown_path`、转换可读且没有有效 `semantic_classification` 的项目。`classification_sha256` 与当前 SHA-256 一致的既有分类跳过；同哈希继承分类也跳过。
2. 先读取标题、目录、转换锚点和必要锚点，只有分类判断需要时才展开相关段落。多个候选可以由子 Agent 并行读取。
3. 分类必须包含 role、tags、summary、applicable_modules、versions、confidence、rationale，并显式写入 `"source_backed": false` 与 `"provenance": "model_inference"`。这些字段属于资料整理推断，不是材料事实；正式分析仍回到 Markdown 来源锚点。
4. 分类结果准备后按 source_path 逐条串行执行，禁止并发写 catalog：

```text
<preflight.python_executable> -m tooling.pangea_cli library classify --source-path "<catalog.source_path>" --file <classification.json>
```

只报告实际写入成功的分类；失败项保留为未分类。
