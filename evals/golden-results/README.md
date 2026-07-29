# Golden Results

This directory intentionally contains no claimed host result yet. The executable runner
stores real and synthetic runs outside the repository by default:

```text
uv run --locked python scripts/run_evals.py run ...
uv run --locked python scripts/run_evals.py validate --run-dir <run-dir>
```

Only copy a run here when `result.json` validates with `goldenEligible: true`. That gate
requires a complete suite, a clean source tree, and `REAL_HOST` adapter provenance.
Synthetic contract outputs, partial suites, failed hosts, and unavailable hosts are never
golden evidence. A content hash detects later artifact or metadata drift; it is not a
cryptographic identity or authenticity signature.
`REAL_HOST` is a trusted adapter declaration, not remote attestation; only execute and
publish results from adapters whose implementation and invocation you have reviewed.
The Codex adapter additionally requires explicit provider provenance and records the
`CODEX_HOME` skill/plugin surface. Use an auth-only isolated home for publishable runs.

`goldenEligible: true` proves execution completeness and provenance gates, not semantic
finding correctness. Do not publish the run's structural recall, precision, or
false-positive fields as semantic quality claims without a separately validated
`review-craft.eval-adjudication-result.v1` artifact. Keep unresolved adjudications explicit;
do not replace `null` semantic metrics with inferred values.
