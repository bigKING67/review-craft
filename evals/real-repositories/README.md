# Real Repository Benchmark v1

This benchmark uses eight public repositories at immutable commits. Each benchmark
revision is the direct parent of a real upstream fix commit. The suite covers two Python
projects, two Node.js projects, Electron, Go, Rust, and JVM code, including mature
compatibility-sensitive projects.

Each repository defines five treatment-safe probes:

- a real finding bound privately to an upstream fix;
- a justified `KEEP` decision;
- a false-positive decoy;
- a claim that requires measurement;
- a claim that must remain blocked by an evidence gap.

A `KEEP` probe is a preservation candidate, not the rejected modernization proposal. Its public
prompt therefore starts with `Determine whether evidence supports keeping ...`: evidence that
supports `KEEP`, `DEFER`, or `DOCUMENT` validates the candidate, while `FALSIFIED` means the
preservation decision itself was refuted. This convention is repeated in every treatment prompt
so disposition labels do not depend on which side of the comparison a reviewer informally calls
the candidate.

`evals/specs/real-repositories.json` is the oracle-bearing suite. Never pass that file to a
review treatment. Generate or use `current/blind-suite.json`, which excludes upstream fix
identities, expected dispositions, decisions, rationales, and other answer-bearing data.

`current/materialization.json` records the live source verification performed on
2026-08-20. All eight source checkouts were clean, every declared scope existed, and each
benchmark revision was verified as the direct parent of its bound upstream fix. This is a
source receipt, not a model-quality result.

Current materialization receipts declare `bindingKind: SOURCE_MATERIALIZATION_V1`. Their suite
hash covers only source-determining fields: repository ID, remote, benchmark revision, upstream
fix revision, and declared scope. Prompt and oracle wording changes therefore require a new blind
suite and new campaign plans, but not a dishonest claim that the unchanged source checkouts were
materialized again. Any change to a source-determining field invalidates the receipt. Historical
v1 receipts without `bindingKind` remain readable with their original full-suite hash semantics.
Historical campaigns and adjudications also remain sealed to their original suite, prompt, output,
and subject hashes; regenerating current artifacts does not relabel or upgrade that evidence.

The materializer verifies fix-parent lineage in a disposable Git repository, then independently
fetches only the benchmark parent at depth 1 into the evaluation checkout. It fails closed if the
upstream fix object is accessible or enumerable there. This separation prevents treatments from
recovering the hidden oracle through unreachable Git objects.

New receipts record `fixObjectExcluded: true`. Earlier receipts remain schema-readable historical
source evidence, but execution rejects them because they do not attest oracle-object exclusion.
They also record `evaluatorBoundary.kind: DISJOINT_COORDINATOR_ROOT_V1`. The evaluator workspace
contains only `repositories/`; oracle-bearing `suite.json` and `materialization.json` are written
to a separate, non-nested coordinator root. Planning and execution reject receipts without this
attestation, while execution and the standalone live validator reject extra evaluator-root files,
non-directory or symlinked repository roots, nested coordinator roots, and post-receipt Git-object
leakage before adapter description or provider work begins. Execution revalidates every checkout
listed in the receipt, including unselected siblings, so a partial shard cannot hide a control
artifact behind an unselected repository name. It also rejects the campaign run directory when it
contains or is contained by the evaluator root, and rejects suite, receipt, plan, adapter, skill,
verifier, or budget-ledger paths placed anywhere inside that root. Re-materialize the selected
repositories before starting any new campaign.

This is a path and artifact-delivery boundary, not an operating-system read sandbox. It prevents
the materialization coordinator and campaign run artifacts from being placed in or above the
evaluator workspace, prevents control inputs from being embedded inside it, and makes those
conditions contract-verifiable without a model call. Host enforcement must still be reported
separately; `--cd` and a read-only mutation policy alone do not prove that the process cannot read
arbitrary unrelated host paths.

The deterministic release gate, purpose-bound REAL_HOST engineering validation, and Golden
research evidence are separate contracts. Ordinary source, protocol, documentation, packaging,
or deterministic-test changes do not launch a provider campaign. A REAL_HOST plan must declare
exactly one purpose; its matrix cannot be enlarged by CLI options or resume:

