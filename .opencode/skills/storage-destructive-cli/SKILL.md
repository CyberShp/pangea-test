---
name: storage-destructive-cli
description: Analyze source-backed destructive storage CLI paths such as format, sanitize, namespace delete, firmware activation, reset, force or confirmation handling, target scope, exclusivity, rescan, and post-command state; never execute those operations.
---

# Destructive storage CLI analysis

Use this only as a conditional domain method for `analysis-worker`; it is not a persona or an agent. Do not delegate, alter source, claim completion, or recommend running a destructive operation on a real device.

Read [references/analysis-checklist.md](references/analysis-checklist.md) in full only when an assigned obligation reaches destructive dispatch, target selection, force/exclusive/confirmation, admin submission, post-success update, reset, or safety-safe black-box validation.

1. Bind every fact to inventory IDs, obligation IDs, and allowed source ranges. Trace command dispatch → selected target scope → capability/confirmation/force/exclusive checks → submission → success/failure handling → rescan or state update.
2. Distinguish declaration, validation, submission, and device-side completion. Identify irreversibility and missing guardrails only when source supports the chain.
3. Derive black-box controls/oracles using mocks, disposable namespaces, emulators, or explicitly safe test fixtures. Never prescribe format, sanitize, namespace deletion, firmware activation, or reset against production or unknown hardware.
4. Mark device semantics, privilege behavior, and irreversible effects `need_verify` unless exact source facts establish them.
5. Return one disposition per obligation. N/A needs narrow scope plus source counterevidence; High, Critical, P0, and P1 require exact facts and a safe validation boundary.

Runtime 将本方法与 checklist 嵌入冻结 context；方法加载记录不是源码证据。只产出分配给当前 slice 的语义对象、risk candidate 与 gap，不输出可执行的破坏性命令配方。
