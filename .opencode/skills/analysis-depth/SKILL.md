---
name: analysis-depth
description: 完整模块语义深度方法，不定义 Runtime 验收规则。
---

# 分析深度

语义片段围绕 Behavior、Flow、Branch、State、Resource、Concurrency、Error Chain、Risk Candidate
和 Gap 展开。不要用风险摘要替代实现模型，也不要为了填满类型而制造对象。

Flow 应给出触发、顺序步骤和外部结果；Branch 给出条件及不同结果；State 给出事件前后状态；
Resource 给出申请、所有权、释放和异常回滚；Concurrency 给出上下文、共享对象、协调与时序；
Error Chain 给出触发、传播、处理、恢复和影响。证据不足时缩小结论或记录 Gap。
