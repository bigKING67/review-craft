# Standard Review Workflow

Use this workflow only for canonical full reviews or when the bounded fast path
exits. The target repository remains read-only and all run artifacts stay outside
the target source.

## Contents

1. Runtime and preflight
2. Project Quality Model
3. Inventory and coverage
4. Evidence collection
5. Discovery and review dimensions
6. Candidate validation
7. Decisions
8. Scoring
9. Target design and remediation
10. Validation and completion

## 1. Runtime and preflight

Set the skill root to the directory containing `SKILL.md`. Check the runtime first:

```text
python3 <skill-root>/scripts/review_craft.py doctor --json
```

If Python 3.10+, Git, or a writable system temporary directory is unavailable,
continue with a clearly labeled manual E0/E1 review only when useful. Do not
fabricate run artifacts or command receipts. A manual fallback cannot emit a
numeric score, weighted score table, or approximate overall estimate.

Inspect the real target, scoped `AGENTS.md`, Git status, manifests, entrypoints,
build scripts, tests, and applicable engineering authority. Then run:

```text
python3 <skill-root>/scripts/review_craft.py preflight --target <repository>
```

For a Git diff or focused review:

```text
python3 <skill-root>/scripts/review_craft.py preflight \
  --target <repository> --mode diff --base origin/main

python3 <skill-root>/scripts/review_craft.py preflight \
  --target <repository> --mode focus \
  --focus architecture,maintainability,performance
```

Pass `--config <repository>/.review-craft.json` only when that explicit control
file exists. The command returns the run directory. Keep all subsequent artifacts
inside that directory, never in the target repository.

Bind every run to revision, branch, dirty state, source fingerprint, configuration
fingerprint, and exact scope. Start a new run when any of them changes materially.
`review-scope.json` is authoritative for mode, dimensions, resolved profile, and
immutable diff base. `module-map.json` is deterministic path evidence.
`dependency-map.json` is best-effort static evidence, not proof that dynamic or
framework-injected edges do not exist.

## 2. Project Quality Model

Read `quality-model.md` and populate `quality-model.json` from source and user
evidence:

- purpose and real audience;
- critical paths and invariants;
- compatibility and performance budgets;
- reliability requirements;
- explicit non-goals;
- authority sources, assumptions, and unknowns.

Ask a focused question only when a material product constraint cannot be derived.
Do not penalize a self-use project for enterprise capabilities explicitly outside
its goals.

## 3. Inventory and coverage

Use the preflight inventory as the only file worklist. Do not replace it with a
handwritten sample. Tracked files remain in scope even when ignored. Do not follow
symlinks outside the repository root.

Apply configured scope and exclude patterns before opening or hashing file contents.
Reuse that exact canonical source projection for run-state fingerprints, evidence
mutation checks, validation, and remediation baselines. Excluded paths are accounted as
an explicit boundary, not secretly re-read by a second full-worktree fingerprint. This
also means source receipts make no unchanged-content claim about excluded paths.

Assign every file exactly one outcome in `coverage.json`:

```text
REVIEWED
COVERED_BY_PARENT
GENERATED
VENDORED
BINARY
OUT_OF_SCOPE
UNREADABLE
DEFERRED
```

Keep repository-relative POSIX paths. Record reasons and evidence references for
every disposition. Classify generated and vendored content only through explicit
patterns or reliable build/package facts; size and unfamiliarity are insufficient.

`COVERED_BY_PARENT` is for tightly coupled files reviewed through one parent
artifact. Record the parent and evidence. It is not a shortcut for skipping a
directory. Keep unreadable and deferred files explicit, and never alter inventory
hashes to hide drift.

Track two percentages separately:

- `accountedPercent`: files with any final disposition;
- `reviewedPercent`: only `REVIEWED` and `COVERED_BY_PARENT` files.

`coveragePercent` in newly finalized run.v3 artifacts is the reviewed percentage.
Generated, vendored, and binary files may be fully accounted without pretending they
were reviewed. A final score still requires complete accounting and no `PENDING`,
`DEFERRED`, `UNREADABLE`, or `OUT_OF_SCOPE` review gaps; otherwise keep it provisional.

## 4. Evidence collection

Prefer the smallest command set that covers critical paths. Expand from targeted
tests to type checks, builds, integrations, benchmarks, profiles, or smoke tests
according to risk. Run only commands explicitly allowed in `.review-craft.json`:

```text
python3 <skill-root>/scripts/review_craft.py \
  run-evidence --run-dir <run-dir> --command <configured-name>

python3 <skill-root>/scripts/review_craft.py \
  run-evidence --run-dir <run-dir> --all
```

The runner uses argv without a shell and records duration, exit code, output, and
before/after repository state. It captures evidence but is not a security sandbox.
Each receipt is validated against the configured command's canonical name, argv, and
cwd. Editing a receipt, recomputing its ID, or renaming its output files cannot turn a
different command into valid evidence.

For commands that aggregate packaging, isolated installation, installed-runtime smoke,
benchmarks, profiles, traces, or deployment reproduction, configure `evidenceClaims`.
Each claim declares an ID, canonical kind, RFC 6901 `jsonPointer`, and scalar `equals`
value. The command must emit one JSON document on stdout. The runner evaluates the
assertions, records VERIFIED or UNVERIFIED per claim, and binds the results into receipt
identity. A configured claim without matching stdout is not evidence and causes the
runner to return 4 when the subprocess otherwise exited zero.

