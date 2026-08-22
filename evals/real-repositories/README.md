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

`evals/specs/real-repositories.json` is the oracle-bearing suite. Never pass that file to a
review treatment. Generate or use `current/blind-suite.json`, which excludes upstream fix
identities, expected dispositions, decisions, rationales, and other answer-bearing data.

`current/materialization.json` records the live source verification performed on
2026-08-20. All eight source checkouts were clean, every declared scope existed, and each
benchmark revision was verified as the direct parent of its bound upstream fix. This is a
source receipt, not a model-quality result.

The benchmark is not Golden until all declared treatments, repetitions, and model
configurations complete, the outputs are independently adjudicated, and the stability
report validates. Do not use the materialization receipt alone to claim review accuracy,
cross-model stability, or human agreement.

```text
uv run --locked python scripts/real_repository_benchmark.py validate-suite

uv run --locked python scripts/real_repository_benchmark.py materialize \
  --workspace-root <external-empty-directory>

uv run --locked python scripts/real_repository_benchmark.py blind-suite \
  --output <external-path>/blind-suite.json

uv run --locked python scripts/real_repository_benchmark.py plan-campaign \
  --materialization evals/real-repositories/current/materialization.json \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --adapter-config <outside-adapters.json> \
  --campaign-id <immutable-campaign-id> \
  --repetitions 3 --timeout-seconds 1800 \
  --soft-wall-seconds 64800 --hard-wall-seconds 86400 \
  --hard-reported-token-ceiling 60000000 \
  --hard-reported-input-token-ceiling-per-sample 1250000 \
  --hard-reported-token-ceiling-per-sample 1500000 \
  --hard-reported-input-token-ceiling-per-shard 7000000 \
  --hard-reported-token-ceiling-per-shard 8000000 \
  --max-consecutive-infrastructure-failures 2 \
  --max-unknown-usage-samples 1 \
  --max-timed-out-samples-per-model-profile 1 \
  --max-artifact-invalid-samples 1 \
  --inactivity-warning-seconds 300 \
  --inactivity-diagnostic-seconds 600 \
  --max-recovered-inactivity-samples-per-model-profile 2 \
  --output <external-path>/campaign-plan.json

uv run --locked python scripts/real_repository_benchmark.py \
  validate-campaign-plan \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --plan <external-path>/campaign-plan.json

# Execute one natural repository shard at a time. Add --resume only to an
# interrupted RUNNING state; terminal budget/circuit stops require a new plan.
uv run --locked python scripts/real_repository_benchmark.py run-plan \
  --materialization evals/real-repositories/current/materialization.json \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --workspace-root <materialized-workspace-root> \
  --adapter-config <outside-adapters.json> \
  --plan <external-path>/campaign-plan.json \
  --shard <repository-id> \
  --run-dir <external-shard-directory> \
  --budget-ledger <external-path>/campaign-budget-ledger.json \
  --allow-partial

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

uv run --locked python scripts/real_repository_benchmark.py analyze-stability \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --campaign <campaign.json> --adjudication <independent-adjudication.json> \
  --output <stability.json>
```

New plans stop before scheduling another sample after the cumulative unknown-usage,
artifact-invalid, or any single model-profile timeout budget is reached. They also capture a
live progress diagnostic after 600 seconds without a first semantic item. A recovered diagnostic
stall is counted by model profile: the first continues, while the second stops subsequent
scheduling. These fail-closed limits apply across repository shards through the shared budget
ledger; they do not shorten or increase the per-sample timeout. Prompted output invariants are
not hidden post-processing rules:
`BLOCKED` probes must use null severity, and every reported location must stay inside the
declared benchmark scope.

The full matrix is eight repositories by three treatments by at least two real-host model
configurations by three repetitions. `--repository`, `--treatment`, and `--repetitions` may
produce a labeled partial smoke, but partial output is never Golden. The runner stores exact
prompt/output hashes, usage, wall time, adapter provenance, completion state, and before/after
Git state; any target mutation fails the sample. Adapter processes run through the canonical
process-lifecycle boundary, so timeout terminates the inherited process tree. The Codex adapter
streams JSONL and atomically checkpoints sanitized tool traces and usage while it runs. A timed
out sample therefore retains partial stdout and completed tool-call evidence when available;
token counts remain null when no complete host usage event was emitted. When available, usage
retains input, cached-input, cache-write-input, output, reasoning-output, and total tokens instead
of collapsing every host-reported component into one number.

