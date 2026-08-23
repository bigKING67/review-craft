# review-craft

Evidence-driven software engineering review for real codebases.

**Review Craft** turns “review this repository” into a repeatable process:

```text
model -> inventory -> evidence -> discover -> validate -> decide -> score -> plan -> finalize
```

It reviews:

- correctness, reliability, and failure recovery;
- architecture, module boundaries, dependencies, state, and data flow;
- maintainability, code simplicity, consistency, and change amplification;
- measured performance and resource efficiency;
- tests, builds, CI, and release systems;
- dependencies and basic security posture;
- observability, repository structure, documentation, and developer experience.

Review Craft does not reward finding the most issues. It rewards explicit coverage,
reproducible evidence, validated findings, proportional remediation, and knowing
when existing code should be kept.

> 中文定位：面向真实代码库的证据驱动工程审查、问题验证、整改决策与质量治理系统。

## Status

Version `0.6.4` provides read-only repository, Git diff, and focused-dimension review
workflows, explicitly authorized remediation verification, immutable fix-attempt lineage,
and independent post-commit delivery attestations. The runtime binds selected findings to
a sealed review, captures the exact pre-change source, executes only selected configured
verification commands, and content-binds the resulting source diff, command receipts, and
HUMAN/AGENT_ASSISTED/AUTOMATED assessment. It never edits target source, commits, pushes,
or publishes. Canonical run, fix, delivery, and eval artifacts are written outside the
target repository by default. A sanitized matched real-host Golden snapshot from v0.3
remains tracked under `evals/golden-results/705dbac-gpt-5.6-sol/`.

The repository also contains an independent three-arm review-feedback eval harness under
`evals/`. Its active protocol compares ordinary review, project-specific risk-lens review,
and the complete Review Craft evidence loop without changing the installable runtime. The earlier
four-arm v1 snapshot is frozen historical evidence: its adversarial-only arm is a negative
control, not an active treatment or product recommendation. A single complete ablation is
only narrow evidence for its bound suite and environment; it is not a general product claim.

Preflight creates `review-craft.run.v4`. Its `evidence-registry.json` and
`register-evidence` command copy decisive manual runtime, benchmark, profile, or trace
files into the run and bind each file by canonical ID, path, SHA-256, and byte size before
it can be cited as `artifact:<id>`. Validation rejects unknown or path-style references,
missing or modified bytes, size drift, symlinks, duplicate IDs or paths, orphan artifacts,
and mutation after the run is sealed. Sealed `review-craft.run.v3` data remains readable
for historical validation but does not gain run.v4 integrity guarantees.

The `review-craft.fix-attempt.v1` protocol runs bound commands before assessment, stores
each attempt in a separate immutable directory, permits a retry only for the exact same
source/Git/configuration state, and reports recovery without erasing the first failure.
After the latest attempt is verified and committed, `verify-attempt-delivery` copies the
entire canonical attempt lineage into a portable `review-craft.delivery.v2` artifact
instead of silently reinterpreting it as legacy `fix.v1`. The v0.5-compatible
`review-craft.fix.v1` and `review-craft.delivery.v1` protocols remain supported without
changing their semantics.

The following are intentionally not implemented in 0.6.4: deep multi-pass review,
automatic source mutation, historical comparison, SARIF, MCP, custom UI, and a cloud
service. Delivery v1 and v2 do not verify GitHub Releases or npm registry publication.

## What makes it different

Review Craft requires:

- a Project Quality Model grounded in real project goals and non-goals;
- deterministic per-file coverage accounting;
- distinct accounted and actually reviewed coverage metrics;
- a candidate ledger separate from validated findings;
- independent severity and remediation priority;
- explicit `KEEP`, `CLEAN_UP`, `MERGE`, `REPLACE`, `REWRITE`, `DELETE`, `DEFER`,
  `MEASURE`, and `DOCUMENT` decisions;
- evidence-gated scoring;
- structured command claims and content-bound command-produced evidence artifacts;
- content-bound registration for decisive manually produced evidence artifacts;
- scope-limited score wording for `focus` and `diff` reports;
- separate confirmed-finding, evidence-gap, and remaining-risk report sections;
- migration, compatibility, rollback, and verification for destructive decisions;
- a deterministic Markdown report generated from canonical JSON;
- explicit `review`, `diff`, and `focus` scope artifacts;
- deterministic profile, module-map, and best-effort local dependency evidence.

## Relationship to official OpenAI workflows

- Use ordinary Codex Review for a bounded PR, commit, branch, or working-tree diff.
- Use Codex Security for threat modeling, vulnerability discovery, exploitability,
  attack paths, PoCs, and security remediation validation.
- Use Review Craft for repository-wide, multi-dimensional engineering assessment
  and remediation governance.

Review Craft complements these tools. Version 0.6.4 does not claim to replace or
outperform Codex Security.

