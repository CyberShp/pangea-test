---
name: risk-card
description: PANGEA-TEST 内部 DFX 风险卡协议
---

# 风险卡协议

通用 `analysis-worker` 只能在严格 `analysis_fragment` 的风险贡献字段中提交风险卡，不生成最终报告、测试用例集或插桩代码。风险卡不是 fragment 的全部内容，不能替代 assigned obligations 的 dispositions、事实和 Flow/Branch/State/Resource/Concurrency/Error Chain/Scenario contributions。新建卡必须符合 `schemas/risk-card.schema.json`，使用扁平 canonical 结构。

风险卡首先服务于测试复现，不服务于安全攻击叙事。最终必须能回答四件事：**什么条件可以复现、系统出现什么结果、测试侧如何观察、改变什么条件可以排除。**“攻击者能否利用”“TSan 是否报错”都不能单独构成可执行风险卡。

```yaml
artifact_type: risk_card
schema_version: 1.0
risk_id: R-RESOURCE-001
title: 规格回落后业务能力未恢复
dfx: [资源与规格, 性能与压力]
severity: High
confidence: medium
trigger: 并发请求超过规格上限后逐步降回规格内
propagation: 可申请资源持续减少，后续请求无法重新获得业务能力
external_impact: 新业务建立失败或 IOPS 长时间不能恢复
observation: 通过 CLI、协议报文、日志、指标或后续业务确认压力解除后资源余量与业务能力仍未恢复
recovery: 恢复方式：停止压力并等待自动恢复；排除条件：相同环境下不超过规格上限时资源余量和业务能力始终正常
translation_status: Graybox-ready
test_explanation: 复现条件：先超过规格上限再回落；风险表现：回落后业务能力仍不足；外部观测：新业务建立和 IOPS 未恢复；排除条件：不经过超规格阶段时现象不出现。
source_scope:
  repository: driver
  ref: MR-123 或 commit
inference: 计数路径可能未在超规格分支纳入回收；需用“超规格后回落”与“始终规格内”两组条件对照证伪或确认。
instrumentation_request:
  requested_point: 接收就绪状态切换的可控时窗
  control_semantics: 允许将就绪动作延后指定时长后恢复正常行为
  parameters: 延后 0 至 2 秒，可重复开关
  observation: 记录开关生效时间、报文到达时间和连接/业务状态
  recovery_requirement: 关闭控制后不得残留连接、资源或业务状态
evidence:
  - location: driver/resource.c:42
    observation: 超规格分支与正常回收路径的计数处理不一致
coverage_gap: null
related_risk_ids: []
status: open
```

必填字段是 `artifact_type`、`schema_version`、`risk_id`、`title`、`dfx`、`severity`、`confidence`、`trigger`、`propagation`、`external_impact`、`observation`、`recovery`、`translation_status`、`evidence`。其中 `artifact_type` 固定为 `risk_card`，`schema_version` 固定为字符串 `1.0`。`dfx` 至少包含一个且只允许 `功能与状态`、`资源与规格`、`性能与压力`、`并发与异常`、`升级与兼容`、`可靠性与一致性`；一张卡可包含多个不重复维度。`test_explanation`、`source_scope`、`inference`、`instrumentation_request`、`coverage_gap`、`related_risk_ids`、`status` 为 canonical 可选字段。

`evidence` 至少包含一项；schema 强制每项都是仅含 `location` 和 `observation` 的对象，两个字段均为必填非空字符串。`location` 必须给出文件行号、MR hunk 或材料锚点，`observation` 必须记录该位置可复核的事实。`instrumentation_request` 为 `null` 或完整的控制语义请求，绝不能包含插桩实现代码、函数级 mock/stub 代码或白盒测试代码。

字段解释如下：

```text
artifact_type: 固定为 risk_card
schema_version: 固定为字符串 1.0
risk_id: run 内稳定的简短标识
title: 面向测试人员的风险标题；描述系统失效，不以“攻击者可...”为标题
dfx: 从功能与状态 | 资源与规格 | 性能与压力 | 并发与异常 | 升级与兼容 | 可靠性与一致性中选择一个或多个不重复维度
evidence: 非空数组；每项必含非空 location（文件:行号、MR hunk 或材料锚点）和 observation（该位置的观察事实）
inference: 因果推断及证伪路径；没有则为 null
trigger: 测试侧可构造的复现条件与环境；禁止只写函数、变量、源码竞态或攻击者能力
propagation: 条件进入系统后状态、资源或协议行为如何传播到问题
external_impact: 业务、数据、连接、性能或恢复后果
severity: Low | Medium | High | Critical
confidence: high | medium | low，并说明理由
observation: 至少一个外部 PASS/FAIL 判据，例如 CLI、协议报文、返回码、连接/业务状态、日志、指标、数据结果或恢复结果；Sanitizer 只能辅助
recovery: 必须同时写恢复方式和排除条件；排除条件用于验证关键条件改变后现象消失
translation_status: Blackbox-ready | Graybox-ready | Developer-confirm
test_explanation: 建议按“复现条件 -> 风险表现 -> 外部观测 -> 排除条件”写清测试闭环
instrumentation_request: null，或插桩点、控制语义、参数、观测和恢复要求
coverage_gap: null，或缺失仓库/版本/材料及下一步建议
related_risk_ids: 同 run 内的关联 risk_id
status: open | covered | confirmed | dismissed
```

严重度参考：数据不一致/丢失、业务归零或断连、需修卡或无法在线恢复为 `Critical`；核心功能受损、显著性能退化、持续泄漏或恢复代价高为 `High`；非核心功能受损且有规避方式为 `Medium`；轻微异常或可观测性问题为 `Low`。严重度不等于可信度。

## 可复现性门禁

- `Blackbox-ready`：必须存在测试侧可直接构造的 `trigger`、外部 `observation` 和明确排除条件；不得依赖函数名、变量、源码行或 Sanitizer 才能判定。
- `Graybox-ready`：允许日志、指标、诊断命令、故障注入或插桩协助进入条件，但仍必须有业务/协议/连接/数据/性能等外部结果作为最终 oracle。TSan、ASan、UBSan、Valgrind 等只能作为辅助证据。
- `Developer-confirm`：只有代码疑点，尚不能给出可靠复现条件或外部观测时使用；交付“还缺什么条件/证据”，不得伪造可执行用例。
- 风险卡不得以“攻击者”“恶意用户”“利用漏洞”为主语描述测试结论。安全风险也应改写为“当输入/状态/权限/报文条件 X 出现时，系统产生结果 Z；改为条件 Y 后结果 Z 消失”。
- 如果只能用 TSan/ASan 等发现内部异常，但无法给出系统级构造与外部后果，状态不得高于 `Developer-confirm`。

转译规则：`Blackbox-ready` 只使用主机、协议、CLI、日志、指标、诊断和故障注入等外部可操作语言；`Graybox-ready` 可少量说明所需控制时窗或状态条件，但不得把函数名、变量名当作测试步骤；`Developer-confirm` 仅交付待确认风险与所需证据，不得生成可执行用例。插桩仅提出控制语义，不生成插桩代码。
