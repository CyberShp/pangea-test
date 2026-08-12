---
name: storage-nvmeof
description: Analyze NVMe-oF source ranges involving controller, subsystem, namespace, qpair, RDMA or TCP connect/disconnect, discovery, keepalive, reset, transport resources, or transport error states; distinguish initiator and target code.
---

# NVMe-oF analysis

Use this only as a conditional domain method for `analysis-worker`; it is not a persona or an agent. Do not delegate, alter source, or claim completion.

Read [references/analysis-checklist.md](references/analysis-checklist.md) in full only when an assigned obligation spans initiator/target boundary, controller/subsystem/ns/qpair state, TCP/RDMA transport resources, outstanding work, reset/reconnect, or an externally observable recovery flow.

1. Bind facts to inventory IDs, obligation IDs, and source ranges. Identify whether each path is initiator or target before reasoning across controller, subsystem, namespace, and qpair boundaries.
2. Trace connect/disconnect, discovery, keepalive, or reset through RDMA/TCP callbacks, qpair/controller states, transport resource allocation, and terminal error cleanup.
3. Record state guards, ownership handoffs, retry/reconnect decisions, and externally observable completion/failure. Do not turn protocol names into behavior without the matched source path.
4. Derive black-box controls and oracles from observable connect, discovery, I/O, timeout, reset, or reconnect outcomes. Flag unproven peer behavior and hardware conditions `need_verify`.
5. Give every obligation one disposition. N/A needs a narrow boundary and source counterevidence; High, Critical, P0, and P1 need exact source facts.

Runtime 将本方法与 checklist 嵌入冻结 context；方法加载记录不能替代分析。优先完成当前 slice 的语义对象、risk candidate 与 gap，不输出协议背景或重复卡片。