Codex adapter v6 also emits `review-craft.eval-isolation-receipt.v1` for every invocation. The
receipt captures pre-run, post-start, and post-exit Codex-home fingerprints, separating managed
`.system` files from user-installed extensions. A required receipt that is missing, malformed,
unavailable, or drifted changes the sample to `FAILED/ARTIFACT_INVALID`; the runner neither deletes
nor repairs that home automatically. This receipt verifies the declared Codex-home surface only.
It is not evidence that the host enforced network denial or an operating-system filesystem sandbox.

High-cost campaigns must use a content-bound plan and `run-plan`. The legacy `run` command
remains available for compatibility and bounded diagnostics, but it does not provide resumable
plan execution or campaign-level budget enforcement. A plan binds the suite, blind suite,
materialization receipt, adapter configuration, live REAL_HOST descriptions, deterministic
sample order, exact prompt hash for every cell, per-sample timeout, global token and wall-time
ceilings, and infrastructure circuit-breaker threshold. New plans also bind cumulative unknown-usage, artifact-invalid,
and per-model-profile timeout ceilings, plus inactivity warning/diagnostic thresholds and the
recovered-inactivity ceiling. They additionally bind reported input/total-token ceilings per
sample and per natural repository shard. The defaults are 1.25M input and 1.5M total tokens per
sample, plus 7M input and 8M total tokens per repository shard; these are safety stops, not target
budgets or quality claims.

Campaign-plan schemas remain backward-readable so historical evidence can still be validated.
`run-plan` is stricter: plans without the current cumulative failure, inactivity, per-sample and
per-shard token budgets, or without every prompt hash are validation-only legacy data and must be
regenerated. This prevents an old global-ceiling-only plan from bypassing later safety controls
or mixing prompt revisions across resumed repository shards. Every treatment also receives the
same bounded-output instruction: inspect size first, prefer targeted matches and line windows,
and keep a single command below roughly 200 lines or 32 KiB.

Each sample commits an atomic `checkpoint.json` marker containing the exact content-bound run
state before replacing the `campaign.json` and `run-state.json` mirrors. Resume trusts the
checkpoint state only when its campaign hash matches the committed campaign, and repairs a
stale state mirror from that marker. Any uncommitted sample directory is preserved and blocks
overwrite rather than being silently replayed. Failed and timed-out samples remain immutable
evidence. The default natural shard is one repository, or 18 cells for the declared
8 x 3 x 2 x 3 matrix.

Every shard in one plan must use the same external `--budget-ledger`. The runner holds an
exclusive lock for the complete shard invocation, reserves one shard before creating its run
state, and refuses a new shard while the latest shard is still `RUNNING`. The content-bound
ledger aggregates reported tokens, unknown-usage samples, artifact-invalid samples, active
runner time, per-model-profile timeout and recovered-inactivity counts, plus the consecutive
infrastructure-failure tail across the serial shard order. A resume repairs the latest ledger contribution from the
committed checkpoint; it cannot recreate a missing ledger or switch to an older shard. Token
usage is known only after an adapter checkpoint, so every reported-token ceiling stops before the
next sample and may be exceeded by the final completed sample's reported usage. Per-sample limits
therefore detect an oversized completed attempt; they do not terminate a model request mid-stream.
All budget and circuit-breaker signals are admission stops: when the final scheduled attempt
reaches a threshold, the exhausted shard remains `COMPLETED/SCHEDULE_COMPLETE`, while the shared
ledger retains the threshold and prevents a later shard from admitting its first sample. Safety
failures such as source mutation or credential exposure still fail an exhausted shard.

`merge-campaign-runs` rejects running states, duplicate shards, duplicate cells, plan drift,
budget-ledger drift, model-configuration drift, and samples outside the plan. Its receipt binds
the exact shared ledger and copies it into the merge directory. A merged campaign becomes
`COMPLETED` only when every planned full-matrix cell completed successfully.

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