## Invocation and review depth

Implicit invocation is enabled in `skills/review-craft/agents/openai.yaml` because the
published real-host bilingual routing result is bound to the exact Skill metadata and every
repetition passes the declared thresholds. The routing gate fails closed if that result is
missing, stale, synthetic, or below threshold. Quick PR, small diff, casual scoring,
visual-design, deep exploit-validation, and direct implementation requests remain explicit
negative routes. Use `$review-craft` when deterministic routing is required.

The current product has a bounded path plus three canonical assurance levels:

- `bounded`: one narrow evidence-backed `KEEP`, `CLEAN_UP`, `MEASURE`, or other decision;
  it does not emit canonical artifacts or a numeric score;
- canonical `fast`: a budgeted run capped at 200 eligible files, three evidence commands,
  and 12 candidates; its score remains provisional and evidence is capped at E2;
- canonical `standard`: complete inventory, candidate validation, deterministic artifacts,
  scope-bound scoring, and final reporting;
- canonical `assured`: standard requirements plus E3+ evidence and one independently
  produced, registered verifier artifact that agrees with every canonical finding.

Set `assuranceLevel` in `.review-craft.json` or pass `preflight --assurance`. The
`REVIEW_CRAFT_EVIDENCE_LOOP` eval treatment remains a separate development experiment and
does not itself satisfy the `assured` verifier contract.

## Repository layout

```text
.codex-plugin/plugin.json       Codex skills-only plugin manifest
skills/review-craft/            canonical installable runtime
contracts/                      package, evidence, and release policies
tests/                          deterministic runtime and contract tests
evals/                          positive and anti-over-review fixtures
scripts/                        repository validation and packaging gates
```

Only `skills/review-craft/` is the installable runtime product.

## Requirements

- Python 3.10 or later;
- Git;
- a writable system temporary directory;
- Node.js only for npm/Pi packaging and package validation.

The installed runtime has no third-party Python dependencies. Repository development
uses locked tooling through `uv`.

## Install as a Codex or Agent Skill

`skills/review-craft/` is the complete installable Skill. Install the whole directory;
copying only `SKILL.md` omits the runtime, schemas, references, and templates.

For hosts that share Agent Skills, install the pinned release into
`~/.agents/skills`:

```bash
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
python3 "$CODEX_HOME/skills/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo bigKING67/review-craft \
  --path skills/review-craft \
  --ref v0.6.4 \
  --dest "$HOME/.agents/skills"
```

The Codex installer defaults to `$CODEX_HOME/skills` (normally
`~/.codex/skills`) when `--dest` is omitted. Choose one active root for
`review-craft`; do not install different copies under both `~/.agents/skills` and
`~/.codex/skills`. The installer fails rather than overwriting an existing target,
so back up or remove an old installation intentionally before upgrading.

The installed directory must contain at least:

```text
review-craft/
├── SKILL.md
├── VERSION
├── agents/
├── lib/
├── references/
├── schemas/
├── scripts/
└── templates/
```

Start the next Codex turn after installation so the host can discover the Skill,
then validate the installed runtime rather than the repository checkout:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 \
  "$HOME/.agents/skills/review-craft/scripts/review_craft.py" \
  doctor --json
```

The result should report `"ready": true` and `"version": "0.6.4"`.
If you selected the Codex-only root, replace `$HOME/.agents/skills` in the
verification path with `$CODEX_HOME/skills`.

## Use from a source checkout

The repository keeps the canonical Skill under `skills/review-craft/`. A host may
load that directory directly or expose it through a repository-local
`.agents/skills` root. Pi can load the source without installing it:

```text
pi --skill ./skills/review-craft
```

## Use with Pi or the Codex plugin

Install the public Pi package with:

```text
pi install npm:@bigking67/review-craft
```

Pi/npm installation does not install or register the Skill under a Codex or Agent
Skill root. The Codex plugin entrypoint is declared separately in
`.codex-plugin/plugin.json`; plugin installation and direct Skill-directory
installation are alternative host integration paths, not cumulative requirements.

After the host has discovered the Skill, request:

```text
Use $review-craft to perform an evidence-driven engineering review of this repository.
```

## Runtime CLI

Check prerequisites:

```bash
python3 skills/review-craft/scripts/review_craft.py doctor --json
```

Create a run:

```bash
python3 skills/review-craft/scripts/review_craft.py preflight --target .
```

Review a Git diff or selected dimensions:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  preflight --target . --mode diff --base origin/main

python3 skills/review-craft/scripts/review_craft.py \
  preflight --target . --mode focus \
  --focus architecture,maintainability,performance
```

With an explicit configuration:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  preflight --target . --config .review-craft.json
```

Run a configured evidence command:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  run-evidence --run-dir <run-dir> --command test

python3 skills/review-craft/scripts/review_craft.py \
  run-evidence --run-dir <run-dir> --all
```

