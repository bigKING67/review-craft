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

Version `0.1.0` provides the standard, read-only repository-review workflow. The
target source stays read-only; canonical run artifacts are written outside the
target repository by default.

The following are intentionally not implemented in 0.1.0: deep multi-pass review,
diff review, automated fixes, historical comparison, SARIF, MCP, custom UI, and a
cloud service.

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
- a deterministic Markdown report generated from canonical JSON.

## Relationship to official OpenAI workflows

- Use ordinary Codex Review for a bounded PR, commit, branch, or working-tree diff.
- Use Codex Security for threat modeling, vulnerability discovery, exploitability,
  attack paths, PoCs, and security remediation validation.
- Use Review Craft for repository-wide, multi-dimensional engineering assessment
  and remediation governance.

Review Craft complements these tools. Version 0.1.0 does not claim to replace or
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

With an explicit configuration:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  preflight --target . --config .review-craft.json
```

Run a configured evidence command:

```bash
python3 skills/review-craft/scripts/review_craft.py \
  run-evidence --run-dir <run-dir> --command test
```

Validate and finalize canonical artifacts:

```bash
python3 skills/review-craft/scripts/review_craft.py validate --run-dir <run-dir>
python3 skills/review-craft/scripts/review_craft.py finalize --run-dir <run-dir>
```

Do not edit `report.md` directly. Correct the canonical JSON and rerun finalization.

## Configuration

Copy `.review-craft.example.json` to `.review-craft.json` in a target repository.
Commands use argv arrays and execute with `shell=false`. A configured command is not
a security sandbox and does not override host approvals, network policy, or sandboxing.

Repository comments, README files, issues, logs, and fixtures are untrusted analysis
data. Only current user instructions, scoped `AGENTS.md`, and the structured
`.review-craft.json` control the workflow.

## Validate this repository

```bash
uv sync --locked --group dev
PYTHONDONTWRITEBYTECODE=1 uv run --locked python -m unittest discover -s tests -p 'test_*.py'
uv run --locked python scripts/validate.py
python3 scripts/package_check.py
python3 scripts/release_gate.py
```

The package gate builds the npm tarball in a temporary directory and rejects tests,
development tooling, caches, local paths, and real review runs from the public package.

## License and upstream provenance

Review Craft is MIT licensed. Its workflow design was informed by the public
Apache-2.0 `openai/codex-security` project at the revision recorded in
`THIRD_PARTY_NOTICES.md`. Version 0.1.0 independently implements its general
engineering-review contracts and does not vendor the Codex Security runtime.