| Purpose | Fixed matrix | Samples | Intended use |
| --- | --- | ---: | --- |
| `CANARY` | 1 repository x 3 treatments x 2 models x 1 | 6 | Provider, adapter, timeout, or process-lifecycle changes |
| `CORE_ITERATION` | 8 repositories x Ordinary/Evidence Loop x primary model x 1 | 16 | Routine evidence-loop, prompt, or core review-method iteration |
| `RISK_ITERATION` | 8 repositories x 3 treatments x primary model x 1 | 24 | Isolate Risk Lens marginal contribution |
| `CANDIDATE` | 8 repositories x 3 treatments x 2 models x 1 | 48 | User-facing quality-mechanism candidate; directional cross-model evidence |
| `GOLDEN` | 8 repositories x 3 treatments x 2 models x 3 | 144 | Refresh a broad, repeated quality claim with independent adjudication |

The fixed primary profile is `gpt-5.6-terra/high`; the comparison profile is
`gpt-5.6-sol/high`. A one-repetition result is directional evidence, not stability evidence.
`CANDIDATE` is mandatory before publishing a user-facing Review Craft quality-mechanism change,
but it does not itself authorize a public superiority claim. Only `GOLDEN` plus complete
human adjudication, FINAL human oracle verification, agreement gates, and stability analysis can
support a Golden claim. A model or provider update never automatically launches Golden.

