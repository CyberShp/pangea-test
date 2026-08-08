# R2 analysis and evaluation readiness

Date: 2026-08-07

## Outcome

The deterministic R2 path is locally complete and independently audited:

- independent source inventory and obligation denominator;
- immutable context packs with capability/Storage Skill receipts;
- generic `analysis-worker` fragments with eight contribution families;
- replayable publication and fragment transactions;
- full-body merge/projection and exact H/C causal-chain binding;
- independent semantic Judge with signed worker/auditor execution attestations;
- four-role product topology only: `pangea-test`, `analysis-worker`, `auditor`, and conditional `mr-reader`;
- isolated leaf-role processes with role-specific permissions and minimal visible artifacts.

The implementation does not treat a primary-only As-Shipped OpenCode run as
score-eligible. A complete evaluator-owned composition must consume signed
leaf-role outputs; same-process leaf-role task events fail closed.

## Frozen inputs

| Input | Frozen identity |
|---|---|
| PANGEA main | `1ccacd68d811c762570afa47bdaa16e7e9a41c66` |
| SPDK | commit `97af299e3c76368219f0cddcc710fafd57edcc1c`, tree `3718a94e7956cd5f15a1e8edb65d6bbeacef9c7d` |
| nvme-cli | commit `cc00f4fd5d8262c440d033de9504ebf641880e62`, tree `a0f34ca372b1fe44cba2bfd1be1a02c2ba808349` |
| CodeTalks archive | SHA-256 `7369ef35d339bc554610754ceb385b78d15f94fc8e1e5435350c4ebcf2b27325` |
| Verified Fuse preset | `skill_version_build_cd5236626f824050a1598a845d2b5eba`, digest `sha256:8217e197c006884f845a141b967d498c0a3fa716ccb4dd924fbad11377b0fbfc` |
| OpenCode/model | `1.18.4`, official `deepseek/deepseek-v4-flash`, 200K context, 180K conservative input envelope, 4096 output |

The evaluator accepts only the official DeepSeek route. It obtains credentials
from `DEEPSEEK_API_KEY` or a regular, non-symlink OpenCode `auth.json` containing
an exact DeepSeek API entry with only `type` and `key`. Mixed source files are
accepted, but the evaluator constructs a new auth file containing only that
validated `deepseek` entry; other provider entries are never projected. The
minimal file is written with mode `0600` into an evaluator-owned temporary XDG
data directory outside the public bundle and leaf artifact cwd; the temporary
HOME/XDG tree is removed on return or exception, and credential values, source
file contents, and credential hashes never enter receipts.

The archive is frozen as the uploaded CodeTalks v2.4.0 Skill and is executed
through the evaluator's minimal `codetalks-fused-v2.4` OpenCode adapter.  The
adapter is identified separately from the Skill and does not add PANGEA
methods.  The 180K leaf-input envelope reserves context for system/tool/protocol
material and output; the model context window itself remains 200K.

## Local acceptance evidence

- `python3 -m unittest discover -s tests -p 'test_*.py'`: 538/538 PASS.
- `python3 -m pytest -q`: 538/538 PASS.
- Draft 2020-12 meta-validation: 36/36 schemas PASS.
- Python compilation for evaluator/runtime/benchmark/tooling modules: PASS.
- `git diff --check`: PASS.
- Public corpus materialization: both pinned commit/tree pairs PASS; 184
  candidate files; zero bundle validation errors; evaluator signing material
  absent from the public bundle.
- Smoke staging: 2/2 cases publish exactly `CASE.json` and `TASK.md`.
- Pilot staging: 4/4 cases publish exactly `CASE.json` and `TASK.md`.
- Independent Sol audit: R2 transaction replay, semantic Judge, role/process
  isolation, signed persistence, exact receipt-directory closure, and real
  TemporaryDirectory stage-report path PASS.

The first macOS case-staging attempt used the lexical `/var/...` temporary
path and was correctly rejected because `/var` is a symlink. Re-running with
the resolved `/private/var/...` trusted root passed. This is expected from the
no-follow publication contract.

## Frozen quality gates

Absolute gates: score >= 90; Critical recall 100; High recall >= 95; mutation
kill >= 90; supported precision >= 90; evidence references 100; semantic
support >= 97; P0 >= 95; P1 >= 90; black-box executability >= 90; N/A
specificity >= 95; applicable disposition 100; H/C contribution retention
100; unsupported H/C on clean/fixed cases 0; no truncation, invalid JSON, or
hard-gate regression.

Comparison gates: non-inferior when mean score delta >= 0 and paired CI lower
bound >= -2; exceeds Fuse when delta >= 3, CI lower bound > 0, core-case win
rate >= 70%, and no hard-gate regression.

## Execution status

| Phase | Status | Reason |
|---|---|---|
| Local smoke/pilot corpus and contract validation | PASS | Frozen inputs materialize and validate locally. |
| Clean/N/A, mutation, scorer, adapter-review and gate regression | PASS (deterministic fixtures) | Covered by the final 538-test unittest / pytest suites; no model-quality claim is inferred. |
| Real PANGEA smoke/pilot model calls | PENDING | Requires an official DeepSeek login and a controlled single-case calibration; no valid model telemetry exists yet. |
| Real ablation | BLOCKED | Requires successful matched model executions. |
| Blind CodeTalks Skill A/B | BLOCKED | The Skill and minimal adapter are frozen; matched official-provider samples must exist before blind comparison. |

No score, non-inferiority result, or “better than Fuse” claim may be issued
until the blocked real executions produce valid sealed receipts.
# R2 evaluation readiness update

CodeTalks is now frozen as the uploaded v2.4.0 Skill plus the minimal
`codetalks-fused-v2.4` adapter.  Its formal Markdown/JSON output is collected
from `codetalks-data` by the evaluator; chat final text is auxiliary when
files exist.  The frozen model is `deepseek/deepseek-v4-flash` on the official
DeepSeek provider route.  No real valid samples have been produced yet; A/B
execution remains pending official provider login and a subsequent run.
