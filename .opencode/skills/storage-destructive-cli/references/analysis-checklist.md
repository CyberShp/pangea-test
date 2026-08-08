# Destructive CLI safety checklist

## Boundary and trigger

Use only for source-backed format, sanitize, namespace deletion, firmware activation, reset, force, confirmation, exclusivity, admin submission, rescan, or post-command update paths.
Default to analysis only: never issue a real-device destructive command.
Do not confuse a user confirmation prompt with device-side authorization or safe completion.

## Evidence order

1. Locate command dispatch and prove which spelling resolves to the destructive handler.
2. Trace target-scope selection: controller, namespace, file/device handle, plugin context, and any ambiguity rejection.
3. Trace capability, privilege, force, exclusivity, and confirmation gates separately.
4. Locate the actual admin submission point; distinguish submission, host return, device completion, reset, rescan, and cached-state update.
5. Translate only source-proven behavior into a safe substitute control and non-destructive oracle.

## Required mechanism translation

| Source mechanism | Must check invariant | Constructible control | External oracle | Common misread |
| --- | --- | --- | --- | --- |
| dispatch/target scope | selected target is unambiguous and intended | pure mock captures selected handle | mock receives exact target or rejects | command name proves target |
| capability/confirmation | each gate is checked before submission | mocked missing capability/negative confirmation | no submission observed | prompt means device is protected |
| force/exclusive | bypass and exclusivity conditions are explicit | mocked busy/exclusive denial | deterministic refusal path | force always bypasses all guards |
| admin submission | submit is distinct from completion | mock returns submit success then completion failure | correct status/update path | submit success means mutation complete |
| rescan/reset/update | post-action state is refreshed only after supported result | emulator or safe fixture state transition | observed refresh/reset status | rescan proves device result |

## Safe test substitute hierarchy

Use pure mock first: assert arguments, gate order, and whether submission would occur.
Use an emulator second when it models the required completion/status behavior.
Use a clearly isolated, one-time lab fixture only after explicit authorization and documented disposal; keep production and unknown devices out of scope.
Never provide copyable destructive device commands in analysis output.

## Per-obligation minimum

For trace provide dispatch, target, pre-submit gates, submission/completion distinction, and resulting external update.
For blackbox state substitute tier, control, and oracle without real hardware mutation.
For safety provide exact missing/present guard evidence, not a generic warning.
For N/A give narrow counterevidence, e.g. “assigned range renders help text and contains no handler, target, or submit edge”; this example is not target fact.

## False-positive guards

Do not call a confirmation a capability check.
Do not call host submit success device completion.
Do not claim destructive reachability from help/ENTRY text alone.
Do not propose a real-device experiment to resolve uncertainty.
Do not treat rescan or reset as evidence of irreversible effect without source support.

## 4096-token completion order

Emit target, gates, submission/completion fact, safe substitute control, oracle, and disposition first.
Stop after assigned obligations are disposed and record device semantics as `need_verify`.
Do not include command recipes, vendor background, or repeated risk cards.

## Fact ledger fields

Record command spelling, dispatcher, and final handler.
Record selected controller/namespace/handle scope and ambiguity branch.
Record capability, privilege, confirmation, force, and exclusive gates separately.
Record admin submission point without spelling a runnable device command.
Record host return and device-completion distinction.
Record rescan, reset, and cached-state update condition.
Record safe substitute tier and mock/emulator observable result.
Record unresolved device semantics as `need_verify`.

## Cross-file stopping rules

Stop at UI confirmation until its gate reaches the submit path.
Stop at a submit wrapper until its completion/error convention is known.
Stop at an emulator boundary without assuming physical-device equivalence.
Stop after the selected target and safety gates are proved; do not enumerate other destructive handlers.

## Narrow N/A examples

“The assigned range renders help and never resolves a target or submission point.”
“The path validates display options only and has no capability/force/confirmation gate.”
“A reset label is present, but no destructive command dispatch reaches it in allowed ranges.”
These are example shapes only; bind N/A to exact target counterevidence.

## Fragment self-check

Check selected target scope is source-proven.
Check confirmation and device-side safety are kept distinct.
Check submit and completion status are kept distinct.
Check proposed control is mock, emulator, or authorized isolated lab only.
Check every obligation has one disposition.
Check unknown device semantics is `need_verify`.