Command evidence is configuration-bound: receipt name, argv, and cwd must match the
canonical configured command. E2-E4 require at least one receipt that passed without
timeout, repository mutation, or a failed declared semantic assertion.

Commands that wrap several materially different checks can declare machine-readable
semantic claims. A declaration is not evidence by itself: the command must emit one JSON
document on stdout, and every claim is verified by an RFC 6901 pointer and exact scalar
comparison. Claim kinds calibrate the strongest evidence they can support: check, test,
build, and package are E2; isolated-install, runtime, benchmark, profile, and trace are E3;
clean-deployment-reproduction is E4. Once a run uses semantic receipts, E3/E4 must have a
matching verified claim rather than relying on an opaque exit code.

```json
{
  "commands": {
    "cli-check": {
      "argv": ["node", "scripts/cli-check.mjs", "--json"],
      "evidenceClaims": [
        {
          "id": "npm-pack",
          "kind": "package",
          "jsonPointer": "/checks/npmPack",
          "equals": true
        },
        {
          "id": "isolated-install",
          "kind": "isolated-install",
          "jsonPointer": "/checks/isolatedInstall",
          "equals": true
        },
        {
          "id": "installed-cli-smoke",
          "kind": "runtime",
          "jsonPointer": "/checks/installedCliSmoke",
          "equals": true
        }
      ],
      "artifacts": [
        {
          "id": "installed-runtime-result",
          "pathJsonPointer": "/artifact/path",
          "sha256JsonPointer": "/artifact/sha256",
          "sizeBytesJsonPointer": "/artifact/sizeBytes",
          "maxBytes": 52428800
        }
      ]
    }
  }
}
```

Declared artifact paths must resolve to regular non-symlink files under a system temporary
root or the run directory, never inside the target repository. Review Craft copies accepted
artifacts into the canonical run, records SHA-256 and byte size, and revalidates the copy.
Missing, rejected, oversized, or mismatched artifacts remain auditable receipt failures;
they cannot be upgraded to verified evidence by editing configuration.

Register a decisive artifact produced outside a configured command before citing it:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  register-evidence --run-dir <run-dir> \
  --id renderer-dom-probe \
  --source <outside-artifact.json> \
  --kind runtime \
  --producer Codex \
  --description "Controlled streaming and settled DOM probe" \
  --media-type application/json
```

The command is available only for an unsealed current-source run.v4 draft. It copies the
regular non-symlink source to `evidence/registered/<id>/artifact` and returns
`artifact:<id>`. Canonical validation rejects absolute, traversal, and path-style
artifact references, unknown IDs, symlinks, missing copies, and SHA-256 or size drift.
Manually placing a file under the run directory is not registration and does not make it
durable evidence.

Validate and finalize canonical artifacts:

```bash
python3 skills/review-craft/scripts/review_craft.py validate --run-dir <run-dir>
python3 skills/review-craft/scripts/review_craft.py finalize --run-dir <run-dir>
```

Do not edit `report.md` directly. Correct the canonical JSON and rerun finalization.

Prepare an explicitly selected fix before editing the target:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  prepare-fix --run-dir <sealed-run-dir> \
  --finding RC-FINDING-001 --command test
```

`prepare-fix` is read-only and records `EXPLICIT_USER_REQUIRED`. After the user has
authorized the implementation and the host has applied only the selected changes, prefer
capturing immutable command evidence before writing an assessment:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  capture-fix-attempt --fix-dir <fix-dir>
```

The result includes `attemptDir`, `completedAt`, and `evidenceSha256`. Read the attempt's
receipts and structured stdout, then create a `review-craft.fix-attempt-assessment` that
binds those exact values. Finalize, validate, and project its lineage:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  finalize-fix-attempt --attempt-dir <attempt-dir> \
  --assessment <assessment.json>

python3 skills/review-craft/scripts/review_craft.py \
  validate-fix-attempt --attempt-dir <attempt-dir>

python3 skills/review-craft/scripts/review_craft.py \
  list-fix-attempts --fix-dir <fix-dir>
```

Assessment evidence can reference `change:<path>`, `command:<name>`,
`claim:<command>:<claim-id>`, `measurement:<measurement-id>`, and HUMAN-only
`manual:<description>` evidence. Each structured measurement names the command, RFC 6901
JSON pointer, and exact scalar value. Finalization rejects a measurement that conflicts
with captured stdout or an `assessedAt` earlier than command completion.

If an attempt fails because of a command flake, finalize that failure first. A retry may
then append another attempt only when source fingerprint, revision, branch, remote, Git
status, fix plan, and command configuration still match. `VERIFIED_WITH_RETRY` preserves
the failed predecessor and records `FLAKY_COMMAND_RECOVERED`; it never rewrites the first
attempt. Use `--snapshot-only` when validating an older attempt after the live checkout has
moved on.

