---
name: module-full-analysis
description: 对存储模块执行可恢复、可审计、可受控回挖的全量分析
---

加载 `core/shared/溯源铁律.md`、`core/shared/铁律总纲.md`、`core/shared/八问纲领.md`、`core/scenarios/模块全量分析.md`、`registry/scenarios.json`。

直接专家模式不加载本 Skill。托管模式必须以 task envelope、manifest、Schema 为机器事实来源：

- 证据先保存 JSON，再经 `python runtime/managed.py put-artifact` 入库；
- 审计经 `python runtime/runctl.py apply-audit` 入库；
- FAIL/CONCERNS 经 `python runtime/managed.py plan-rework` 生成整改计划；
- 只自动执行 rework plan 的 `next_tasks`，其他动作进入 `manual_actions`；
- 达到审计轮数上限后停止自动回挖并交付未决项。
