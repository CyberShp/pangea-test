# 统一风险卡契约

> 六个内部 DFX 子 Agent 只提交风险卡，不各自输出完整报告或独立用例集。主 Agent 对风险卡去重、合并、定级并统一转译。

```yaml
artifact_type: risk_card
schema_version: 1.0
risk_id: <run 内唯一 ID>
title: <面向外部后果的风险标题>
dfx: [资源与规格, 性能与压力] # 从六个固定中文维度中选择一个或多个
severity: Critical | High | Medium | Low
confidence: high | medium | low
trigger: <外部条件或内部状态组合>
propagation: <从变化到故障的因果链；不确定处标推测>
external_impact: <业务、数据、性能、连接或恢复后果>
observation: <报文/CLI/告警/日志/性能/错误码/数据比对>
recovery: <压力解除、重试、断链、重启后的期望及代价>
translation_status: Blackbox-ready | Graybox-ready | Developer-confirm
evidence:
  - location: <仓库相对文件:行号或 MR hunk>
    observation: <可核查代码事实>
test_explanation: <黑盒优先的测试语言说明>
source_scope: null # 或 {repository: <只读仓或临时 MR 副本>, ref: <commit/MR/分支>}
inference: null # 或因果推断及证伪路径
instrumentation_request: null # 或完整控制语义需求，绝不含代码
coverage_gap: null # 或未闭环原因及下一步建议
related_risk_ids: []
status: open | covered | confirmed | dismissed
```

字段、嵌套和枚举以 `schemas/risk-card.schema.json` 为唯一机器契约。风险卡是扁平对象：不得使用 `id`、`translation`、`impact`、`instrumentation_need`，不得使用 `causal_chain`、`test_translation` 或 `coverage` 嵌套对象。运行时为历史报告输入保留的归一化别名不属于新风险卡协议。

`dfx` 只允许 `功能与状态`、`资源与规格`、`性能与压力`、`并发与异常`、`升级与兼容`、`可靠性与一致性`。一张风险卡可归入多个不重复维度；不得提交英文包名、子 Agent 名或临时自定义维度。

必填字段为 `artifact_type`、`schema_version`、`risk_id`、`title`、`dfx`、`severity`、`confidence`、`trigger`、`propagation`、`external_impact`、`observation`、`recovery`、`translation_status`、`evidence`。`artifact_type` 固定为 `risk_card`，`schema_version` 固定为字符串 `1.0`；二者共同标识 canonical 风险卡及其契约版本。`source_scope` 必须同时包含 `repository` 与 `ref`；`instrumentation_request` 必须同时包含 `requested_point`、`control_semantics`、`parameters`、`observation`、`recovery_requirement`。不需要时两者均显式为 `null`。

`test_explanation` 用黑盒语言说明测试切入；只有确有必要时，才用少量灰盒术语描述可控的状态时窗或诊断条件。`instrumentation_request` 只请求插桩点与控制语义，不能生成插桩代码，也不能将函数名、变量名、Mock/Stub 或白盒实现细节写成测试操作。

## 定级与可信度

- `Critical`：数据不一致/丢失、业务归零或断连、需修卡/更换硬件、无法在线恢复或大范围扩散。
- `High`：核心功能受损、显著性能退化、持续资源泄漏、恢复代价高。
- `Medium`：非核心功能受损、影响范围有限且存在规避或恢复方式。
- `Low`：轻微异常、可观测性或易用性问题。

严重度衡量外部后果，可信度衡量证据闭环程度，二者不得混为一谈。`inference` 非空时必须写明依据与证伪方法；缺少验证条件则设置 `coverage_gap`，并将 `translation_status` 设为 `Developer-confirm`，不得生成可执行用例。