The legacy v0.5-compatible single-attempt workflow remains available. Create a
`review-craft.fix-assessment` before running the legacy command:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  verify-fix --fix-dir <fix-dir> --assessment <assessment.json>

python3 skills/review-craft/scripts/review_craft.py \
  validate-fix --fix-dir <fix-dir>
```

`verify-fix` returns `VERIFIED`, `PARTIAL`, `FAILED`, or `NO_CHANGES`. A valid `FAILED`
artifact remains a failed remediation: `validate-fix` proves content integrity, not that
the selected issue was resolved. See
`skills/review-craft/references/remediation.md` for assessment evidence rules and exit
codes.

Legacy `review-craft.fix.v1` has one terminal verification attempt. `verify-fix` holds an exclusive
session lock through command execution and terminal artifact creation, so concurrent or
sequential callers cannot create competing results. A completed session is read-only.
If a crash leaves command receipts or only one terminal artifact, the session fails closed;
run `prepare-fix` again to create a new session for an explicit rerun. `validate-fix`
requires the receipt ledger to match the final verification references exactly.

Do not mix legacy root receipts with attempt-local receipts in the same fix directory.
`verify-delivery` continues to accept only a legacy finalized `review-craft.fix.v1`
source. For attempt lineage, explicitly name the latest verified attempt:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  verify-attempt-delivery --attempt-dir <latest-verified-attempt-dir>
```

The command rejects a failed, partial, awaiting-assessment, or non-latest attempt. It also
requires the deterministic lineage aggregate to be `VERIFIED` or
`VERIFIED_WITH_RETRY`; it never searches the lineage for an older green result.

### Post-delivery attestation

Do not update `fix-verification.json` after commit, push, or CI. Create an independent
delivery artifact instead:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  verify-delivery --fix-dir <fix-dir>

python3 skills/review-craft/scripts/review_craft.py \
  validate-delivery --delivery-dir <delivery-dir>
```

For `review-craft.fix-attempt.v1`, use the separate v2 producer and the same portable
validator:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  verify-attempt-delivery --attempt-dir <latest-verified-attempt-dir>

python3 skills/review-craft/scripts/review_craft.py \
  validate-delivery --delivery-dir <delivery-dir>
```

Local-only delivery proof is `PARTIAL`: the checkout must be clean, current `HEAD` is
recorded, and the current source fingerprint must equal the fix verification fingerprint.
No network command runs by default.

When explicitly authorized, bind remote push and GitHub Actions state:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  verify-delivery \
  --fix-dir <fix-dir> \
  --verify-push \
  --github-run <run-id>
