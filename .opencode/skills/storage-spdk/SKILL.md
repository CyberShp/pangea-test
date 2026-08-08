---
name: storage-spdk
description: Analyze SPDK storage-source ranges when inventory or obligations mention reactor, poller, SPDK thread messages, JSON-RPC/subsystem registration, bdev, io_channel, NVMf transport/qpair, async callback, DMA, mempool, reset, or error unwind.
---

# SPDK storage analysis

Use this only as a conditional domain method for `analysis-worker`; it is not a persona or an agent. Do not delegate, alter source, or claim completion.

Read [references/analysis-checklist.md](references/analysis-checklist.md) in full only when an assigned obligation covers registration/dispatch, reactor affinity, bdev or io_channel lifetime, DMA/mempool, async unwind, reset, or a source-backed black-box flow. Keep this Skill body for simple routing-only obligations.

1. Bind every statement to assigned inventory IDs, obligation IDs, and allowed source ranges. Extract exact facts first: symbol, path, lines, guard, ownership transition, and callback/context.
2. Trace external trigger → JSON-RPC/subsystem or transport entry → internal message/callback chain → bdev/io_channel or qpair operation → observable result. Distinguish registration from execution.
3. Model reactor/poller affinity, `spdk_thread_send_msg` handoff, async completion, DMA/mempool lifetime, and init/fini/reset/error-unwind ownership before inferring a race or leak.
4. Derive black-box controls and oracles from those facts: request, observable completion/status/log/resource counter, and recovery expectation. Mark uncertain transport behavior `need_verify`.
5. For each obligation return one disposition. An N/A needs a narrow boundary and source counterevidence. A High, Critical, P0, or P1 claim needs an exact source fact; otherwise use `need_verify`.

Runtime records the triggered ranges, applicable obligations, and this Skill content hash in its receipt. Loading this text is never evidence. Under 4096 tokens, complete assigned obligations and concise `analysis_fragment` contributions; omit background history and repeated risk cards.
