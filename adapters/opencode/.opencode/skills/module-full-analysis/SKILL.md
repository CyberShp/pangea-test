---
name: module-full-analysis
description: 对存储模块执行主干、分支、状态、资源、异常传播、风险扫描、SFMEA 和黑盒用例全量分析
---

# 模块全量分析

本 Skill 是 OpenCode 适配层；平台无关的唯一事实来源仍在 `core/`，不得在此复制或改写业务方法论。

执行时必须按顺序加载：

1. `core/shared/溯源铁律.md`
2. `core/shared/铁律总纲.md`
3. `core/shared/八问纲领.md`
4. `core/scenarios/模块全量分析.md`
5. `core/shared/证据包schema.md`
6. `registry/scenarios.json` 中的 `module-full-analysis`

## 运行约束

- 深度任务必须先由 `runtime/runctl.py init` 创建 task envelope 与 manifest。
- 下游 Task 必须消费完整 task envelope，不得只传一段临时自然语言。
- 每份 `code_evidence` 必须符合 `schemas/code-evidence.schema.json`，并通过 `runctl.py put-artifact` 入库。
- 只消费 manifest 中登记的任务；恢复时跳过 `complete`。
- 报告必须经过 `auditor`，并使用 `runctl.py apply-audit` 记录裁定。
- `audit.max_rounds` 用尽后停止自动回挖，带未决项交付。