```text
uv run --locked python scripts/real_repository_benchmark.py validate-suite

uv run --locked python scripts/real_repository_benchmark.py materialize \
  --workspace-root <external-empty-evaluator-directory> \
  --coordinator-root <separate-empty-coordinator-directory>

uv run --locked python scripts/real_repository_benchmark.py \
  validate-evaluator-workspace \
  --materialization <coordinator-directory>/materialization.json \
  --workspace-root <external-evaluator-directory>

uv run --locked python scripts/real_repository_benchmark.py blind-suite \
  --output <external-path>/blind-suite.json

uv run --locked python scripts/real_repository_benchmark.py plan-campaign \
  --materialization <coordinator-directory>/materialization.json \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --adapter-config <outside-adapters.json> \
  --campaign-id <immutable-campaign-id> \
  --purpose CANARY \
  --output <external-path>/campaign-plan.json

uv run --locked python scripts/real_repository_benchmark.py \
  validate-campaign-plan \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --plan <external-path>/campaign-plan.json

# Copy the exact contentSha256 printed by plan-campaign. Authorization is bound
# to that immutable plan, not merely to a path or campaign ID.
uv run --locked python scripts/real_repository_benchmark.py run-plan \
  --materialization <coordinator-directory>/materialization.json \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --workspace-root <materialized-workspace-root> \
  --adapter-config <outside-adapters.json> \
  --plan <external-path>/campaign-plan.json \
  --shard <repository-id> \
  --run-dir <external-shard-directory> \
  --budget-ledger <external-path>/campaign-budget-ledger.json \
  --authorize-plan-sha256 <campaign-plan-content-sha256> \
  --allow-partial

# GOLDEN additionally requires the same exact hash in the explicit high-cost gate:
# --allow-golden-campaign-sha256 <campaign-plan-content-sha256>

uv run --locked python scripts/real_repository_benchmark.py \
  validate-campaign-run \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --run-dir <external-shard-directory> \
  --budget-ledger <external-path>/campaign-budget-ledger.json

uv run --locked python scripts/real_repository_benchmark.py \
  merge-campaign-runs \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --plan <external-path>/campaign-plan.json \
  --run-dir <external-shard-1> \
  --run-dir <external-shard-2> \
  --budget-ledger <external-path>/campaign-budget-ledger.json \
  --output-dir <external-merged-directory>

uv run --locked python scripts/real_repository_benchmark.py validate-campaign \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --campaign <external-merged-directory>/campaign.json

uv run --locked python scripts/real_repository_benchmark.py prepare-adjudication \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --campaign <campaign.json> --output-dir <empty-adjudication-directory> \
  --adjudicator human-a --adjudicator human-b --adjudicator human-c

# Give each reviewer only packet-<id>.json and submission-<id>.json. Keep the
# coordinator mapping private. After each reviewer fills every label/rationale:
uv run --locked python scripts/real_repository_benchmark.py \
  finalize-adjudication-submission \
  --packet <packet-human-a.json> --submission <submission-human-a.json>

uv run --locked python scripts/real_repository_benchmark.py assemble-adjudication \
  --campaign <campaign.json> --mapping <coordinator-mapping.json> \
  --submission <submission-human-a.json> --submission <submission-human-b.json> \
  --submission <submission-human-c.json> \
  --kind HUMAN \
  --output <independent-adjudication.json>

uv run --locked python scripts/real_repository_benchmark.py validate-adjudication \
  --campaign <campaign.json> --adjudication <independent-adjudication.json>

# Only after the blind submissions are finalized, generate the coordinator-side
# oracle template. Fill classification and rationale, then seal and validate it.
uv run --locked python scripts/real_repository_benchmark.py prepare-oracle-assessment \
  --suite evals/specs/real-repositories.json \
  --campaign <campaign.json> --adjudication <independent-adjudication.json> \
  --verifier-id <verifier-id> --kind HUMAN \
  --output <oracle-assessment.json>

uv run --locked python scripts/real_repository_benchmark.py finalize-oracle-assessment \
  --suite evals/specs/real-repositories.json \
  --campaign <campaign.json> --adjudication <independent-adjudication.json> \
  --assessment <oracle-assessment.json>

uv run --locked python scripts/real_repository_benchmark.py validate-oracle-assessment \
  --suite evals/specs/real-repositories.json \
  --campaign <campaign.json> --adjudication <independent-adjudication.json> \
  --assessment <oracle-assessment.json>

uv run --locked python scripts/real_repository_benchmark.py analyze-stability \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --campaign <campaign.json> --adjudication <independent-adjudication.json> \
  --oracle-assessment <oracle-assessment.json> \
  --output <stability.json>

uv run --locked python scripts/real_repository_benchmark.py assess-campaign-promotion \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --plan <campaign-plan.json> --campaign <campaign.json> \
  --budget-ledger <campaign-budget-ledger.json> \
  --adjudication <independent-adjudication.json> \
  --oracle-assessment <oracle-assessment.json> \
  --stability-report <stability.json> \
  --output <promotion-receipt.json>

# Provider-free release evidence check. This revalidates every bound artifact and
# requires the exact assessed Review Craft source content to still be present.
uv run --locked python scripts/real_repository_benchmark.py validate-quality-release \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --plan <campaign-plan.json> --campaign <campaign.json> \
  --budget-ledger <campaign-budget-ledger.json> \
  --adjudication <independent-adjudication.json> \
  --oracle-assessment <oracle-assessment.json> \
  --stability-report <stability.json> \
  --receipt <promotion-receipt.json>
```

Purpose policy fixes a 300-second first-item deadline, a 900-second overall sample deadline,
120/240-second inactivity warning/diagnostic thresholds, and fail-closed cumulative timeout,
unknown-usage, artifact-invalid, recovered-stall, token, and wall-time limits. A first-item timeout
is recorded as `BEFORE_FIRST_ITEM`; a later overall timeout is `AFTER_FIRST_ITEM`. The runner
preserves the original timed-out sample and its lifecycle evidence. Diagnostic reruns are new
artifacts and never replace a predecessor. Prompted output invariants are not hidden
post-processing rules:
`BLOCKED` probes must use null severity, and every reported location must stay inside the
declared benchmark scope.

The runner stores exact
prompt/output hashes, usage, wall time, adapter provenance, completion state, and before/after
Git state; any target mutation fails the sample. Adapter processes run through the canonical
process-lifecycle boundary. A purpose plan requires `review-craft.eval-timeout-control.v2`, so the
runner passes both deadlines and reserves a separate bounded finalization grace period.
The Codex adapter owns termination of its child process tree, then writes final progress, usage,
tool-trace, and post-exit isolation sidecars before returning the dedicated timeout exit code; the
outer runner timeout remains a failsafe for an adapter that cannot finalize. The Codex adapter
streams JSONL and atomically checkpoints sanitized tool traces and usage while it runs. A timed
out sample therefore retains partial stdout and completed tool-call evidence when available;
token counts remain null when no complete host usage event was emitted. When available, usage
retains input, cached-input, cache-write-input, output, reasoning-output, and total tokens instead
of collapsing every host-reported component into one number.