Use command `artifacts` declarations when structured stdout points to a decisive result
file. The source must be an absolute regular non-symlink file under a system temporary
root or the run directory and must not be inside the target repository. The runner copies
it into `evidence/commands/<receipt-id>.artifacts/`, records content hash and byte size,
and validates optional hash/size values from stdout. Missing, rejected, oversized, and
mismatched artifacts remain explicit failures. The canonical copy, not the temporary
source path, is the durable evidence.

If a command mutates tracked or untracked target source, stop automatic execution,
report the mutation, and do not revert user work.

Preserve failures rather than rerunning until green and omitting the initial result.
Summarize command output in the report and retain full stdout/stderr in evidence.
If no command is authorized or feasible, lower the evidence level and state the
gap. A source comment claiming a command passes is not execution evidence.

## 5. Discovery and review dimensions

Review in this order so correctness constrains later advice:

1. correctness, data integrity, and failure recovery;
2. architecture, boundaries, dependencies, state, and data flow;
3. maintainability, duplication, abstractions, naming, and change amplification;
4. performance and resource behavior;
5. tests and validation balance;
6. build, CI, release, dependencies, and basic security posture;
7. observability, documentation, repository structure, and developer experience.

Do not let directory preference override a stable module boundary or a performance
idea override correctness and measurement.

Append one canonical object per candidate to `candidate-ledger.jsonl`. At discovery,
record location, evidence, claimed impact, and confidence while keeping validation
separate. Do not create candidates to satisfy a requested count. Read
`finding-lifecycle.md` for canonical candidate fields and distinctions.

## 6. Candidate validation

Choose a method appropriate to the claim:

- correctness: minimal reproduction, test, or complete branch trace;
- architecture: dependency graph, call chain, or change-amplification evidence;
- performance: benchmark, profile, trace, or complete algorithmic analysis;
- concurrency: timing analysis, race test, or stress evidence;
- dead code: entrypoint, export/API, reflection/plugin, build, reference, and runtime checks;
- duplication: semantic and change-history comparison;
- test gap: critical-path mapping and failure injection where practical.

Zero textual references alone do not prove dead code. Uncovered lines alone do not
prove a test gap; connect them to an invariant and credible missed regression.

Close every candidate as `CONFIRMED`, `LIKELY`, `NEEDS_MEASUREMENT`, `BLOCKED`,
`REJECTED`, or `NOT_APPLICABLE`. Only confirmed and carefully bounded likely
candidates may become findings. Retain rejected candidates so false positives are
auditable.

## 7. Decisions

Read `finding-lifecycle.md`. Separate severity from P0-P3 priority. Create each
decision in `decisions.json` and reference it from `findings.json`. Choose one of:

```text
KEEP CLEAN_UP MERGE REPLACE REWRITE DELETE DEFER MEASURE DOCUMENT
```

Explain why the action has a better cost-to-risk ratio than alternatives. Create
evidence-backed `KEEP` decisions for important modules that are appropriate despite
imperfect aesthetics. For `DELETE` and `REWRITE`, record compatibility, migration,
rollback, and verification evidence. A failed gate requires a less destructive
decision.

## 8. Scoring

Read `scoring-and-report.md` and populate the canonical eight dimensions. Every
deduction cites a finding or explicit coverage/evidence gap. Never type an overall
score independently; the finalizer computes it. If canonical finalization is
unavailable, omit numeric scores.

Evidence gates:

- E0: no formal score;
- E1: source and configuration review;
- E2: at least one relevant configured check, test, build, or static tool completed
  successfully without timeout or repository mutation;
- E3: critical runtime behavior, isolated installation, benchmark, profile, or trace
  observed;
- E4: clean and deployment-representative reproduction.

E2-E4 require a successful canonical command receipt. Scores of 95+ require E3.
Scores of 98-100 require E4. Review gaps or unresolved candidates keep scoring
provisional.
When any semantic receipt exists, E3/E4 additionally require a VERIFIED claim with a
canonical kind mapped to that evidence level. This preserves historical run.v3 validation
while preventing a new structured receipt from using an opaque exit code to overstate its
meaning.

## 9. Target design and remediation

Complete `remediation-plan.json` with:

- target architecture and module boundaries;
- dependency direction and core data flow;
- state and error strategy;
- directory and testing structure;
- build, CI, and release flow;
- Phase 0-4 scope, prerequisites, benefits, risks, and acceptance.

Preserve migration and rollback paths whenever behavior or compatibility can
change. Do not promise a target score without a target evidence level and explicit
acceptance conditions.

When the user authorizes implementation, follow `remediation.md`. A prepared fix session
permits one terminal verification attempt under an exclusive session lock. Completed or
receipt-bearing incomplete sessions are not resumed; prepare a new session for an explicit
rerun. Final validation requires exact closure between the command receipt ledger and the
verification references.

## 10. Validation and completion

Run:

```text
python3 <skill-root>/scripts/review_craft.py validate --run-dir <run-dir>
python3 <skill-root>/scripts/review_craft.py finalize --run-dir <run-dir>
```

Fix canonical artifacts when validation fails. Never edit `report.md` to bypass a
contract failure. Complete a run only when:

- quality-model authority and unknowns are explicit;
- every file has a final coverage disposition;
- every candidate has a final validation disposition;
- every finding has evidence, severity, priority, and a valid decision;
- every destructive decision passes its gate;
- every score deduction has evidence;
- every remediation phase has acceptance criteria;
- canonical artifacts validate;
- `report.md` is deterministically generated, not hand-authored.