```

The same explicit `--verify-push` and `--github-run` options are available on
`verify-attempt-delivery`; its evidence documents are versioned as
`review-craft.delivery.v2`.

`--verify-push` runs fixed-argv `git ls-remote` and requires the remote branch SHA to equal
local `HEAD`. `--github-run` runs fixed-argv `gh run view` and requires matching `headSha`,
completed run/jobs, and a successful conclusion. Requested missing, failed, incomplete, or
mismatched proof produces `FAILED`; it is recorded rather than discarded. Exit codes are
`0` for `VERIFIED`, `3` for `PARTIAL`, `4` for `FAILED`, and `2` for invalid input or an
invalid contract.

Each invocation writes a new `review-craft.delivery.v1` directory outside the target. It
copies and hashes `fix-plan.json`, `fix-assessment.json`, `fix-verification.json`, and the
source inventory configuration. `validate-delivery` is portable: it does not read the
original fix directory or target checkout. Raw command stdout/stderr are not stored; only
normalized fields, byte counts, and hashes are retained. GitHub Release and npm registry
stages remain explicit `NOT_VERIFIED` values in v1.

Each `review-craft.delivery.v2` invocation copies `fix-plan.json`, the source inventory
configuration, a deterministic `fix-lineage.json`, and the manifest, evidence,
assessment, and verification JSON for every attempt through the selected latest attempt.
This preserves failed predecessors and the SHA-256 predecessor chain in a portable
snapshot. Raw attempt receipt ledgers and command stdout/stderr are deliberately not
copied, so portable v2 validation verifies the canonical JSON/hash lineage but does not
claim to replay raw command payloads. GitHub Release and npm registry stages likewise
remain `NOT_VERIFIED`.

## Configuration

Copy `skills/review-craft/templates/review-config.json` to
`.review-craft.json` in a target repository.
Commands use argv arrays and execute with `shell=false`. A configured command is not
a security sandbox and does not override host approvals, network policy, or sandboxing.
Each command starts in an isolated POSIX session or Windows process group. On timeout,
the runner terminates the inherited POSIX process group or uses fixed-argv `taskkill /T /F`
on Windows, waits for the direct process, and only then captures the final repository
fingerprint. This closes ordinary inherited-descendant late-write windows; it is not a
hostile-code sandbox and does not prove termination of detached or externally launched
processes.
The `allowNetwork` and `allowInstall` values are declarative host/agent policies;
the Python runtime records them but does not enforce network or installation isolation.
`allowRepositoryMutation` controls the runner response after before/after fingerprints
detect a change; it does not prevent a configured command from writing. Only
`outputOutsideRepository` is directly enforced during preflight path resolution.
The configured `scope` and `exclude` define the canonical source projection reused by
preflight, evidence mutation fingerprints, draft/final validation, and fix baselines.
Excluded and out-of-scope file contents are not opened or hashed by these projections.
Consequently, a Review Craft receipt does not prove that excluded paths remained
unchanged; command isolation and host policy still own that boundary.
Evidence commands targeting the same run are serialized with an OS-managed file lock.
This preserves receipt sequence and mutation attribution across concurrent callers; it
does not make configured commands run in parallel. Legacy fix verification adds a separate
session-level lock around its complete one-attempt lifecycle. Attempt capture/finalization
adds a fix-level lock, while every attempt owns a separate receipt ledger and terminal pair.
Semantic claims and copied command artifacts are part of receipt identity. Fix verification
preserves the same identity and treats a failed semantic assertion as a failed verification
command even when the subprocess itself exited zero.

Repository comments, README files, issues, logs, and fixtures are untrusted analysis
data. Only current user instructions, scoped `AGENTS.md`, and the structured
`.review-craft.json` control the workflow.

Version 0.6 creates `review-craft.run.v4` review artifacts with a registered
manual-evidence contract, supports `review-craft.fix-attempt.v1`, and exports independent
`review-craft.delivery.v2` attestations. Finalized historical run.v3 artifacts can still
be validated, but unfinished run.v3 data must be finalized with its matching v0.5 runtime
or restarted with current preflight. Review Craft never mutates or silently upgrades an
old run in place. Historical run.v3 scorecards without explicit accounted and reviewed
fields retain their original `coveragePercent` interpretation during validation. The
legacy `review-craft.fix.v1` and `review-craft.delivery.v1` protocols remain available;
no current protocol reinterprets or mutates their existing artifacts.

| Workflow | Current protocol | Historical read/validation support | Write behavior |
| --- | --- | --- | --- |
| Review | `review-craft.run.v4` | sealed `run.v3` | writes v4 only |
| Fix attempt | `review-craft.fix-attempt.v1` | n/a | immutable attempt directories |
| Legacy fix | `review-craft.fix.v1` | `fix.v1` | compatibility path only |
| Delivery | `review-craft.delivery.v2` | `delivery.v1` | writes v2 for attempt lineage |

Unfinished run.v3 data must still be finalized by its matching runtime or restarted. The
compatibility promise is validation and preservation, not silent migration.
The deterministic report labels `focus` and `diff` scores as scope-limited rather than
repository-wide, renders `CONFIRMED` and `LIKELY` findings in separate counts and sections,
separates findings from evidence gaps and remaining risks, and lists verified command claims
and captured evidence artifacts. Final run.v4 score deductions must reference an existing
finding ID or a canonical `evidence-gap:<id>`; sealed run.v3 validation retains its published
free-form reference semantics.

This repository also keeps an active `.review-craft.json` as its dogfood control file. It
binds the canonical unit-test, source-validation, package, complexity-budget, and 1k runtime
benchmark commands while disabling network, install, and target-mutation authorization.

## Validate this repository

```bash
uv sync --locked --group dev
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m unittest discover -s tests -p 'test_*.py'
uv run --locked python scripts/validate.py
uv run --locked python scripts/complexity_budget.py
python3 scripts/package_check.py
python3 scripts/release_gate.py
npm pack --dry-run --json
```

`release_gate.py` now runs the global complexity ratchet, tests, routing policy, source and
Schema validation, Ruff, and one exact installed-package E2E. It can preserve the same
validated tarball and a content receipt for downstream host parity:

```text
uv run --locked python scripts/release_gate.py \
  --package-output artifacts/review-craft.tgz \
  --package-receipt artifacts/package-receipt.json
```

The package E2E installs the tarball in an isolated temporary home, proves the runtime was
imported from that installation, executes `doctor`, completes canonical preflight through
deterministic report finalization, validates the installed `fast` assurance contract,
captures and validates an immutable remediation attempt, and checks the target mutation
boundary. CI builds this tarball once and revalidates those
same bytes on Ubuntu, Windows, and macOS; source-tree tests remain a separate evidence layer.

The eval runner does not invoke a model during CI. Contract tests use a synthetic adapter
that is permanently ineligible for golden status. A real Codex CLI run is explicit and
may incur host cost:

```text
uv run --locked python scripts/run_evals.py run \
  --treatment REVIEW_CRAFT \
  --adapter-command python3 scripts/codex_eval_adapter.py \
  --model <model> --reasoning <reasoning>
