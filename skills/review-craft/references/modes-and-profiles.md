# Review Modes and Project Profiles

## Contents

1. Standard review
2. Diff review
3. Focused review
4. Project profiles
5. Review depth
6. Run schema compatibility

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

Auto detection reads only the canonical selected inventory produced after `scope`,
`exclude`, and (for `diff`) changed-file resolution. A manifest, `SKILL.md`, or source file
outside that inventory cannot contribute a profile signal. Explicit profiles remain
configuration authority and do not invoke auto detection.

Profile detection is context, not proof. It must not overrule `ENGINEERING.md`, user
requirements, runtime behavior, or explicit non-goals. Override an incorrect result
through `.review-craft.json` rather than editing generated artifacts.

## Review depth

Use the bounded path for one narrow evidence-backed decision when canonical artifacts and
scoring are unnecessary. Use canonical `review`, `diff`, or `focus` for complete inventory,
candidate validation, scope-bound scoring, and deterministic reporting.

Canonical runs support `fast | standard | assured` through `assuranceLevel` or
`preflight --assurance`:

- `fast` is capped at 200 eligible files, three evidence commands, and 12 candidates. It
  always remains provisional and cannot claim above E2.
- `standard` runs the full canonical workflow without requiring a second verifier.
- `assured` requires a final score, E3+ evidence, no unverified claims, and exactly one
  registered `verification` artifact whose independent verifier agrees with every finding
  in canonical order.

The bounded path is still lighter than canonical `fast`: it emits neither canonical
artifacts nor a numeric score. The repository evidence-loop treatment is an eval protocol,
not proof that an `assured` verifier ran.

## Run schema compatibility

Version 0.6 creates `review-craft.run.v4`, adds `evidence-registry.json`, and requires
manual artifact references to use registered `artifact:<id>` identities whose files,
SHA-256, and byte sizes validate. Sealed run.v3 artifacts remain supported as historical
validation input, but they do not gain run.v4 manual-artifact integrity guarantees. An
unfinished run.v3 must be finalized with its matching v0.5 runtime or restarted with the
current preflight. Review Craft never mutates or silently upgrades an old run in place.
See [protocol-lifecycle.md](protocol-lifecycle.md) for frozen-write and retirement windows.
