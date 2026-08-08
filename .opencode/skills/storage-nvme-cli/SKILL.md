---
name: storage-nvme-cli
description: Analyze nvme-cli source ranges when obligations cover builtin ENTRY tables, cmd_handler dispatch, macro-generated commands, plugin or alias selection, argv parsing, libnvme admin calls, status/exit handling, output formatting, or device-context ownership.
---

# nvme-cli analysis

Use this only as a conditional domain method for `analysis-worker`; it is not a persona or an agent. Do not delegate, alter source, or claim completion.

Read [references/analysis-checklist.md](references/analysis-checklist.md) in full only when an assigned obligation spans ENTRY/macro dispatch, plugins or aliases, parse/open ownership, libnvme status, output mode, or a source-backed CLI oracle.

1. Bind every claim to inventory IDs, obligation IDs, and allowed ranges. Start with precise facts for ENTRY/cmd_handler declarations, generated expansion sites, `main`/plugin dispatch, parsed argument, opened handle, and returned status.
2. Trace command spelling or alias/prefix → dispatcher → parser/open → libnvme admin operation → status/exit/output. Treat a command declaration as reachability evidence only, never as proof that the command executed.
3. Check plugin selection, alias and prefix ambiguity, multi-stage macros, and ownership of `ctx`, fd, and handle across early failures and output paths.
4. Produce black-box controls/oracles from source-visible arguments, exit code, stderr/stdout, and device state only. Preserve initiator-side uncertainty as `need_verify` rather than inventing device behavior.
5. Dispose every obligation individually. N/A requires a narrow scope and source counterevidence. High, Critical, P0, and P1 contributions require exact facts; otherwise say `need_verify`.

Runtime records triggered ranges, obligations, and the content hash in the receipt. “Skill loaded” is not analysis. Within 4096 tokens, fit the assigned `analysis_fragment` before any background explanation; do not repeat command encyclopedias or risk cards.
