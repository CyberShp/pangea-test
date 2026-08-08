# iSCSI cross-file checklist

## Boundary and trigger

Use only when the matched source implements iSCSI login/session/connection/task/PDU behavior, sequence numbers, authentication/digest, or recovery.
Do not infer an implementation merely because comments contain CmdSN, CHAP, or “iSCSI”.
Do not apply target-side rules to initiator-only ranges without a traced boundary.

## Evidence order

1. Locate login entry and map login, session, connection, and task ownership separately.
2. Follow PDU parsing/building and state transitions; record guards around CmdSN, ExpCmdSN, MaxCmdSN, StatSN, ITT, and TTT only if executable code uses them.
3. Follow CHAP/digest negotiation and every validation/failure branch.
4. Follow timeout/recovery through task/PDU ownership, cancellation, connection teardown, and re-establishment.
5. Translate only source-proven results into external login, authentication, status, ordering, timeout, or recovery oracles.

## Required mechanism translation

| Source mechanism | Must check invariant | Constructible control | External oracle | Common misread |
| --- | --- | --- | --- | --- |
| login/session/connection | each layer has an explicit owner and teardown | failed login or connection-loss fixture | rejected login and released session state | connection equals session |
| sequence window | sequence acceptance checks executable bounds | in-window/out-of-window synthetic PDU | accepted/rejected status transition | variable name proves validation |
| ITT/TTT task link | task correlation survives error/cancel once | delayed or duplicate task response fixture | one terminal task result | ID field proves lifetime safety |
| CHAP/digest | negotiated option gates data path | bad auth/digest safe fixture | auth/error branch, no silent advance | option parsing proves enforcement |
| timeout/recovery | timeout terminalizes or transfers every owner | withheld PDU in harness | bounded recovery/error outcome | timer means recovery works |

## Per-obligation minimum

For trace state the layer, entry, PDU/task edge, and external effect.
For state/concurrency state the sequence or ownership guard and transition event.
For resource/error state the owner and teardown/cancel edge.
For N/A give narrow counterevidence, e.g. “range declares a protocol constant but has no parser, state guard, or call edge”; template only.

## False-positive guards

Never derive protocol compliance from names or comments.
Never call a sequence race without executable window and concurrent path evidence.
Never treat ITT/TTT presence as proof of correct correlation.
Never call a digest/auth bypass without a source path that accepts invalid input.
Never claim recovery if no terminal task ownership is traced.

## 4096-token completion order

Emit layer, exact evidence, invariant, safe control, oracle, and disposition per obligation.
Stop after complete dispositions and use `need_verify` for peer/network behavior.
Omit generic protocol teaching and duplicate risk text.

## Fact ledger fields

Record whether the path owns login, session, connection, or task state.
Record PDU direction and parser/builder edge.
Record each executable sequence guard and transition.
Record ITT/TTT association and cancellation owner.
Record CHAP/digest negotiated state and rejecting branch.
Record timeout source and recovery terminal action.
Record teardown order for connection, session, and task.
Record a local observable status/counter, not a guessed peer result.

## Cross-file stopping rules

Stop at a protocol constant unless an executable guard consumes it.
Stop at a PDU send/receive abstraction without the state transition.
Stop at peer response boundary and mark it `need_verify`.
Stop once assigned ownership is proven; do not map all iSCSI phases.

## Narrow N/A examples

“The range logs an iSCSI label but has no session/connection/task state edge.”
“The matched code is a checksum helper with no negotiation or PDU ownership call.”
“The assigned source cannot observe the required remote sequence window.”
Use only with exact counterevidence from the target.

## Fragment self-check

Check layer ownership is declared before sequence reasoning.
Check each sequence claim uses an executable guard.
Check PDU/task ownership reaches a terminal action.
Check the oracle comes from source-visible local behavior.
Check every obligation has one disposition.
Check peer assumptions remain `need_verify`.
