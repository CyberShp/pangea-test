# iSCSI cross-file checklist

## Boundary and trigger

Use only when the matched source implements iSCSI login/session/connection/task/PDU behavior, sequence numbers, authentication/digest, or recovery.
Do not infer an implementation merely because comments contain CmdSN, CHAP, or “iSCSI”.
Do not apply target-side rules to initiator-only ranges without a traced boundary.

## Evidence order

1. Locate login entry and map login, session, connection, and task ownership separately.
2. Follow PDU parsing/building and state transitions; record guards around CmdSN, ExpCmdSN, MaxCmdSN, StatSN, ITT, and TTT only if executable code uses them.
3. Follow CHAP/digest negotiation and every validation/failure branch. Before generating tests, extract every source-backed authentication parameter that changes negotiation, validation, state, fallback, or error behavior.
4. Follow timeout/recovery through task/PDU ownership, cancellation, connection teardown, and re-establishment.
5. Translate only source-proven results into external login, authentication, status, ordering, timeout, or recovery oracles.

## Required mechanism translation

| Source mechanism | Must check invariant | Constructible control | External oracle | Common misread |
| --- | --- | --- | --- | --- |
| login/session/connection | each layer has an explicit owner and teardown | failed login or connection-loss fixture | rejected login and released session state | connection equals session |
| sequence window | sequence acceptance checks executable bounds | in-window/out-of-window synthetic PDU | accepted/rejected status transition | variable name proves validation |
| ITT/TTT task link | task correlation survives error/cancel once | delayed or duplicate task response fixture | one terminal task result | ID field proves lifetime safety |
| CHAP/digest | negotiated option gates data path | source-supported auth parameter combination or bad auth/digest fixture | auth/result branch, no silent advance | one success case proves all algorithms/lengths |
| timeout/recovery | timeout terminalizes or transfers every owner | withheld PDU in harness | bounded recovery/error outcome | timer means recovery works |

## CHAP/authentication matrix

Build the matrix from executable source and user material, not protocol folklore. Look for the following dimensions only when the source exposes them:

- authentication direction or role, such as one-way versus mutual authentication;
- negotiated authentication/hash algorithm values;
- source-visible DH/group/key/secret length, range, enum, table, or boundary;
- credential state: correct, incorrect, missing, mismatch, expired/invalid if implemented;
- fallback, preferred-order, unsupported-value and negotiation failure paths;
- retry/re-authentication/session-recovery behavior if it changes the result.

For each dimension, record the exact source anchor that proves the value or boundary. If two or more finite dimensions feed the same negotiation/validation/state path, enumerate their supported combinations. **每个保留组合必须映射到至少一个明确的测试用例。** 用例标题或第一步必须包含精确参数组合；distinct invalid/missing/unsupported branches require negative cases.

Do not collapse a matrix into “single-direction CHAP succeeds” or “test all algorithms” when multiple source-backed values exist. A representative case is acceptable only when source evidence proves the omitted values are behaviorally independent/equivalent; record that proof and still cover each boundary plus at least one cross-dimension check.

Example format, values are placeholders and must be replaced by source-proven values:

```text
参数维度：认证方向=单向|双向
参数维度：算法=A|B
参数维度：DH/密钥长度=L1|L2
参数维度：凭据状态=正确|错误
参数组合：认证方向=单向,算法=A,DH/密钥长度=L1,凭据状态=正确
```

## Per-obligation minimum

For trace state the layer, entry, PDU/task edge, and external effect.
For state/concurrency state the sequence or ownership guard and transition event.
For resource/error state the owner and teardown/cancel edge.
For N/A give narrow counterevidence, e.g. “range declares a protocol constant but has no parser, state guard, or call edge”; template only.
For CHAP/authentication state the source-backed dimensions, retained combinations, negative branches, and one external oracle per result class.

## False-positive guards

Never derive protocol compliance from names or comments.
Never call a sequence race without executable window and concurrent path evidence.
Never treat ITT/TTT presence as proof of correct correlation.
Never call a digest/auth bypass without a source path that accepts invalid input.
Never claim recovery if no terminal task ownership is traced.
Never invent an algorithm, DH/group/key length, credential state, or fallback rule that the source/user material does not expose.
Never claim authentication coverage complete while a source-backed matrix cell has no case or explicit unsupported/N-A evidence.

## 4096-token completion order

Emit layer, exact evidence, invariant, safe control, oracle, and disposition per obligation.
For CHAP/authentication, preserve parameter dimensions and combination coverage before background explanation or duplicate risk text.
Stop after complete dispositions and use `need_verify` for peer/network behavior.
Omit generic protocol teaching and duplicate risk text.

## Fact ledger fields

Record whether the path owns login, session, connection, or task state.
Record PDU direction and parser/builder edge.
Record each executable sequence guard and transition.
Record ITT/TTT association and cancellation owner.
Record CHAP/digest negotiated state and rejecting branch.
Record each source-backed authentication parameter dimension/value and whether it couples with another dimension.
Record retained test combinations and any unsupported/N-A combination with exact evidence.
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
“The source exposes only one authentication algorithm and no alternative or fallback path, so algorithm matrix expansion is N/A for this range.”
Use only with exact counterevidence from the target.

## Fragment self-check

Check layer ownership is declared before sequence reasoning.
Check each sequence claim uses an executable guard.
Check PDU/task ownership reaches a terminal action.
Check the oracle comes from source-visible local behavior.
Check every obligation has one disposition.
Check peer assumptions remain `need_verify`.
For CHAP/authentication, check every source-backed finite dimension is listed and every retained combination has an explicit case or a source-backed unsupported/N-A disposition.
