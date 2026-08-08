# SPDK cross-file checklist

## Boundary and trigger

Use only with source evidence for SPDK registration, reactor/thread/poller code, bdev or `io_channel`, NVMf transport/qpair, DMA/mempool, or lifecycle cleanup.
Do not infer that a generic callback is SPDK-affine from its name.
Route NVMf protocol-state detail to `storage-nvmeof`; retain only the SPDK integration edge here.
Do not use this checklist merely because a repository links SPDK.

## Evidence order

1. Locate the external entry and its registration site: JSON-RPC method, subsystem callback, transport registration, or bdev module hook.
2. Separately prove execution: follow the dispatcher, handler, and first callable body; registration alone is not execution.
3. Follow state and call edges across `spdk_thread_send_msg`, poller registration/removal, completion callback, and error label.
4. Build a resource chain for bdev descriptor, io_channel, qpair, request, DMA buffer, and mempool element.
5. Follow init/fini/reset and every async failure completion to the external status, log, counter, or later retry behavior.

## Required mechanism translation

| Source mechanism | Must check invariant | Constructible control | External oracle | Common misread |
| --- | --- | --- | --- | --- |
| JSON-RPC/subsystem registration | handler is actually reachable and validates arguments | valid/invalid request through supported harness | status/result and log | registration means request ran |
| thread message/poller | callback runs on intended thread and is removed before owner dies | delayed completion or shutdown race fixture | completion once; no stale callback | function name proves affinity |
| bdev/io_channel | acquire/release is paired on all paths | fail channel acquisition or close during I/O | error completion and stable counters | channel is globally owned |
| mempool/DMA | allocation, mapping, completion and return preserve ownership | bounded allocation/fault injection | explicit error and pool watermark recovery | allocation implies leak |
| reset/fini unwind | outstanding operation gets one terminal outcome | reset during pending request | bounded completion/cancel and later usable state | reset is synchronous |

## Per-obligation minimum

For `trace`, name entry, at least one cross-file edge, and terminal external effect.
For `state` or `concurrency`, name state/affinity guard and the event that changes it.
For `resource` or `error`, name acquisition, owner, release/unwind, and the relevant failure edge.
For `blackbox`, provide a source-supported control and independently observable oracle.
For `disconfirm`, identify the exact guard or cleanup that defeats the hypothesized fault.
For N/A write a narrow boundary, e.g. “assigned range contains only registration metadata; no handler/call edge in the allowed range,” plus exact counterevidence. This is an example shape, not a fact about the target.

## False-positive guards

Never call a thread race without an actual cross-thread handoff or shared state.
Never call DMA unsafe without a source-backed lifetime/mapping edge.
Never call a leak when a later callback or common unwind releases the resource.
Never claim an RPC is externally usable without dispatcher and argument-path evidence.
Never duplicate NVMf state-machine analysis that belongs to the NVMe-oF checklist.

## 4096-token completion order

Emit obligation ID, exact facts, causal edge, control, oracle, disposition, and unresolved item first.
Stop after all assigned obligations have one supported disposition and list remaining unproven edges as `need_verify`.
Do not add SPDK architecture history, duplicate risk cards, or unmatched transport detail.

## Fact ledger fields

Record the external actor and request/event.
Record registration symbol and separately the proven invocation symbol.
Record thread/reactor or poller identity when source states it.
Record callback scheduling edge and completion edge.
Record bdev descriptor and io_channel owner separately.
Record DMA/mempool allocator, borrower, and return point.
Record state before and after reset/fini.
Record error label and the resource it unwinds.
Record observable status, log, or counter without fabricating one.

## Cross-file stopping rules

Stop at a function pointer only after locating its concrete assignment or mark it `need_verify`.
Stop at an async enqueue only after locating completion/cancellation or mark the terminal edge unresolved.
Stop at a subsystem interface if its implementation is outside allowed ranges; do not fill the gap from memory.
Stop after the exact ownership boundary is known; do not enumerate unrelated bdev modules.

## Narrow N/A examples

“The assigned lines only serialize an already-computed result; no reactor, bdev, or callback edge occurs here.”
“The assigned registration record has no dispatcher or handler body in allowed ranges.”
“The matched symbol is an NVMf name only; transport state analysis is routed to NVMe-oF.”
Each example requires target-specific counterevidence before use.

## Fragment self-check

Check every fact has a path and line range.
Check every causal claim has a call, state, or ownership edge.
Check the oracle is external, not another inferred source fact.
Check each assigned obligation has exactly one disposition.
Check unknown callback behavior is `need_verify`.
Check the fragment contains no unassigned source range.
