---
name: dfx-analysis
description: 模块语义冻结后的六维 DFX 复核方法，只输出 assessment 和 finding。
---

# 六维 DFX

每维输出一个 summary 和零到多条 findings，不设置数量配额。Finding 类型是 strength、risk、
limitation、gap 或 recommendation。相同因果链不要拆成六个风险；一条 finding 可关联多个语义对象。

- 功能与状态：入口闭环、状态迁移、分支一致性、功能边界。
- 资源与规格：所有权、配额、队列、池、计数、过载回落与释放。
- 性能与压力：同步慢路径、复杂度、队列深度、锁竞争、压力解除后的恢复。
- 并发与异常：执行上下文、共享状态、回调/销毁时序、超时取消和异常传播。
- 升级与兼容：配置、API/ABI、条件编译、持久状态、协议与回滚边界。
- 可靠性与一致性：错误处理、重复执行、恢复、状态/数据一致性和可观测性。

NVIDIA/DOCA/BlueField/ConnectX 方法只在依赖、符号、配置或硬件信息匹配时使用；可借鉴升级检查点、
分层诊断、遥测、队列和硬件资源边界，但不得假设目标使用相关产品。
