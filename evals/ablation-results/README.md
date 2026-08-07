# Review Feedback Ablation Results

The active v2 protocol produces sanitized snapshots for three treatments:

1. `ORDINARY_PROMPT`
2. `RISK_LENS_REVIEW`
3. `REVIEW_CRAFT_EVIDENCE_LOOP`

The experiment compares ordinary review, project-specific attention guidance, and the
complete Review Craft evidence loop. The B-to-C delta adds both Review Craft skill
instructions and verifier feedback, so it does not isolate the independent effect of external
evidence. The harness does not add Risk Lens injection, verifier execution, or automatic
self-correction to the installable `skills/review-craft/` runtime. Generic adversarial wording
is not an active treatment. The tracked v0.3 two-arm Golden result remains a separate
historical artifact under `../golden-results/705dbac-gpt-5.6-sol/`.

## Published snapshot

- [`13ad6f2-gpt-5.6-sol/`](13ad6f2-gpt-5.6-sol/) is the frozen four-arm v1 snapshot. Its
  adversarial-only arm regressed against ordinary review and is retained only as a historical
  negative control. The combined risk-lens-plus-adversarial arm is also historical because
  it cannot isolate which instruction caused the observed result. The evidence loop had the
  strongest semantic metrics and the highest duration, token, and tool-call cost. See the
  result README for exact metrics, attempt lineage, integrity hashes, and limitations.

No v2 three-arm result is published yet. `RISK_LENS_REVIEW` and the high-assurance evidence
loop remain evaluation candidates until a new matched run is completed and adjudicated.

An earlier full-suite attempt ended `PARTIAL` after a provider stream failure and was not
adjudicated or exported. Published snapshots must not conceal, repair, or overwrite such
failed attempts; a later run is a new independent attempt.

Keep raw runs, prompts, fixture copies, stdout/stderr, tool traces, and adjudication working
files outside the repository. A new run uses the content-bound three-arm v2 protocol and
then receives one treatment-blinded semantic adjudication. The validator continues to
accept sealed v1 artifacts without making their retired treatments runnable again:

```text
uv run --locked python scripts/run_evals.py run-ablation \
  --output-root <external-directory> \
  --adapter-command <adapter argv>

uv run --locked python scripts/run_evals.py validate-ablation \
  --ablation-dir <ablation-directory>

uv run --locked python scripts/run_evals.py prepare-ablation-adjudication \
  --ablation-dir <ablation-directory> \
  --kind HUMAN \
  --protocol <protocol-id> \
  --bundle-output <blind-bundle.json> \
  --output <adjudication-input.json>

uv run --locked python scripts/run_evals.py adjudicate-ablation \
  --ablation-dir <ablation-directory> \
  --bundle <blind-bundle.json> \
  --adjudication <adjudication-input.json> \
  --output <adjudication-result.json>
```

`compare-ablation` binds the manifest, three child runs, structural metrics, adjudication,
semantic metrics, and the fixed A-to-B, B-to-C, and A-to-C deltas. Cost ratios use
validated aggregate duration, total-token, and completed-tool-call counts; unavailable or
zero-denominator costs stay `null`.

```text
uv run --locked python scripts/run_evals.py compare-ablation \
  --ablation-dir <ablation-directory> \
  --bundle <blind-bundle.json> \
  --adjudication-result <adjudication-result.json> \
  --output <comparison.json>
```

Only `export-ablation` may create a trackable `snapshot.json`. It requires all three child
runs to be full-suite, clean-source, source-stable, `REAL_HOST`, usage-complete, and
semantically adjudicated. The ordinary and risk-lens arms must have zero verifier
executions; the evidence-loop arm must execute every bound verifier successfully. The first
two arms receive an empty staged skill directory and no verifier directory, while the third
arm receives the bound Review Craft skill and verifiers. The snapshot excludes provider base URLs, adapter argv, raw
prompts, stdout/stderr, raw tool output, artifact paths, and absolute paths.

For the Codex adapter, set both `HOME` and `CODEX_HOME` to the same auth-only directory.
The adapter rejects a split home and scans both Codex-native and `$HOME/.agents` skill/plugin
locations so an eval arm cannot silently inherit user review instructions.

```text
uv run --locked python scripts/run_evals.py export-ablation \
  --ablation-dir <ablation-directory> \
  --bundle <blind-bundle.json> \
  --adjudication-result <adjudication-result.json> \
  --output <snapshot.json>
```

One complete run supports only a narrow statement about the bound suite, prompts, host,
model, reasoning profile, and adjudication protocol. It does not establish rerun stability,
cross-model or cross-repository generality, or a universal causal gain from self-correction.
The adjudication bundle hides treatment labels and raw prompts, but outputs and tool traces
can expose intervention characteristics, so it is treatment-label-blinded rather than a
guarantee that the evaluator cannot infer the arm.
