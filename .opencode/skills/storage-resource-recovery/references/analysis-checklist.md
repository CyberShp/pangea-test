# Resource and recovery checklist

## Boundary and trigger

Use only for matched acquire/transfer/release/unwind, refcount, pool/queue, FD/socket/timer/poller, reconnect, reset, or failover source paths.
Do not activate solely on `malloc`, `free`, `close`, or a variable named `ref`.
Do not turn a resource review into a claim without a path from acquisition through terminal outcome.

## Evidence order

1. Create a resource ledger: type, acquisition point, initial owner, transfer/ref edge, terminal release, and observable counter.
2. Draw acquire → transfer → release/unwind for success and every reachable failure edge.
3. Trace refcount/queue/pool conservation and FD/socket/timer/poller registration/removal across callbacks.
4. Exercise repeated fault, long-run, reset, reconnect, and failover paths that may retain old ownership.
5. Translate a source-backed imbalance into a safe fault control and external oracle such as counter recovery, bounded retry, or one terminal completion.

## Required mechanism translation

| Source mechanism | Must check invariant | Constructible control | External oracle | Common misread |
| --- | --- | --- | --- | --- |
| allocation/owner | every acquired object has one terminal owner | fail immediate successor allocation | stable counter and error result | allocation alone is leak |
| transfer/refcount | increment/decrement matches ownership handoff | delayed callback/cancel fixture | no double terminalization | refcount field proves safety |
| pool/queue | debit/credit remains bounded and conserved | capacity pressure/retry fixture | watermark returns or bounded failure | queue growth is leak |
| fd/socket/timer/poller | registration is removed before object destruction | shutdown/reconnect fixture | no stale event; handle count stable | cleanup hidden in callback is absent |
| reset/failover | old generation has terminal release before reuse | repeated reset/failover safe harness | stable long-run counters | one clean run proves recovery |

## Per-obligation minimum

For resource give a complete ledger row and one failure edge.
For error give the responsible owner at the branch and exact unwind/callback edge.
For concurrency give the handoff/callback condition and owner lifetime condition.
For blackbox give a non-destructive fault control and measurable counter/status oracle.
For N/A state a narrow excluded resource class and counterevidence; example: “only a borrowed pointer is read and no acquire/transfer/release edge appears.”

## False-positive guards

Do not call leak if a later common unwind/callback owns release.
Do not call UAF without dereference after a source-proven terminal release or lifetime loss.
Do not call double-free without two reachable releases of the same ownership token.
Do not call queue/pool leak without an expected conservation edge.
Do not infer long-run instability from a single failure path.

## 4096-token completion order

Emit ledger facts, failure edge, invariant, control, oracle, disposition, and unresolved item first.
Stop once assigned obligations are resolved; retain unknown allocator/callback behavior as `need_verify`.
Avoid generic memory-management advice and repeated cards.

## Resource ledger template

Name the resource token or object identity.
Name the acquire line and failure return.
Name the first owner and every transfer/ref increment.
Name successful release and each error/unwind release.
Name callback, timer, poller, or queue that can retain it.
Name the expected conservation counter or terminal state.
Name reset/reconnect/failover generation boundary when applicable.
Name the only safe fault control and external oracle.

## Cross-file stopping rules

Stop when release occurs through an unknown helper and mark it `need_verify` rather than call a leak.
Stop at a borrowed pointer when no ownership contract is source-visible.
Stop after every reachable failure edge for the assigned path is checked.
Stop before global allocator analysis unless it is required by an assigned obligation.

## Narrow N/A examples

“The range borrows an already-owned immutable object and contains no acquire, transfer, or release edge.”
“The path returns before queue insertion, so no queue conservation obligation applies there.”
“The timer is only read; registration/removal lies outside the assigned range.”
Each example must be supported by exact target facts.

## Fragment self-check

Check every resource has an acquisition or explicit borrowed boundary.
Check every suspected failure has a reachable error edge.
Check each release refers to the same ownership token.
Check the control cannot mutate production data.
Check every obligation has one disposition.
Check hidden helper behavior is `need_verify`.
