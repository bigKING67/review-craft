# Golden Results

The executable runner stores real and synthetic runs outside the repository by default:

```text
uv run --locked python scripts/run_evals.py run ...
uv run --locked python scripts/run_evals.py validate --run-dir <run-dir>
```

Do not copy raw runs into this directory. Export a sanitized snapshot only when both matched
runs validate with `goldenEligible: true` and both content-bound semantic adjudications are
complete:

```text
uv run --locked python scripts/run_evals.py export-golden \
  --review-craft-run <review-run> \
  --baseline-run <ordinary-prompt-run> \
  --review-craft-adjudication <review-adjudication-result.json> \
  --baseline-adjudication <baseline-adjudication-result.json> \
  --output <snapshot.json>
```

The gate requires complete suites, matched host and isolation metadata, clean and stable
source, `REAL_HOST` adapter provenance, and zero unresolved semantic cases. Synthetic
contract outputs, partial suites, failed hosts, unavailable hosts, and partial adjudications
are never Golden evidence. Export is deterministic for identical validated inputs.

Track only `snapshot.json` and a concise local README. The snapshot intentionally excludes
raw stdout/stderr, prompts, fixture copies, adapter argv, provider base URLs, credentials,
and absolute paths. Its hashes bind the external evidence but do not make the snapshot a
self-contained reproduction bundle or an authenticity signature. Repository validation
checks every tracked snapshot against `eval-golden-snapshot.schema.json` and recomputes its
canonical content hash.

Snapshots created from eval run v3 may include sanitized aggregate usage under each
treatment's structural metrics. The aggregate identifies complete, partial, or unavailable
coverage and includes only reported-case totals. Historical run v2 snapshots remain valid
without a usage object; absence means unmeasured, not zero.

`REAL_HOST` is a trusted adapter declaration, not remote attestation; only execute and
publish results from adapters whose implementation and invocation you have reviewed.
The Codex adapter additionally requires explicit provider provenance and records the
`CODEX_HOME` skill/plugin surface. Use an auth-only isolated home for publishable runs.

`goldenEligible: true` proves execution completeness and provenance gates, not semantic
finding correctness. Do not publish the run's structural recall, precision, or
false-positive fields as semantic quality claims without a separately validated
`review-craft.eval-adjudication-result.v1` artifact. Keep unresolved adjudications explicit;
do not replace `null` semantic metrics with inferred values.

`AGENT_ASSISTED` means the semantic classification was performed with model assistance. It
must not be described as independent human adjudication. Each result directory documents
its environment, narrow supported claim, measured cost, and remaining limits.
