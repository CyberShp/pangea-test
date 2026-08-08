---
name: storage-resource-recovery
description: Analyze matched storage-source ranges for allocation, ownership, release or unwind, refcount, pool, queue, fd, socket, timer, poller, reset, reconnect, failover, repeated fault, long-run stability, or conservation invariants.
---

# Resource and recovery analysis

Use this only as a conditional domain method for `analysis-worker`; it is not a persona or an agent. Do not delegate, alter source, or claim completion.

Read [references/analysis-checklist.md](references/analysis-checklist.md) in full only when an assigned obligation crosses acquire/transfer/release, failure unwind, reference or queue accounting, repeated faults, long-run stability, reconnect, or failover.

1. Bind facts to inventory IDs, obligation IDs, and allowed ranges. Build allocation → owner → transfer/reference → release/unwind chains for each relevant resource.
2. Check refcount, pool/queue watermarks, fd/socket/timer/poller lifecycle, and failure labels across repeated faults, reset, reconnect, and failover.
3. Do not report a leak because an allocation appears. Require a source-proven missing release, violated ownership edge, or conservation mismatch; otherwise use `need_verify`.
4. Derive black-box controls/oracles from fault injection, retry count, long-run resource counters, queue/pool balance, and recovery completion as source permits.
5. Make one disposition per obligation. N/A needs narrow scope and source counterevidence. High, Critical, P0, and P1 require exact facts.

Runtime records trigger scope, applicable obligations, and content hash in its receipt. “Loaded” never substitutes for evidence. In 4096 tokens, finish assigned `analysis_fragment` contributions and do not repeat generic resource advice.
