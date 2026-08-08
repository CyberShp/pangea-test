# 历史迭代记录（非活动运行契约）：迭代 004 / Workspace & Asset Platform

> 本文仅保留 v1 工作区迁移历史；当前活动路径、角色和命令以 `README.md`、`registry/` 与 `runtime/` 为准。

> 目标：把 PANGEA 从“需要用户记路径和命令的托管分析器”升级为“根目录预设空间、自动识别项目与资料、按需检索测试资产的工作台”。

## 计划与落地映射

| # | 计划 | 本迭代落地 | 验收方式 |
|---|---|---|---|
| 1 | 六区空间模型 | `source/ inputs/ workspace/ outputs/ projects/ assets/` | 目录契约与 `.gitignore` |
| 2 | Project Manager | `python -m tooling.pangea_cli project ...` | 项目创建不改源码目录 |
| 3 | Input Catalog | `input scan/add/list` | 自动角色分类、hash、版本提示 |
| 4 | Asset Registry | `asset index/search/show` | profile/tag/type 检索，不全量遍历 |
| 5 | 自然语言托管升格 | PANGEA-TEST 主 Agent 与族 Agent 自动判定正式任务 | 不要求用户记 `/analyze-module` |
| 6 | 工作流隔离 | `workspace/<project>/<workflow>/<run>` 与 `outputs/...` | `inputs.lock.json`、`artifacts.json`、`latest.json` |
| 7 | 源码命令变成 Agent 能力 | CLI → Skill → Agent 三层 | `project-workspace`、`test-asset-retrieval` Skills |
| 8 | Windows/CI 验收 | 零第三方依赖测试 | 单元测试 + py_compile |

## 空间红线

- `source/` 只存被分析源码，PANGEA 不得在源码仓库内部写入任何文件。
- `inputs/` 存用户提供的设计、需求、覆盖率、已有用例、日志和抓包。
- `workspace/` 只存中间工件、状态、证据、审计和草稿。
- `outputs/` 只存正式交付物。
- `assets/` 是团队长期测试资产，不等于本次用户输入，也不等于 `core/` 方法论。

## 命令的定位

命令保留给调试和自动化；正常用户只表达意图。Agent 通过 Skill 选择并调用确定性 CLI。
