# Comparative Evaluation

Compare Review Craft with an ordinary repository-review prompt and the host's native
Codex Review only on matched repository, revision, model, reasoning, scope, and output
constraints. Each normalized case output is limited to one primary candidate finding so
case-level semantic adjudication remains unambiguous. Measure structural detection, raw
false positives, location overlap, evidence presence, decision quality, rewrite restraint,
runtime, token usage, and completed tool calls from matched, schema-valid outputs.
`candidateRecallPercent` means that a
positive fixture produced a finding; it does not prove that the evidence matches the seeded
issue. The scorer does not claim to automate semantic evidence quality, review coverage
honesty, remediation completeness, or the stability of a cost ratio from one run.

Eval run v3 stores an adapter-neutral usage record for every case. `AVAILABLE` records keep
input, cached-input, cache-write-input, output, reasoning-output, total token, turn, and
completed tool-call counts. `totalTokens` is input plus output; cached input and reasoning
output are reported subcounts and are not added again. Aggregate `reportedUsage` covers only
`reportedCases`. `PARTIAL` and `UNAVAILABLE` results retain fixed unavailable reasons and
use `null`, never fabricated zeroes, for unknown costs.

The separate eval run v4 four-arm ablation tests ordinary review, adversarial review,
risk-lens-guided adversarial review, and a Review Craft evidence loop over six matched
positive/negative fixture pairs. It uses a Latin-square treatment order, content-bound
sanitized tool traces, hidden behavioral verifiers, treatment-blinded adjudication, and
fixed A-to-B, B-to-C, C-to-D, and A-to-D comparisons. This is an evaluation harness
capability, not an installable runtime feature. See `../ablation-results/README.md` for its
execution, export, and claim boundaries.

Codex real-host runs require `HOME` and `CODEX_HOME` to resolve to the same auth-only
directory. This prevents `$HOME/.agents` skills or plugins from contaminating a nominally
ordinary prompt while retaining authentication.

Run `REVIEW_CRAFT` and `ORDINARY_PROMPT` as separate full-suite runs with the same host,
version, model, reasoning profile, revision, case timeout, and case selection. Do not call
the result matched if any of those fields differ. Provider and Codex-home isolation metadata
must also match; external credentials are never included. `CODEX_NATIVE_REVIEW` remains
reserved until a diff-aware adapter can provide equivalent target and scope semantics.

Use the deterministic matcher rather than comparing filenames or scores by inspection:

```text
uv run --locked python scripts/run_evals.py compare \
  --review-craft-run <review-run> \
  --baseline-run <ordinary-prompt-run> \
  --output <structural-comparison.json>
```

`comparativeEligible` is true only when both input runs independently pass their full-suite,
clean-source, real-host golden gates. This form compares deterministic structural metrics
only and emits `semantic: null`.

Before publishing semantic recall, precision, or false-positive claims, create and validate
a content-bound adjudication for each run:

```text
uv run --locked python scripts/run_evals.py prepare-adjudication \
  --run-dir <run-dir> \
  --kind HUMAN \
  --protocol <protocol-id> \
  --output <input.json>

uv run --locked python scripts/run_evals.py adjudicate \
  --run-dir <run-dir> \
  --adjudication <input.json> \
  --output <result.json>

uv run --locked python scripts/run_evals.py validate-adjudication \
  --run-dir <run-dir> \
  --result <result.json>
```

Bind both validated adjudications into the matched comparison rather than comparing their
metrics by inspection:

```text
uv run --locked python scripts/run_evals.py compare \
  --review-craft-run <review-run> \
  --baseline-run <ordinary-prompt-run> \
  --review-craft-adjudication <review-adjudication-result.json> \
  --baseline-adjudication <baseline-adjudication-result.json> \
  --output <semantic-comparison.json>
```

Both adjudications must use the same `kind` and `protocol`. An `AGENT_ASSISTED`
adjudication is explicit model-assisted evaluation, not independent human review. Partial
or unresolved adjudication remains visible through nullable semantic metrics and makes the
semantic comparison ineligible for Golden export.

Every adjudicated case binds the run ID, run content hash, and normalized output hash. The
outcome must be one of `SEEDED_ISSUE_MATCH`, `OTHER_VALID_FINDING`, `FALSE_POSITIVE`, `MISS`,
`NO_FINDING_CORRECT`, or `UNRESOLVED`. A valid finding in a nominally negative fixture is
reported as fixture contamination and excluded from the clean-negative FPR denominator.
An unresolved detected finding keeps semantic precision at `null`; an unresolved negative
case keeps FPR at `null`. An unresolved decision disposition independently keeps decision
accuracy at `null` rather than hiding the uncertainty.

See `../golden-results/README.md` for the sanitized, deterministic Golden export contract.

Do not compare Review Craft with Codex Security by vulnerability count. Future security
integration should measure identity, severity, confidence, and provenance preservation,
plus zero unsupported security overclaims.