```

The Codex adapter ignores user configuration and rules. For a non-default provider,
pass credential-free provider metadata explicitly after `--adapter-command`:

```text
--provider-name <name> \
--provider-base-url <http-or-https-url> \
--provider-wire-api responses \
--provider-requires-openai-auth \
--provider-supports-websockets
```

Use an auth-only temporary `CODEX_HOME` for real evaluations. Codex-managed
`skills/.system/` files are allowed and fingerprinted separately. By default the adapter
fails closed on other `skills/` or `plugins/`; both system and extension surfaces become
matched provenance fields. Credentials remain external and must never be placed in
adapter argv or run artifacts.

Routing evaluation is separate from output-quality evaluation:

```text
uv run --locked python scripts/run_evals.py run-routing \
  --output-root <external-directory> \
  --repetitions 2 \
  --adapter-command python3 scripts/codex_eval_adapter.py \
  --model <model> --reasoning <reasoning>

uv run --locked python scripts/run_evals.py validate-routing \
  --result <routing-result.json>
```

The 60-case suite is bilingual and reports implicit precision and recall, explicit activation,
workflow accuracy, and high-cost false-trigger rate. Its structured decision is not a stable
native Skill-load receipt. A content-bound real-host result is published under
`evals/routing-results/current/`; see `evals/routing-results/README.md` for its scope and
limitations.

Eval run v3 also passes an optional `REVIEW_CRAFT_EVAL_USAGE_OUTPUT` sidecar path to every
adapter. The Codex adapter uses `codex exec --json` and deterministically extracts
`turn.completed.usage` plus completed command, file-change, MCP, collaboration, and
web-search items. Each case records input, cached-input, cache-write-input, output,
reasoning-output, and total tokens together with turn and tool-call counts. `totalTokens`
is input plus output; cached input and reasoning output are subcounts. Unsupported
adapters, invalid JSONL, and unsupported future formats produce explicit unavailable
reasons with `null`, not zero. Aggregate `reportedUsage` always states how many cases it
covers.

Run the same full suite with `--treatment ORDINARY_PROMPT` and identical host metadata for
a matched baseline. Use `compare` to bind both run hashes and reject mismatched source,
suite, host, provider, isolation, timeout, or adapter inputs.
Adapter descriptions are trusted provenance declarations rather than cryptographic
attestations. The runner binds their metadata and artifacts, records start/completion
source parity, and rejects Golden eligibility when the source changes during a run, but
operators must still review and trust any third-party adapter they execute.

For the separate three-arm review-feedback experiment, use `run-ablation`, blinded
`prepare-ablation-adjudication` / `adjudicate-ablation`, `compare-ablation`, and the
fail-closed `export-ablation` command. Raw artifacts remain outside the repository; only a
full-suite, clean-source, real-host, usage-complete, fully adjudicated sanitized snapshot may
be tracked. The ordinary and risk-lens arms receive neither the Review Craft skill tree nor
verifier access; the evidence-loop arm must execute every bound verifier successfully.
Treatment labels and raw prompts are withheld from adjudication, although observable output
and tool-use differences can still reveal intervention characteristics. A-to-B isolates the
active risk-lens prompt relative to ordinary review. B-to-C adds both Review Craft skill
instructions and verifier feedback, so it measures the complete evidence loop rather than
the independent effect of either addition.
See `evals/ablation-results/README.md` for the exact boundary.

The repository also contains an independent remediation-safety / anti-degradation harness.
It is governance for Review Craft development, not automatic mutation in the installable
`skills/review-craft/` runtime, and it does not change run.v4, fix.v1, fix-attempt.v1, or
delivery.v1/v2 semantics. The protocol runs isolated copies of each fixture through
`ORDINARY_NAIVE_LOOP`, `REVIEW_CRAFT_UNGATED_LOOP`, and
`REVIEW_CRAFT_EVIDENCE_GATED_LOOP`. Baseline and post-change oracles measure defect
resolution, preservation regressions, clean-case mutation, scope violations, cumulative
churn, first-decision alignment, invocations, and reported usage. Initial decision metrics
bind to the first completed Review artifact and remain optional for legacy v1 runs.
`repairSuccessRate` is deliberately
invocation-based: a later `NO_CHANGE` repair invocation remains in its denominator.

```text
uv run --locked python scripts/run_evals.py run-remediation-safety \
  --rounds 3 \
  --adapter-command python3 scripts/codex_eval_adapter.py \
  --model <model> --reasoning <reasoning>

