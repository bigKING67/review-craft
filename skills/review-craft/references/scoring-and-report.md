# Scoring, Evidence, and Report Contract

## Contents

1. Dimensions
2. Deductions
3. Evidence levels
4. Confidence
5. Report projection

## Dimensions

Use the fixed 100-point model:

- correctness and stability: 20;
- architecture and module design: 20;
- long-term maintainability: 15;
- performance and resources: 15;
- code quality and consistency: 10;
- testing and engineering systems: 10;
- dependencies and basic security governance: 5;
- repository structure, documentation, and developer experience: 5.

Map observability and recovery to correctness/testing according to their impact.
Do not silently change weights for a preferred architecture.

## Deductions

Start from the dimension maximum and deduct only for a validated finding or explicit
evidence gap. Cite IDs. The finalizer checks that deduction points equal maximum
minus awarded points and computes the total.

Only the canonical finalizer may produce the numeric total. When the runtime or
finalizer is unavailable, report the evidence level and qualitative gaps without
a numeric table, approximate total, or alternate weighting model.

Use a provisional score when coverage or candidates remain open. Do not deduct for
an explicit non-goal. Do not inflate a score merely because source files, schemas,
or tests exist.

## Evidence levels

- E0: description or documentation only; no formal score.
- E1: current source and configuration reviewed.
- E2: relevant checks, tests, or builds executed.
- E3: critical runtime, benchmark, profile, or trace evidence captured.
- E4: clean and deployment-representative reproduction.

Scores of 95 or more require E3. Scores of 98-100 require E4. These gates prevent
source completeness or self-evaluation from masquerading as operational proof.

## Confidence

Confidence reflects coverage, validation quality, evidence level, and unresolved
conditions. It is not a stylistic expression of certainty. Record low confidence
when critical paths, environments, or scale cannot be reproduced.

## Report projection

The report is a deterministic view of canonical JSON. It contains the executive
summary, eight scores, full findings, dispositions, target design, Phase 0-4 plan,
and final five answers. Edit JSON and rerun finalization instead of patching the
Markdown.
