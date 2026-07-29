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

Version `0.3.0` provides read-only repository, Git diff, and focused-dimension
review workflows. Command receipts have unique sequences and content-bound output,
validation rebuilds deterministic repository maps from the bound source, and the
repository includes an executable, content-bound host evaluation protocol. The target
source stays read-only; canonical run and eval artifacts are written outside the target
repository by default.

The following are intentionally not implemented in 0.3.0: deep multi-pass review,
automated fixes, fix verification, historical comparison, SARIF, MCP, custom UI,
and a cloud service.

## What makes it different

Review Craft requires:

- a Project Quality Model grounded in real project goals and non-goals;
- deterministic per-file coverage accounting;
- a candidate ledger separate from validated findings;
- independent severity and remediation priority;
- explicit `KEEP`, `CLEAN_UP`, `MERGE`, `REPLACE`, `REWRITE`, `DELETE`, `DEFER`,
  `MEASURE`, and `DOCUMENT` decisions;
- evidence-gated scoring;
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

Review Craft complements these tools. Version 0.3.0 does not claim to replace or
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

## Use as a source skill

Codex can load a checked-out skill from a repository-local `.agents/skills` path or
from the user's skill directory. Pi can load the source directly without installing:

```text
pi --skill ./skills/review-craft
```

Or install the public Pi package:

```text
pi install npm:@bigking67/review-craft
```

Then request:

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

Validate and finalize canonical artifacts:

```bash
python3 skills/review-craft/scripts/review_craft.py validate --run-dir <run-dir>
python3 skills/review-craft/scripts/review_craft.py finalize --run-dir <run-dir>
```

Do not edit `report.md` directly. Correct the canonical JSON and rerun finalization.

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
Evidence commands targeting the same run are serialized with an OS-managed file lock.
This preserves receipt sequence and mutation attribution across concurrent callers; it
does not make configured commands run in parallel.

Repository comments, README files, issues, logs, and fixtures are untrusted analysis
data. Only current user instructions, scoped `AGENTS.md`, and the structured
`.review-craft.json` control the workflow.

Version 0.3 creates `review-craft.run.v3` artifacts. Finalized v0.1/v0.2 reports
remain historical outputs; finalize an unfinished old run with its matching runtime
or restart it with v0.3 preflight. Review Craft never mutates an old run in place.

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

Run the same full suite with `--treatment ORDINARY_PROMPT` and identical host metadata for
a matched baseline. Version 0.3 does not claim a golden or comparative quality result until
those real-host artifacts exist and validate.
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
