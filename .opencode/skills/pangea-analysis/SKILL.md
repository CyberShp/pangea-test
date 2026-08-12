---
name: pangea-analysis
description: PANGEA 分析方法索引；具体阶段按 Runtime 注入的 Skills 执行。
---

# PANGEA analysis method index

从外向内阅读源码：先找可见入口、输入和完成结果，再还原调用步骤、关键判断、状态变化、
资源所有权、并发时序、错误传播和恢复。函数名和语法节点用于导航，不直接等于业务结论。

每个结论连接到当前逐行源码中的最小充分证据。证据不足时缩小结论并记录 gap，不用协议常识
补齐源码没有表达的行为。

Slice 阶段只生成语义对象、risk candidate 与 gap。模块语义合成后才执行一次 DFX，再独立判定
风险、SFMEA 和测试设计。风险成立与测试可执行性分开，blocked 风险不得因无用例而删除。

Runtime 会把当前阶段和证据命中的具体 Skills 作为冻结 context 一并提供。Skills 是方法，不是
第二套流程、Schema 或完成门禁。
