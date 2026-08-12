---
name: storage-iscsi
description: iSCSI Target 领域语义方法，仅在 iSCSI 路径、符号或配置证据命中时使用。
---

# iSCSI Target 分析方法

区分应用启动壳层与真正协议实现。优先识别配置/RPC 入口、Portal/Initiator/Target Node、连接与
Session、Login、PDU 收发、SCSI Task、超时、登出、异常断链和资源回收的边界。

当前范围没有协议实现时，明确说明哪些结论只覆盖启动入口，哪些需要 `event_iscsi` 或 `lib/iscsi`
等关联源码。不得用协议常识补齐未提供源码，也不得把“可能存在”直接写成风险。
