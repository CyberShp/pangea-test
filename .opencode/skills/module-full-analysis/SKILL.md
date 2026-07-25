---
name: module-full-analysis
description: 对存储模块执行项目化、可恢复、可审计、可受控回挖的全量分析
---

加载 `project-workspace`、`test-asset-retrieval`、`core/shared/溯源铁律.md`、`core/shared/铁律总纲.md`、`core/shared/八问纲领.md`、`core/scenarios/模块全量分析.md`、`registry/scenarios.json` 与 `registry/workflows.json`。

托管模式必须以项目配置、task envelope、manifest、`inputs.lock.json` 和 Schema 为机器事实来源：

- 源码从当前项目读取，严格只读；
- 设计、需求、覆盖率、已有用例从 inputs Catalog 锁定；
- 团队资产通过 asset search 检索，不得遍历全部资产；
- 中间件写入 `workspace/<project>/module-full-analysis/<run>/`；
- 正式交付发布到 `outputs/<project>/module-full-analysis/<run>/`；
- Evidence、审计和受控回挖遵循 `runctl.py` 与 `managed.py`；
- 达到审计轮数上限后停止自动回挖并交付未决项。
