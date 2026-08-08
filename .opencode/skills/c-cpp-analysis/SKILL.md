---
name: c-cpp-analysis
description: PANGEA-TEST C/C++ 共享只读源码分析底座
---

# C/C++ 共享分析底座

确定性运行时先建立独立 inventory 与 obligation ledger；同一 `analysis-worker` 只在 immutable context pack 的允许 ranges 内使用本底座，并加载适用 capability pack。它沿入口、调用方、错误路径、异步回调和销毁/恢复路径追踪，重点抽取：

- 外部入口、协议事件、CLI/REST 和状态机迁移。
- 资源所有权、申请/释放、引用计数、队列额度、预留与动态内存。
- 错误处理、清理、重试、取消、超时、初始化、卸载和复位。
- 共享状态、锁、原子、回调上下文和 C/C++ 边界。

不把符号名称直接当测试步骤。先说明测试人员可制造的外部条件、可观测的行为和恢复标准；再在灰盒说明或证据附录给出函数、变量和行号。

可根据源码证据加载 DPDK、RDMA、DOCA、mlx 或 Intel 驱动的参考方法论；厂商专用 API、寄存器和命令只能在证据匹配时使用，不能伪装为通用事实。每次加载都必须留下绑定 obligation、版本与内容哈希的 receipt；分析结果进入严格 `analysis_fragment`，不得用风险卡或摘要替代完整模型贡献。
