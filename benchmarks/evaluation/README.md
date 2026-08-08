# Blind evaluation contracts

This directory freezes the public benchmark conditions: the user-supplied
`codetalks-fused-v2.4-zh.zip` archive and its SHA-256, the separately verified
CodeTalk v2.4 preset path/Skill version/content digest, SPDK/nvme-cli commits,
OpenCode model/budget, fair tracks, and the candidate-visible corpus.  The
comparison target is the uploaded CodeTalks v2.4.0 Skill, materialized from
that frozen archive and invoked through a separately hashed minimal OpenCode
adapter.  The adapter supplies only the runnable primary-agent boundary and
managed output root; it does not add PANGEA analysis methods.  The sealed
scoring Oracle for a real run
must live under an evaluator-controlled directory outside this workspace; it
must never be mounted, copied, or prompted into a candidate run.  The legacy
fixtures under `benchmarks/oracles/` are inactive legacy repository test data,
not active evaluator Oracle input.  The active public cases live in
`benchmarks/manifest.json`, conform to `public-case.schema.json`, and stage
only as `TASK.md` plus `CASE.json`; `benchmarks.stage` never reads, copies, or
deletes an Oracle directory. Staging requires a trusted root and walks every
directory through no-follow file descriptors, so traversal and symlinked
ancestors fail closed. Publication uses no-replace hard links rather than
replacement, and every self-created pathname is recorded by device/inode;
failure cleanup unlinks only the same inode, so it cannot erase a concurrently
published or replaced final. Source-scope paths reject traversal, trailing
slashes, and NUL/control characters in both the JSON Schema and Python
validation. The JSON Schema is also executed by manifest validation,
in addition to the frozen eight-case and phase-matrix checks.

`evaluation.benchmark.execute_opencode()` is the sealed primary-phase runner.
It preserves only an explicit allowlist of host/provider environment keys,
records key names but never secret values, and runs `opencode --version`,
`opencode debug agent`, and the primary `opencode run` in one isolated
environment. Equal Tools is an
agent-level override whose resolved enabled tools must be exactly
`read/glob/grep`.  As-Shipped means candidate prompt, frozen skills, and worker
topology plus an explicit evaluator safety overlay; it is not a claim of zero
override.  The overlay enables `read/glob/grep/skill/task/bash`, default-denies
task and bash, then admits only the frozen workers and audited read-only or
managed-runtime commands.  Baseline and overlaid primary/worker configs are
independently resolved, prompt/tool/permission hashes are bound into the
receipt, and a missing or unsafe worker fails before model execution. An
As-Shipped primary-only receipt is deliberately not score-eligible, and any
same-process leaf-role task is rejected.

OpenCode 1.18.4 debug resolution is frozen to 120 seconds per `debug config`
or `debug agent` invocation, based on a local provider-free observation of
70.571 seconds. The version probe remains capped at 30 seconds, model runs at
the 1800-second frozen wall-clock limit, and the aggregate evaluator deadline
continues to cover the complete entry-to-exit execution.

`execute_isolated_role()` is the only evaluator-owned leaf-role boundary.
`analysis-worker` receives exactly `CONTEXT.json`; `auditor` receives exactly
`CLAIM.json` and `FACTS.json`. Each invocation has its own process, HOME/XDG
directories, and minimal working directory. Resolved permission rules must
equal the intended role overlay exactly. The evaluator signs the complete
role receipt, and the Run persists the attestation under
`internal/execution-receipts/`. Pre-Judge and pure Judge verify the signature,
role, session, input artifacts, output payload, file name, and exact directory
membership. Raw JSONL or self-declared receipt hashes are not accepted.

OpenCode 1.18.4 native JSONL contains `step_start`, `step_finish`, `tool_use`,
`text`, `reasoning`, and `error` events.  Token usage and finish reasons come
from `step_finish.part`; final candidate text comes from `text.part.text`.
Tool input is accepted from either `state.input` or `part.input`; receipts keep
only normalized action/target and an input digest, never the raw input.  Paths,
worker/Skill targets, and every bash command are audited.  The public bundle is
hashed before and after execution: only `pangea-data` and
`.evaluator-scratch` may change, while protected changes, out-of-scope additions,
symlinks, and special files fail.  Unknown/malformed events, missing
token/finish observability, provider errors, tool/network policy violations,
wall-clock timeout, output truncation and budget excess all fail closed.  The runner never uses a synthetic
`evaluation_summary` event.

The frozen policy states `candidate_network=disabled` while
`provider_transport=required`.  The evaluator does not itself provide an
operating-system network sandbox: model-provider transport must remain
available.  Its enforceable candidate boundary is disabled network tools,
event-level rejection of network attempts, restricted task/Skill dispatch,
audited bash, and post-run filesystem integrity.  A
deployment claiming stronger egress isolation must supply that external
sandbox receipt separately.  In particular, trusted managed runtime CLIs are
not treated as proof of OS-level egress isolation; their own network behavior
must be constrained by a read-only archived runtime or an external sandbox.

The neutral normalizer and scorer deliberately separate candidate output from
private rubric credit.  Adapter output marked `evaluator_review_required` is
not scoreable: an independent evaluator must provide an outside-workspace,
raw-digest-bound resolution receipt that disposes every unparsed section.
Only then may the evaluator supply per-dimension credit.  The frozen scorecard is 100 points
(recall 30, precision 15, evidence 20, black-box executability 15, flow
coverage 10, N/A specificity 10), with absolute and Fuse-comparison gates.
# Frozen evaluation status

The comparison freezes the uploaded CodeTalks Skill
`codetalks-source-driven-blackbox-v2` at v2.4.0 and its archive SHA-256.  It
is run through the minimal `codetalks-fused-v2.4` adapter, not treated as an
unresolved runtime; the verified preset is corroborating evidence only.

The official configured route is `deepseek/deepseek-v4-flash` through the
DeepSeek provider.  Candidate network tools are disabled (provider transport
remains required).  Real valid samples are currently 0: real A/B execution
awaits an official provider login and a later evaluator run.
