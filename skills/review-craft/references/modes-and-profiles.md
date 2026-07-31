# Review Modes and Project Profiles

## Contents

1. Standard review
2. Diff review
3. Focused review
4. Project profiles
5. Run schema compatibility

## Standard review

Use `review` when the user asks for a repository-wide assessment. The configured
scope is inventoried deterministically. A final claim still requires every included
file to receive a coverage disposition.

## Diff review

Use `diff` for a branch, commit-base, or working-tree review that needs Review
Craft's candidate validation and remediation contracts. Prefer the host's normal
review command when the user only needs a fast PR pass.

Always provide an explicit base. Preflight resolves it to an immutable commit and
records modified, added, renamed, copied, deleted, and untracked paths. Deleted files
are hashed from the base commit. Do not silently replace a missing base with `HEAD`.

```text
python3 <skill-root>/scripts/review_craft.py preflight \
  --target . --mode diff --base origin/main
```

`diff` can also receive `--focus`. Coverage then means coverage of the selected
changed-file inventory for the declared dimensions, not a full-repository review.

## Focused review

Use `focus` when the user limits the review to one or more canonical dimensions:

```text
correctness
architecture
maintainability
performance
codeQuality
testing
dependenciesSecurity
repositoryExperience
```

Do not introduce unrelated findings merely because the full inventory is visible.
A directly observed P0/P1 correctness or security issue may still be surfaced with
an explicit scope exception.

## Project profiles

The default `auto` profile uses deterministic repository signals and records its
confidence and signals. Supported explicit profiles are:

```text
generic
application
desktop-app
frontend
backend-service
library
cli
monorepo
agent-project
data-pipeline
```

Profile detection is context, not proof. It must not overrule `ENGINEERING.md`, user
requirements, runtime behavior, or explicit non-goals. Override an incorrect result
through `.review-craft.json` rather than editing generated artifacts.

## Run schema compatibility

Version 0.5 continues to create `review-craft.run.v3` artifacts with content-bound
command receipts plus source-revalidated `module-map.json` and `dependency-map.json`.
It adds separate `review-craft.fix.v1` artifacts without mutating the sealed review.
Finalized v0.1/v0.2/v0.3 reports remain valid historical outputs, but an unfinished
old run must be finalized with its matching runtime or restarted with v0.5 preflight.
Review Craft never mutates an old run in place.
