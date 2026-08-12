---
name: risk-card
description: 风险候选判定、canonical risk 与测试可行性分离方法。
---

# 风险判定

先判断候选因果链是否由证据支持，再独立判断测试可行性。Canonical risk 回答：构造什么条件、
如何传播、产生什么外部影响、如何观察、如何恢复或排除。严重度与可信度分开。

`blackbox_ready` 有外部入口、触发和判据；`graybox_ready` 需要日志、指标、诊断或控制时序；
`blocked` 表示风险成立但缺少环境、接口、观测或关联证据。Blocked 风险必须保留 missing_evidence，
不能因为没有测试而 dismissed。测试只能从 confirmed risk 或 behavior 派生。
