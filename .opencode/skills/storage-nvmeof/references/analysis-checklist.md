# NVMe-oF cross-file checklist

## Boundary and trigger

Use only where source identifies initiator or target plus controller, subsystem, namespace, qpair, TCP/RDMA, discovery, keepalive, reset, or reconnect behavior.
First label every path initiator or target; do not bridge the two from protocol terminology.
Do not analyze generic socket code as NVMe-oF without matched transport/control evidence.

## Evidence order

1. Locate connect/disconnect/discovery/keepalive/reset entry and prove initiator or target ownership.
2. Follow controller/subsystem/namespace/qpair state guards and creation/destruction edges.
3. Follow TCP PDU or RDMA request/credit allocation, queued/outstanding transition, completion callback, and error branch.
4. Follow reset/reconnect through terminalization of outstanding work and release/recreation of transport resources.
5. Bind the result to a source-visible external connect, I/O, timeout, discovery, or recovery observation.

## Required mechanism translation

| Source mechanism | Must check invariant | Constructible control | External oracle | Common misread |
| --- | --- | --- | --- | --- |
| connect/qpair creation | state permits creation exactly once | connect then duplicate/disconnect fixture | accepted/rejected connection state | qpair symbol means connected |
| TCP PDU/RDMA credits | queued/outstanding work is bounded and terminalized | constrained credits or delayed completion | bounded failure/completion and counters | credit variable proves flow control |
| keepalive | timeout changes controller state through a real path | withheld response in safe harness | timeout/reset/reconnect signal | timer registration means recovery |
| reset/reconnect | old callbacks cannot complete a destroyed generation | reset with outstanding request | one terminal result and later operation | reset automatically drains everything |
| namespace/subsystem binding | request target stays within selected boundary | invalid/missing namespace fixture | explicit rejection/status | subsystem name proves authorization |

## Per-obligation minimum

For state/concurrency provide role, state guard, event, and completion boundary.
For resource provide allocation, owner, outstanding/queued accounting, and terminal release.
For blackbox provide a safe connect/I/O/recovery control and observable result.
For N/A provide a narrow source boundary and counterevidence, e.g. an assigned TCP utility range lacks NVMe-oF objects or call edges. This is only an example.

## False-positive guards

Do not mix target and initiator ownership.
Do not infer RDMA credits from a queue name alone.
Do not call a reconnect bounded without source-backed retry/terminal conditions.
Do not claim outstanding work leaks while its callback or reset drain remains reachable.
Do not claim device completion from host-side submission alone.

## 4096-token completion order

Emit role, exact state/resource facts, control, oracle, disposition, then unresolved edge.
Stop once all assigned obligations are disposed and mark peer/device semantics `need_verify`.
Skip generic NVMe-oF protocol explanation and repeated cards.

## Fact ledger fields

Record role: initiator or target, never both by default.
Record controller/subsystem/namespace/qpair identity and state.
Record transport object and request/credit owner.
Record queued, outstanding, completed, and cancelled transition when present.
Record callback generation or reset boundary.
Record retry/backoff bound and stop condition when source states one.
Record resource release and recreation owner.
Record source-visible external status or counter.

## Cross-file stopping rules

Stop at a peer boundary; remote behavior is `need_verify` unless local source observes it.
Stop at TCP/RDMA abstraction until concrete transport callback is located.
Stop at a reset call until pending work terminalization is traced.
Stop after the assigned state path is complete; do not document unrelated discovery commands.

## Narrow N/A examples

“The range only holds a transport-neutral constant and no controller/qpair call edge.”
“The source is target-only while the assigned obligation is explicitly initiator-only.”
“No selected namespace or transport resource is reachable from this range.”
These examples require exact counterevidence for the target.

## Fragment self-check

Check role is declared before cross-file causality.
Check each state transition names its event and guard.
Check queued/outstanding work has a terminal path or `need_verify`.
Check source range supports the chosen external oracle.
Check every obligation has one disposition.
Check no remote behavior is invented.
