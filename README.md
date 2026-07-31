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

Version `0.4.1` provides read-only repository, Git diff, and focused-dimension review
workflows plus an explicitly authorized remediation-verification protocol. The runtime
binds selected findings to a sealed review, captures the exact pre-change source,
executes only selected configured verification commands, and content-binds the resulting
source diff, command receipts, and HUMAN/AGENT_ASSISTED/AUTOMATED assessment. It never
edits target source. Canonical run, fix, and eval artifacts are written outside the
target repository by default. A sanitized matched real-host Golden snapshot from v0.3
remains tracked under `evals/golden-results/705dbac-gpt-5.6-sol/`.

The following are intentionally not implemented in 0.4.1: deep multi-pass review,
automatic source mutation, historical comparison, SARIF, MCP, custom UI, and a cloud
service.

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

Review Craft complements these tools. Version 0.4.1 does not claim to replace or
outperform Codex Security.

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
  --ref v0.4.1 \
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

The result should report `"ready": true` and `"version": "0.4.1"`.
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
authorized the implementation and the host has applied only the selected changes,
create a `review-craft.fix-assessment` JSON file and verify it:

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

Each fix session has one terminal verification attempt. `verify-fix` holds an exclusive
session lock through command execution and terminal artifact creation, so concurrent or
sequential callers cannot create competing results. A completed session is read-only.
If a crash leaves command receipts or only one terminal artifact, the session fails closed;
run `prepare-fix` again to create a new session for an explicit rerun. `validate-fix`
requires the receipt ledger to match the final verification references exactly.

## Configuration

Copy `skills/review-craft/templates/review-config.json` to
`.review-craft.json` in a target repository.
Commands use argv arrays and execute with `shell=false`. A configured command is not
a security sandbox and does not override host approvals, network policy, or sandboxing.
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
does not make configured commands run in parallel. Fix verification adds a separate
session-level lock around its complete one-attempt lifecycle.
Semantic claims and copied command artifacts are part of receipt identity. Fix verification
preserves the same identity and treats a failed semantic assertion as a failed verification
command even when the subprocess itself exited zero.

Repository comments, README files, issues, logs, and fixtures are untrusted analysis
data. Only current user instructions, scoped `AGENTS.md`, and the structured
`.review-craft.json` control the workflow.

Version 0.4 continues to create backward-compatible `review-craft.run.v3` review
artifacts and adds separate `review-craft.fix.v1` remediation artifacts. Finalized
v0.1/v0.2/v0.3 reports remain historical outputs; finalize an unfinished old run with
its matching runtime or restart it with v0.4 preflight. Review Craft never mutates an
old run in place. New run.v3 scorecards report both accounted and reviewed coverage;
`coveragePercent` now carries the reviewed value. Historical run.v3 scorecards without
the new optional fields retain their original accounting interpretation during
validation.
The deterministic report labels `focus` and `diff` scores as scope-limited rather than
repository-wide, separates confirmed findings from evidence gaps and remaining risks, and
lists verified command claims and captured evidence artifacts.

## Validate this repository

```bash
uv sync --locked --group dev
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m unittest discover -s tests -p 'test_*.py'
uv run --locked python scripts/validate.py
python3 scripts/package_check.py
python3 scripts/release_gate.py
```

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

Runtime scale measurements are also explicit and external by default. The normal command
runs the 1k-file tier; `--full` additionally runs 10k and 100k tiers and can take materially
longer:

```text
uv run --locked python scripts/benchmark_runtime.py run
uv run --locked python scripts/benchmark_runtime.py validate --result <result.json>
```

The package gate builds the npm tarball in a temporary directory and rejects tests,
development tooling, caches, local paths, and real review runs from the public package.

## License and upstream provenance

Review Craft is MIT licensed. Its workflow design was informed by the public
Apache-2.0 `openai/codex-security` project at the revision recorded in
`THIRD_PARTY_NOTICES.md`. Review Craft independently implements its general
engineering-review contracts and does not vendor the Codex Security runtime.
