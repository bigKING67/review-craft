# Comparative Evaluation

Compare Review Craft with an ordinary repository-review prompt and the host's native
Codex Review only on matched repository, revision, model, reasoning, scope, and output
constraints. Measure recall, precision, false positives, location accuracy, evidence
presence, decision quality, rewrite restraint, and runtime from matched, schema-valid
outputs. The v1 scorer does not claim to automate semantic evidence quality, review
coverage honesty, remediation completeness, or cost. Those require a separately recorded
human or host-specific validation protocol.

Run `REVIEW_CRAFT` and `ORDINARY_PROMPT` as separate full-suite runs with the same host,
version, model, reasoning profile, revision, case timeout, and case selection. Do not call
the result matched if any of those fields differ. Provider and Codex-home isolation
metadata must also match; external credentials are never included. `CODEX_NATIVE_REVIEW` remains reserved
until a diff-aware adapter can provide equivalent target and scope semantics.

Use the deterministic matcher rather than comparing filenames or scores by inspection:

```text
uv run --locked python scripts/run_evals.py compare \
  --review-craft-run <review-run> \
  --baseline-run <ordinary-prompt-run>
```

`comparativeEligible` is true only when both input runs independently pass their full-suite,
clean-source, real-host golden gates.

Do not compare Review Craft with Codex Security by vulnerability count. Future security
integration should measure identity, severity, confidence, and provenance preservation,
plus zero unsupported security overclaims.
