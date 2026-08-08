---
name: storage-iscsi
description: Analyze iSCSI source ranges only when matched source covers login, session, connection, task, PDU, CmdSN, StatSN, ITT, TTT, CHAP, digest, timeout, recovery, resource ownership, or concurrent protocol state.
---

# iSCSI analysis

Use this only as a conditional domain method for `analysis-worker`; it is not a persona or an agent. Do not delegate, alter source, or claim completion.

Read [references/analysis-checklist.md](references/analysis-checklist.md) in full only when an assigned obligation covers login/session/connection/task state, sequence numbers, PDU/auth/digest, timeout/recovery, ownership, or a source-backed protocol oracle.

1. Activate only for matched iSCSI source. Bind all facts to inventory IDs, obligation IDs, and allowed ranges; otherwise return a narrow N/A boundary with source counterevidence.
2. Trace login/session/connection/task/PDU paths and sequence identifiers (CmdSN, StatSN, ITT, TTT) through state guards, send/receive ordering, and cleanup.
3. Examine CHAP/digest negotiation, timeout/recovery, task/resource lifetime, and concurrent access only where source proves them. Do not import protocol folklore.
4. Turn source facts into black-box controls and oracles: login/auth outcome, PDU/status progression, timeout/recovery signal, and resource/reconnect behavior. Keep peer or target assumptions as `need_verify`.
5. Return a disposition for every obligation. Require exact facts for High, Critical, P0, and P1; use N/A only with narrow scope plus counterevidence.

Runtime records triggered scope, applicable obligations, and content hash in its receipt; this text being loaded proves nothing. Under 4096 tokens, prioritize assigned `analysis_fragment` contributions over background and repeated risk cards.
