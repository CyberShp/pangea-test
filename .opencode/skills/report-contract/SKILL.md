---
name: report-contract
description: PANGEA-TEST report.md 与离线 report.html 交付契约
---

# 报告契约

每个 Run 生成内容一致的 `report.md` 和离线单文件 `report.html`，章节顺序固定：

1. 任务契约、输入与覆盖边界。
2. 模块代码地图。
3. 入口与关键业务流程。
4. 异常分支及进入方式。
5. 全量风险账本。
6. 测试场景。
7. 测试用例。
8. 风险与用例覆盖映射。
9. 代码证据附录。
10. 未闭环项与下一步建议。

正文先给测试人员能理解的外部行为、触发、观测和恢复，再给灰盒说明与代码证据。代码地图、流程和分支可出现函数、变量与行号，但必须紧邻黑盒解释。

HTML 完全离线、只读。测试解释默认展开，源码证据默认折叠；支持全文搜索、按严重度/DFX/转译状态筛选、风险与用例双向跳转。Mermaid 图应附相同含义的文字流程。

## 审计交付绑定

报告渲染前，主 Agent 将完整报告模型固定写到 `pangea-data/runs/<run-id>/internal/report-model.json`，并自行计算该文件的 SHA-256。独立审计的 `audit_opinion` 必须使用 `schema_version: "2.0"`，且只审固定 Run 相对路径 `audited_artifact: "internal/report-model.json"` 与同一文件的 `audited_sha256`。

审计意见必须包含 `artifact_type`、`schema_version`、`audited_artifact`、`audited_sha256`、`verdict`、`checks` 和 `required_actions`。`checks` 固定为可追溯性、黑盒可执行性、覆盖、格式合规四维；不得用顶层 `findings` 或 `coverage_gaps` 替代。auditor 只核对主 Agent 提供的模型绑定并审阅内容，不负责计算哈希。只有绑定未变、审计 `PASS` 且 `required_actions` 为空，才允许从该固定模型渲染最终报告。
