# 条件知识加载与来源边界

机器可读来源登记：[`sources.json`](sources.json)。每个 `source_id` 只提取方法论，不复制外部提示词、安装步骤或厂商命令；加载前必须有下表定义的匹配证据。

## 加载原则

外部 skill 先被提炼为“代码信号、追问清单、故障模式、验证方法和适用边界”，再由能力包按证据加载。禁止原样复制角色提示、输出人格、自动安装指令或厂商专用命令。

| 信号 | 加载方法 | 适用边界 |
|---|---|---|
| C/C++ 分配、错误出口、析构、回调、锁、原子 | 共享 C/C++ 取证底座 | 所有 C/C++ 组件；需要源码证据。来源：`source_id:general-cpp-concurrency`、`source_id:general-cpp-ownership` |
| queue/pool/refcount/free/limit/配额 | 资源与规格包 | 区分初始化预留与运行时动态申请；验证回落恢复而非只验证越限拒绝 |
| QD、批处理、锁竞争、吞吐/时延曲线 | 性能与压力包 | 需要外部性能基线或规格，不能仅凭慢路径符号断言性能缺陷。来源：`source_id:general-dpdk-performance` |
| 超时、重试、故障注入、重置、恢复 | 可靠性与一致性包 | 用稳态、爆炸半径和恢复判据定义测试，不假定具体注入工具存在。来源：`source_id:general-chaos-resilience` |
| 异步回调、锁、原子、取消、销毁 | 并发与异常包 | 需要并发上下文和共享状态证据。来源：`source_id:general-cpp-concurrency` |
| RDMA verbs、DPDK、协议 PDU/队列 | 对应协议/厂商参考 | 仅在源码、构建配置或任务材料显示匹配时加载。来源：`source_id:general-rdma-core`、`source_id:general-dpdk-performance`、`source_id:general-linux-device-driver` |
| DOCA 升级、调试、遥测、硬件安全、性能材料 | 升级/可靠性/性能方法 | 迁移版本矩阵、检查点、遥测、队列与硬件安全方法；不假定产品使用 DOCA。来源：`source_id:nvidia-doca-rdma`、`source_id:nvidia-doca-upgrade`、`source_id:nvidia-doca-debug`、`source_id:nvidia-doca-hardware-safety`、`source_id:nvidia-doca-telemetry`、`source_id:nvidia-doca-flow-perf` |
| Intel NIC/ICE/i40e/iavf 资料 | 条件参考 | 已核验：没有同等级 Intel 官方 Agent skill。仅在仓库、驱动名、PCI 设备标识或任务材料匹配时使用官方 driver/DPDK 文档；文档不是 skill。来源：`source_id:intel-agent-skill-negative-finding`、`source_id:intel-ice-driver-docs`、`source_id:intel-i40e-driver-docs`、`source_id:intel-iavf-driver-docs`、`source_id:intel-dpdk-net-drivers` |

## 来源记录

每次使用外部材料应在风险卡或 Run 账本记录 `source`、`version_or_commit`、`license_or_access`、`applicability_evidence` 和 `limitations`。缺少版本或许可证信息时标未知，不阻塞内部方法论使用，但不得声称“已验证厂商知识”。

## 可迁移方法论清单

- C/C++ 并发和所有权：共享状态、锁/原子、条件等待、回调、取消、shutdown、资源守恒、错误出口与 C/C++ 生命周期边界。
- 性能和韧性：热路径分配/拷贝、锁竞争、批处理、队列深度、稳态、爆炸半径、恢复时间与压力解除后的业务恢复。
- device-driver/RDMA/DPDK：描述符/队列、DMA 生命周期、完成路径、背压、重置、链路事件与协议状态机。
- NVIDIA DOCA：只迁移升级检查点、分层诊断、遥测判据、硬件资源安全边界、队列和性能复现条件；绝不假定产品使用 DOCA。
- Intel：只将 ICE/E810/i40e/iavf 的官方驱动和 DPDK 文档作为条件资料。没有已核验的同等级官方 Agent skill。
