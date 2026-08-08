# nvme-cli cross-file checklist

## Boundary and trigger

Use when allowed ranges contain builtin command tables, `ENTRY`, `cmd_handler`, command macros, plugin selection, alias/prefix matching, parsing/opening, libnvme calls, status mapping, or output code.
Do not activate from a command name in documentation or a test alone.
Do not treat an ENTRY row, macro declaration, or plugin descriptor as executed behavior.

## Evidence order

1. Start at command spelling and locate ENTRY/cmd_handler plus all macro expansion stages needed to identify the real handler.
2. Follow main dispatch, plugin lookup, alias and prefix disambiguation, then validate the selected handler.
3. Follow option parsing and `parse_and_open`-style context/handle acquisition through each early return.
4. Follow the libnvme admin call, distinguishing syscall `errno`, library return convention, and NVMe completion status.
5. Follow normal, JSON, and binary output and final main/handler exit mapping to an externally testable oracle.

## Required mechanism translation

| Source mechanism | Must check invariant | Constructible control | External oracle | Common misread |
| --- | --- | --- | --- | --- |
| ENTRY/macro chain | spelling resolves to exactly the traced handler | unique, alias, and ambiguous prefix input | selected handler or rejection exit | table row proves execution |
| plugin/alias dispatch | plugin scope cannot steal/ignore intended command | plugin-qualified and unqualified input | exit/stderr identifies resolution | plugin exists means loaded |
| parse/open | ctx and handle are valid or released on every early exit | malformed option or open failure fixture | exit and diagnostic; no stale handle | parse succeeded means device opened |
| libnvme call | errno, return code and NVMe status are not collapsed | injected library error/status fixture | exact exit/output branch | nonzero always errno |
| output mode | machine output remains coherent with result path | normal/JSON/binary fixture | parseable selected output or documented error | stdout proves command succeeded |

## Per-obligation minimum

For `trace`, include spelling, final handler, one post-dispatch edge, and exit/output effect.
For `resource`, include ctx/handle acquisition and closing owner on success and failure.
For `error`, identify the distinct status domains and their conversion point.
For `blackbox`, choose a source-supported argument/control and exit/stdout/stderr oracle.
For N/A give narrow counterevidence, e.g. “assigned range formats output only and contains no dispatch or admin call”; this is a template, not target evidence.

## False-positive guards

Do not infer a macro expansion without reading the defining and invocation ranges.
Do not call an alias ambiguous without the matching rule and competing names.
Do not claim device mutation from parsing or command declaration.
Do not merge transport/NVMe completion status with host `errno` absent the source conversion.
Do not use output text as an oracle without binding it to a return path.

## 4096-token completion order

Emit command resolution, parse/open fact, operation/status fact, output/exit oracle, then disposition.
Stop after all assigned obligations are disposed; list missing macro definitions or device semantics as `need_verify`.
Avoid command catalogues and generic NVMe background.

## Fact ledger fields

Record the raw command spelling and qualification.
Record ENTRY/macro definition and expansion evidence.
Record final handler and main/plugin dispatch edge.
Record parsed option and validation branch.
Record ctx, fd, and handle owner and close path.
Record library call, host return, errno, and NVMe status separately.
Record chosen output formatter and final exit branch.
Record a testable external effect without assuming device mutation.

## Cross-file stopping rules

Stop at a macro only after locating enough definition layers to identify the handler; otherwise `need_verify`.
Stop at plugin metadata until lookup shows selection semantics.
Stop at a library declaration when its status convention is not in allowed source.
Stop after exit/output behavior is proven; do not trace every unrelated builtin.

## Narrow N/A examples

“The range only defines a help string and contains no dispatch or operation edge.”
“The assigned parser branch rejects before ctx/handle acquisition.”
“The range formats a supplied result but contains no status conversion.”
These are templates; cite exact target lines before issuing N/A.

## Fragment self-check

Check every macro claim names its expansion evidence.
Check dispatch and execution are represented separately.
Check handle ownership includes early error paths.
Check status domains are not collapsed.
Check every obligation has one supported disposition.
Check unresolved device behavior is `need_verify`.