Codex adapter v6 also emits `review-craft.eval-isolation-receipt.v1` for every invocation. The
receipt captures pre-run, post-start, and post-exit Codex-home fingerprints, separating managed
`.system` files from user-installed extensions. A required receipt that is missing, malformed,
unavailable, or drifted changes a non-timeout sample to `FAILED/ARTIFACT_INVALID`; the runner
neither deletes nor repairs that home automatically. A timeout with an unavailable post-exit
capture remains `TIMED_OUT/TIMEOUT` so the capture gap cannot overwrite the primary lifecycle
result, while an explicit system or user-extension drift remains artifact-invalid. The raw
isolation sidecar preserves either condition. This receipt verifies the declared Codex-home surface
only. It is not evidence that the host enforced network denial or an operating-system filesystem
sandbox.

The CANARY token envelope is calibrated above the retained six-cell REAL_HOST smoke rather than
below it: 300,000 input / 350,000 total per sample, 750,000 input / 800,000 total for the shard,
and 800,000 total for the campaign. A future budget change requires a new purpose-policy hash; an
over-budget final sample remains a safety stop and never becomes `SCHEDULE_COMPLETE`.

All REAL_HOST execution uses a purpose-bound content plan and `run-plan`; the unbound legacy
`run` command fails closed. A v2 plan binds purpose policy, the exact fixed matrix, suite, blind
suite, materialization receipt, adapter configuration, live REAL_HOST descriptions, prompt hashes,
budgets, and the exact `skills/review-craft/` source content. `run-plan` requires
`--authorize-plan-sha256` to match that plan. Golden additionally requires
`--allow-golden-campaign-sha256` with the same hash. Source drift between planning and execution
fails before provider work or run-artifact creation.

Campaign-plan v1 remains backward-readable for historical validation only. It cannot be started,
resumed, or implicitly upgraded. Purpose v2 rejects repository, treatment, model-count,
repetition, timeout, or budget deviations rather than treating them as flexible CLI overrides.
Every treatment also receives the
same bounded-output instruction: inspect size first, prefer targeted matches and line windows,
and keep a single command below roughly 200 lines or 32 KiB.

Each sample commits an atomic `checkpoint.json` marker containing the exact content-bound run
state before replacing the `campaign.json` and `run-state.json` mirrors. Any uncommitted sample
directory is preserved and blocks overwrite rather than being silently replayed. Failed and
timed-out samples remain immutable evidence. An operator interrupt seals the attempt as
`INTERRUPTED/OPERATOR_INTERRUPT`, updates the shared ledger and checkpoint, and cannot be resumed;
diagnosis or retry requires a new content-bound attempt so predecessor evidence is not overwritten.

Every shard in one plan must use the same external `--budget-ledger`. The runner holds an
exclusive lock for the complete shard invocation, reserves one shard before creating its run
state, and refuses a new shard while the latest shard is still `RUNNING`. The content-bound
ledger aggregates reported tokens, unknown-usage samples, artifact-invalid samples, active
runner time, per-model-profile timeout and recovered-inactivity counts, plus the consecutive
infrastructure-failure tail across the serial shard order. A resume repairs the latest ledger contribution from the
committed checkpoint; it cannot recreate a missing ledger or switch to an older shard. Token
usage is known only after an adapter checkpoint. Per-sample limits therefore detect an oversized
completed attempt; they do not terminate a model request mid-stream. Every budget, lifecycle,
cleanup, source-mutation, credential, and integrity stop takes precedence over
`SCHEDULE_COMPLETE`, including when the final scheduled sample caused the stop. This prevents an
exhausted matrix from being mislabeled complete at the same instant that its safety budget failed.

`merge-campaign-runs` rejects running states, duplicate shards, duplicate cells, plan drift,
budget-ledger drift, model-configuration drift, and samples outside the plan. Its receipt binds
the exact shared ledger and copies it into the merge directory. A merged campaign becomes
`COMPLETED` only when every cell in the fixed purpose matrix completed successfully.

