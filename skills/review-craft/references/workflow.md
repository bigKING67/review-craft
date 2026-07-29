# Standard Review Workflow

## Contents

1. Preflight
2. Inventory
3. Evidence collection
4. Review dimensions
5. Coverage closure
6. Completion

## Preflight

Bind every run to the real repository state. Record revision, branch, dirty state,
source fingerprint, configuration fingerprint, and the exact scope. When the
repository changes after preflight, start a new run rather than mixing evidence.

## Inventory

Use the preflight inventory as the only file worklist. Do not replace it with a
handwritten sample. Tracked files remain in scope even when ignored. Do not follow
symlinks outside the repository root.

Classify generated and vendored content only through explicit patterns or reliable
build/package facts. A large or unfamiliar file is not automatically generated.

`COVERED_BY_PARENT` is appropriate for tightly coupled files reviewed through one
parent artifact, such as generated type declarations paired with their generator.
Record the parent path and evidence. It is not a shortcut for skipping directories.

## Evidence collection

Start with the narrowest commands that validate the critical path. Expand from
targeted tests to type checks, builds, integrations, benchmarks, profiles, or smoke
tests according to risk. Preserve failures; do not rerun until green and omit the
initial result.

Summarize command output in the report. Keep full stdout and stderr in the evidence
directory. Never claim a command was run without a receipt.

## Review dimensions

Review in this order so correctness constrains later design advice:

1. correctness, data integrity, and failure recovery;
2. architecture, boundaries, dependencies, state, and data flow;
3. maintainability, duplication, abstractions, naming, and change amplification;
4. performance and resource behavior;
5. tests and validation balance;
6. build, CI, release, dependencies, and basic security posture;
7. observability, documentation, repository structure, and developer experience.

Do not let a directory preference override a stable module boundary. Do not let a
performance idea override correctness or measurement.

## Coverage closure

For every file, record the disposition, reason, and evidence references. Keep
unreadable or deferred files explicit. A review can be useful while incomplete,
but its score remains provisional and its delivery must name the blind spots.

## Completion

Complete a run only when:

- quality-model authority and unknowns are explicit;
- every file has a final coverage disposition;
- every candidate has a final validation disposition;
- every finding has evidence, severity, priority, and a valid decision;
- every destructive decision passes its gate;
- every score deduction has evidence;
- every phase has acceptance criteria;
- canonical artifacts validate;
- `report.md` is generated, not hand-authored.
