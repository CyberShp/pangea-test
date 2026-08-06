---
name: analysis-depth-contract
description: PANGEA-TEST 完整型源码分析模型与 Coverage disposition 契约
---

# 完整型分析模型

`complete` 模块分析必须在报告前写入 `pangea-data/runs/<run-id>/internal/analysis-model.json`，并通过：

```text
python runtime/runctl.py stage-analysis-v2 --run-id <run-id> --file <analysis-model.json>
```

固定模型必须包含输入材料消费、入口、完整 Flow Card、分支、状态、资源、并发、错误传播、六维适用性、场景候选、SFMEA、测试场景、测试流程、用例、追溯、Coverage disposition、深度限制和未闭环项。

每个入口、Flow、Branch、State、Resource、Concurrency、Error Chain 和 Scenario Candidate 都必须有 disposition。允许 `analyzed`、`covered_by_other`、`not_applicable`、`blocked`、`need_verify`、`truncated`；完整型不得以 `truncated` 通过。所有 `blocked`/`need_verify`/`truncated` 项必须逐项进入 `unresolved`，写明原因、影响和最小下一步。

风险卡只是风险视图，不能替代开发实现模型。报告模型必须绑定固定分析模型的路径和 SHA-256，不能手工省略分析链。


## 独立 Coverage Judge

固定分析模型和确定性报告投影完成后，运行时必须生成 `internal/coverage-judge.json`。Judge 独立核对入口与 Flow、全部 disposition、场景候选、SFMEA、测试流程、测试用例、风险映射、Oracle、追溯和报告投影。Judge 工件绑定 analysis-model、report-model 和 risk-ledger 的 SHA-256；任何一个变化都会使 Judge 过期。只有 Judge `PASS` 才允许提交 auditor 或完成 Run。