uv run --locked python scripts/run_evals.py validate-remediation-safety \
  --run-dir <run-directory>
```

Raw prompts, source snapshots, diffs, oracle observations, usage, and tool traces remain in
the external run directory. A code regression or out-of-scope edit is a valid experimental
outcome and remains separate from adapter, oracle-process, sandbox, or artifact-integrity
failure. No remediation-safety Golden or general superiority claim is currently published.
See `evals/remediation-safety/README.md` for the exact stop rules and evidence boundary.

The run's recall, precision, false-positive, location, and evidence-presence fields are
deterministic structural metrics. They do not prove that a finding's evidence matches the
seeded issue. Bind an explicit human or agent-assisted semantic adjudication to the run and
every normalized output before publishing semantic quality claims:

```text
uv run --locked python scripts/run_evals.py prepare-adjudication \
  --run-dir <run-dir> \
  --kind HUMAN \
  --protocol <protocol-id> \
  --output <eval-adjudication-input.json>

uv run --locked python scripts/run_evals.py adjudicate \
  --run-dir <run-dir> \
  --adjudication <eval-adjudication-input.json> \
  --output <eval-adjudication-result.json>

uv run --locked python scripts/run_evals.py validate-adjudication \
  --run-dir <run-dir> \
  --result <eval-adjudication-result.json>
```

Adjudication does not mutate the original run. It distinguishes a seeded-issue match, a
different valid finding, a false positive, a miss, a correct no-finding outcome, and an
unresolved case. Evaluation prompts limit each normalized output to one primary candidate
finding. Unresolved evidence produces partial rather than fabricated semantic precision,
false-positive, or decision-accuracy metrics.

Create a semantic-aware matched comparison, then export only its sanitized Golden
projection:

```text
uv run --locked python scripts/run_evals.py compare \
  --review-craft-run <review-run> \
  --baseline-run <ordinary-prompt-run> \
  --review-craft-adjudication <review-adjudication-result.json> \
  --baseline-adjudication <baseline-adjudication-result.json> \
  --output <comparison.json>

uv run --locked python scripts/run_evals.py export-golden \
  --review-craft-run <review-run> \
  --baseline-run <ordinary-prompt-run> \
  --review-craft-adjudication <review-adjudication-result.json> \
  --baseline-adjudication <baseline-adjudication-result.json> \
  --output <snapshot.json>
```

On the tracked 2026-07-29 controlled 12-case fixture suite, Review Craft and the ordinary
prompt baseline both reached 100% semantic seeded-issue recall, 100% semantic finding
precision, and 0% clean-negative false-positive rate. Review Craft improved semantic
decision accuracy from 75% to 100%, while taking 927,819 ms versus 447,243 ms in that single
matched run. The adjudication was `AGENT_ASSISTED`, not independent human review. This is a
narrow fixture result, not universal superiority, a stable cost ratio, a native diff-review
comparison, or a comparison with Codex Security. See
`evals/golden-results/705dbac-gpt-5.6-sol/README.md` for the exact evidence boundary.

Real Repository Benchmark v1 adds eight immutable upstream repositories across Python,
Node.js, Electron, Go, Rust, and JVM. Each case contains a real upstream-fixed defect, a
legitimate `KEEP`, a decoy, a measurement-only claim, and an evidence-gap claim. The
oracle-free blind suite, verified source materialization, campaign runner, independent
adjudication contract, and stability report are under `evals/real-repositories/`. The current
component-rubric workflow emits separately ordered v2 packets with opaque IDs covering all
completed probe responses and additional findings, with a coordinator-only mapping and
content-bound v2 submission finalization. The assembled v3 adjudication binds each subject hash,
derives its overall label from type-specific component verdicts, and leaves every reviewer
disagreement `SPLIT` instead of treating a majority vote as semantic truth. The runner preserves
partial streamed stdout and sanitized tool-call checkpoints across timeouts while the canonical
lifecycle boundary terminates the process tree.
High-cost execution uses a content-bound deterministic plan, resumable exact-prefix
checkpoints, serial repository shards, a shared content-bound budget ledger, validated merge
receipts, content-bound prompt hashes, per-sample and per-repository-shard reported
input/total-token ceilings, a global reported-token ceiling, active runner-time ceilings, and a
cross-shard consecutive
infrastructure-failure circuit breaker. Token receipts retain cached-input, cache-write-input,
reasoning-output, and ordinary input/output components when the host reports them. New plans also
stop after the declared cumulative
unknown-usage, artifact-invalid, or per-model-profile timeout limit. New plans also bind a
300-second inactivity warning, a 600-second live diagnostic capture, and a two-recovered-stall
per-model-profile ceiling. The first recovered diagnostic stall remains evidence and execution
continues; the second for that model profile stops before another sample is scheduled across
repository shards. This policy does not shorten the declared per-sample timeout. Lifecycle
receipts preserve thread/turn/item/tool timing, last semantic progress, the diagnostic process
liveness observation, and timeout cleanup outcome. The stability report keeps the raw
model-authored `rootCauseOverlap` and adds `rootCauseIdentityOverlap`, which uses canonical probe
identity or exact evidence locations rather than free-text root-cause wording. The location
projection is a conservative proxy: range drift can under-match, while two findings on the exact
same span can over-match. The v6 Codex adapter also writes a per-invocation isolation receipt at
pre-run, post-start, and post-exit. It fingerprints managed `.system` state separately from user
extensions. A fresh auth-only Codex home must first run `prepare-campaign-isolation`. That explicit
host-preparation step points a short-lived Codex process at an owned loopback blackhole, waits for
the bundled managed-system tree to become stable, terminates the process before any provider can
respond, and emits a content-bound preparation receipt. Generate the sealed campaign plan only
afterward, so its existing `isolationSha256` binds the fully materialized tree rather than an empty
pre-bootstrap home:

```text
uv run --locked python scripts/real_repository_benchmark.py \
  prepare-campaign-isolation \
  --adapter-config <adapter-config.json> \
  --output <isolation-preparation.json>

