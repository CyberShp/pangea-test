---
description: 初始化 PANGEA-TEST 个人工作空间并探测只读分析能力
agent: pangea-test
---

用户参数：`$ARGUMENTS`

依次运行以下真实入口，并以各命令的实际 JSON 输出为准，不得把预期结果描述成已经执行：

```sh
python3 -m tooling.pangea_cli data session-prepare $ARGUMENTS
python3 -m tooling.pangea_cli library refresh-hints
python3 -m tooling.pangea_cli tool probe
python3 -m tooling.pangea_cli index all
```

`data session-prepare` 的输出已经包含 `incomplete_runs`：直接呈现该字段，不再重复运行 `data incomplete-runs`。`index all` 只为受管影子仓建立或更新 GitNexus 索引；其耗时、磁盘占用、增量能力、工具缺失和单仓失败均以实际输出为准。

完成 `library refresh-hints` 后，按以下规则自主整理本次资料：

1. 先检查 `session-prepare` 输出中的 `inbox.added`、`inbox.changed` 和 catalog。只有 `added + changed > 0` 时才进入分类；两者都为 `0` 时不得读取全部 Markdown 或重分类。
2. 从 catalog 关联本次新增或内容已变化的记录，只处理存在 `markdown_path`、转换可读且没有有效 `semantic_classification` 的记录。`classification_sha256` 与当前 `sha256` 一致且已有语义分类的记录跳过；`refresh-hints` 已生成的同哈希继承分类也跳过，不重复读取 Markdown。
3. 对每个候选记录读取其 `markdown_path`；先读标题、目录和转换锚点，只有分类判断需要时才展开相关段落或必要锚点，不把整份文档无条件塞入上下文。多个文档可以由子 Agent 并行读取和形成分类 JSON。
4. 每份分类必须包含 schema 要求的 `role`、`tags`、`summary`、`applicable_modules`、`versions`、`confidence`、`rationale`，并显式设置 `"source_backed": false`、`"provenance": "model_inference"`。这些字段是资料整理推断，不是材料事实；摘要和理由不得被后续报告当作源材料证据，事实仍须回到 Markdown 锚点引用。
5. 分类 JSON 准备完成后，由主 Agent 按 `source_path` 逐条串行执行，禁止并发写 catalog：

```sh
python3 -m tooling.pangea_cli library classify --source-path "<catalog.source_path>" --json '<classification-json>'
```

只报告命令实际成功写入的分类；失败项保留为未分类并说明原因。不得实现或调用固定的 LLM 分类算法，不移动 `inbox`、archive、Markdown 或原始文件。

1. 显示 `[梳理中 (._.)]`，检查 `pangea-data/` 的目录约定、可读代码仓、未完成 Run 和遗留临时目录；不得创建、移动或删除用户源码。
2. `library refresh-hints` 只为已导入文档补充路径角色提示或同哈希继承标记，不移动 `inbox`、归档或用户原始文件。
3. `tool probe` 安全探测 Git、GitNexus、MR MCP、文档转换和可选静态工具的可用性及版本；只检测，不安装。MR MCP 是运行载体能力，按输出标记为执行时确认。
4. `session-prepare` 才负责检查已登记仓库并仅在运行层安全条件满足时尝试 `git pull --ff-only`；说明实际 pull 结果或未执行原因，不自行补充 pull。
5. 输出能力清单、工具缺口、仓库状态、索引结果和未完成 Run；没有实际数据时明确说明，不猜测。