`assess-campaign-promotion` generates a deterministic, content-bound receipt. Canary promotion is
structural: exact matrix completion, valid plan/campaign/ledger bindings, known usage, clean
process-tree cleanup, and no recovered startup stall. All higher purposes additionally require
adjudication.v3 plus a FINAL oracle assessment. Treatment comparisons fail on quality regression,
false-positive regression, or excessive token/wall-time ratios; Candidate and Golden require a
strict adjudicated Review Craft gain over ordinary review. Golden additionally requires human-only
adjudication and oracle verification plus the agreement threshold. A blocked receipt remains
useful diagnostic evidence but cannot be presented as a quality pass. `validate-quality-release`
is provider-free and revalidates an eligible Candidate/Golden receipt against its complete artifact
set and the exact currently packaged Review Craft source content.

The current adjudication workflow uses v2 packets and submissions to produce a v3 assembled
artifact covering every probe response plus every additional finding from completed samples.
Reviewer packets use reviewer-specific opaque item IDs, bind the exact subject content, and omit
sample, treatment, model, and repetition identities. Packet order is independently deterministic
per reviewer. Probe responses are judged on disposition, decision, severity, evidence, and
rationale; additional findings are judged on actionability, decision, severity, and evidence. An
overall label is derived from those component verdicts and cannot be filled inconsistently.
`assemble-adjudication` requires an explicit uniform `--kind`: use `HUMAN` only for actual
independent human reviewers and `AGENT_ASSISTED` for isolated model-assisted adjudicators.
Agent-assisted labels contribute to adjudicated false-positive analysis and the explicit
`adjudicatorAgreement` metric, but never populate `humanAgreement` or satisfy the independent
human adjudication completion gate.
The coordinator-only mapping is required to assemble at least two completed submissions; three
reviewers are recommended so one unavailable reviewer does not collapse the batch. Any decisive
disagreement remains `SPLIT` with no resolved label, including a 2:1 split; majority voting is not
treated as verified semantic truth. A blank template or a single reviewer cannot produce a valid
adjudication. Legacy v1 and v2 assembled adjudication remains validation-compatible for historical
campaigns; v1 covers additional findings only. Standalone validation of a v3 artifact checks its
campaign/subject hashes and deterministic resolutions, but reopening the private mapping and
submission files is still required for a full provenance audit.

The stability report exposes two distinct root-cause signals. `rootCauseOverlap` preserves the
raw model-authored keys for continuity. `rootCauseIdentityOverlap` removes wording drift by using
the declared probe ID for controlled probes and a hash of the exact evidence-location set for
additional findings. Location-range drift can still reduce the latter, intentionally: the
normalizer does not infer unrecorded semantics. Conversely, two additional findings on the exact
same location set can over-match, so this metric remains a location-backed proxy rather than
human root-cause equivalence. Historical stability reports without the additive identity metric
remain validation-compatible.

Oracle assessment is deliberately post-blind and coordinator-side. It binds every completed
`REAL_FINDING` response to the exact hidden suite oracle and its adjudicated response validity,
then classifies the root cause as `EXACT_ORACLE_MATCH`, `ALTERNATIVE_VALID_FINDING`, `MISSED`, or
`UNRESOLVED`. These are separate claims: a response can be adjudicated correct while missing the
bound upstream root cause, or identify the bound root cause while other response components are
incorrect. Oracle-aware stability uses the v2 report schema and publishes distinct
`responseValidityRate`, `exactOracleRecall`, `alternativeValidFindingRate`, `oracleMissRate`,
`oracleResolutionRate`, and `oracleRootCauseOverlap` metrics. The legacy probe-ID-backed
`rootCauseIdentityOverlap` remains a response-stability proxy and must not be reported as exact
oracle recovery. Agent-assisted oracle verification remains an explicit completion limitation and
does not count as human verification. The suite v1 `protocol.metrics` list remains the historical
base set so existing campaign suite hashes stay valid; the oracle metrics are additive only in an
assessment-bound stability v2 report.
