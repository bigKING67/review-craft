# Remediation Safety Evaluation

This repository-only harness measures whether review-driven source changes resolve known
defect claims, preserve already-correct claims, mutate clean cases, or introduce regressions
over repeated rounds. It is development governance, not part of the installable
`skills/review-craft/` runtime.

The v1 protocol uses three isolated arms:

1. `ORDINARY_NAIVE_LOOP`;
2. `REVIEW_CRAFT_UNGATED_LOOP`;
3. `REVIEW_CRAFT_EVIDENCE_GATED_LOOP`.

The gated arm runs hidden baseline oracles before allowing repair and stops when every
defect and preservation claim passes. Ungated arms may continue to the configured round
limit so cumulative degradation remains observable. Every arm receives an independent
temporary fixture copy. Raw prompts, source snapshots, diffs, oracle output, usage, and tool
traces remain in an external run directory.

Every selected case baseline must match its declared claim states before the first reviewer
invocation. The runner treats actual source diffs as authoritative when a fixer's
`claimedPaths` disagrees. `repairSuccessRate` uses all fixer invocations as its denominator,
so an ungated follow-up `NO_CHANGE` remains visible rather than being discarded.

```text
uv run --locked python scripts/run_evals.py run-remediation-safety \
  --rounds 3 \
  --adapter-command python3 scripts/codex_eval_adapter.py \
  --model <model> --reasoning <reasoning>

uv run --locked python scripts/run_evals.py validate-remediation-safety \
  --run-dir <run-directory>
```

A completed run reports what happened; it does not imply that a treatment was safe or
superior. Model failures, oracle failures, sandbox violations, and invalid artifacts remain
explicit infrastructure failures. Source regressions are valid experimental outcomes and
must not be erased by a later recovery.