uv run --locked python scripts/real_repository_benchmark.py \
  validate-campaign-isolation-preparation \
  --adapter-config <adapter-config.json> \
  --receipt <isolation-preparation.json>
```

`plan-campaign`, `run-plan`, and the legacy direct runner reject preparation-capable adapters whose
managed-system tree is still empty. During every campaign invocation, a missing, invalid,
unavailable, system-drifted, or user-extension-drifted receipt makes a non-timeout sample
artifact-invalid; there is no first-sample drift exception. A timeout is the sole precedence
exception for an unavailable capture: it remains `TIMED_OUT/TIMEOUT` instead of being overwritten
by a missing post-exit capture, while an explicit system or user-extension drift remains
artifact-invalid. The isolation sidecar continues to expose either condition. Adapters declaring
`review-craft.eval-timeout-control.v1` receive the inner sample deadline and a content-bound
finalization-grace contract. The Codex adapter terminates its child tree and finalizes sidecars;
the outer runner deadline is only a failsafe. This proves that the declared Codex-home skill/plugin
surface remained stable after explicit preparation. It does not prove operating-system network or
filesystem sandbox enforcement. Operational Canaries should use a separately sealed,
responsiveness-oriented timeout rather than inheriting a longer quality-Campaign timeout. Legacy plans,
lifecycle receipts, ledgers, run states, and stability reports remain validation-compatible. The
legacy direct `run`
command remains compatibility-only for bounded diagnostics; use `plan-campaign`, `run-plan`,
`validate-campaign-run`, and `merge-campaign-runs` for a full campaign. A campaign is Golden
only after the full 8 x 3 treatments x 2+ model configurations x 3 repetitions
matrix completes and independent adjudication validates; the checked-in materialization
receipt alone makes no model-quality claim.

When observed latency differs materially by model profile or treatment, `plan-campaign` can seal
profile-wide `--timeout-override MODEL_CONFIGURATION_ID SECONDS` entries and more-specific
`--treatment-timeout-override MODEL_CONFIGURATION_ID TREATMENT SECONDS` entries. A treatment
override takes precedence over its profile override, and both take precedence over the default
`--timeout-seconds`. The expanded timeout remains bound into every sample; `run-plan` does not
infer or mutate this policy at execution time.

Runtime scale measurements are also explicit and external by default. The normal command
runs the 1k-file tier; `--full` additionally runs 10k and 100k tiers and can take materially
longer:

```text
uv run --locked python scripts/benchmark_runtime.py run
uv run --locked python scripts/benchmark_runtime.py validate --result <result.json>
uv run --locked python scripts/benchmark_runtime.py compare \
  --baseline <base-result.json> --result <candidate-result.json> \
  --max-regression-percent 20
```

The PR workflow benchmarks the exact base and candidate revisions on the same runner with
three repetitions and fails when comparable p50 wall time regresses by more than 20%.
Nightly 1k/10k and weekly 100k tiers remain telemetry because hosted-runner
noise is too high for a single absolute-time gate. Results record p50/p95 timings,
files-per-second throughput, Python allocation peaks, and process peak RSS where observable.
Cross-runner deltas remain telemetry, not proof of a product regression by themselves.

The package gate builds the npm tarball in a temporary directory and rejects tests,
development tooling, caches, local paths, and real review runs from the public package.

## License and upstream provenance

Review Craft is MIT licensed. Its workflow design was informed by the public
Apache-2.0 `openai/codex-security` project at the revision recorded in
`THIRD_PARTY_NOTICES.md`. Review Craft independently implements its general
engineering-review contracts and does not vendor the Codex Security runtime.
