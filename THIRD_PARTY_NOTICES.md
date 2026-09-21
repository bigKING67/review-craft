# Third-Party Notices

Review Craft's workflow design was informed by the public Codex Security project:

- Project: `openai/codex-security`
- Source: <https://github.com/openai/codex-security>
- Reviewed revision: `f22d4a36f26d16287bcdfd707b369116e02a08c3`
- License: Apache License 2.0

Review Craft independently implements repository inventory, candidate validation,
coverage accounting, and deterministic report projection for general engineering
review. Review Craft does not copy Codex Security's Workbench, MCP application,
TypeScript SDK, deep-scan orchestration, attack-path implementation, or report code.

If future versions incorporate specific upstream source or text, the corresponding
copyright, Apache-2.0 license, modified-file notice, and file-level provenance must
be added before distribution.

## simplify-codebase

Review Craft's `simplification.md` reference selectively adapts concepts and terminology
from the public `tt-a1i/simplify-codebase` project. It does not copy that project's mode
matrix, source-editing workflow, orchestration guidance, or independent reporting model.
The latest bounded review added stable finding identity, verified-locus, and concise-handoff
guidance to the existing selective adaptation. It does not add a topology claim, visual
companion, or cleanup-map renderer.

- Project: `tt-a1i/simplify-codebase`
- Source: <https://github.com/tt-a1i/simplify-codebase>
- Latest reviewed revision: `5da55efcb52db690e7406f06f827a23b15da2706` (2026-09-05)
- Original adaptation review: `add872f3db2a96f90081bedc070dde5d723afa95` (2026-08-28)
- Adapted source paths: `SKILL.md`, `references/investigation.md`,
  `references/boundaries-and-lifecycle.md`, `references/execution-and-recovery.md`, and
  `references/decision-records.md`
- License: MIT

## Cursor Team Kit thermo-nuclear-code-quality-review

Review Craft's `simplification.md` reference selectively adapts structural-review
concepts from Cursor's public `thermo-nuclear-code-quality-review` Skill. It does not
copy the hard universal file-line blocker, presumptive refactor blockers, default source
rewrite posture, Cursor Task subagent orchestration, or rhetoric as evidence.

- Project: `cursor/plugins`
- Source: <https://github.com/cursor/plugins/tree/main/cursor-team-kit/skills/thermo-nuclear-code-quality-review>
- Latest reviewed revision: `93b00b89ef425a9c1bac0d0b317dfc49c930ac99` (2026-09-05; tracked Skill blob unchanged)
- Original adaptation review: `397c8660da6d3d873a91e18c2ca2f22cac1f0ac1` (2026-08-28)
- Adapted source path:
  `cursor-team-kit/skills/thermo-nuclear-code-quality-review/SKILL.md`
- License: MIT

The upstream MIT copyright notices and shared license text follow:

```text
MIT License

Copyright (c) 2026 simplify-codebase contributors
Copyright 2026 Cursor

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Alibaba Open Code Review

Review Craft selectively adapts deterministic source-location anchoring and a sealed
coverage-denominator concept from Alibaba's public Open Code Review project through
independently implemented Python contracts. Current run.v5 validation binds every
inventory-owned coverage field one-to-one to the canonical source projection; it does not
copy Alibaba's run-terminal-state model. No source or text from the reviewed revision has
been copied. Remaining runtime contract surfaces stay non-normative watch candidates. This
boundary does not make its Go CLI, provider runtime, subagent model, default exclusions,
automatic mutation behavior, filtering policy, or benchmark claims part of Review Craft.
The latest bounded review also informed automatic-profile input confinement: Review Craft
uses only canonical-inventory ordinary text, records skipped input explicitly, and does not
follow an external symlink or load binary content. This is a narrow boundary principle, not
an adoption of Alibaba's project-rule loader or runtime.

- Project: `alibaba/open-code-review`
- Source: <https://github.com/alibaba/open-code-review>
- Latest reviewed revision: `2190f11fcd24ed74ed7a1120c5e63f901b1da658` (2026-09-05)
- Original adaptation review: `5d255d160f9707b05537fd933d7adb68ba999c88` (2026-08-28)
- Reviewed source paths: `internal/session/manifest.go`,
  `internal/session/resume_identity.go`, `internal/diff/resolver.go`,
  `internal/config/rules/system_rules.go`, `internal/model/preview.go`,
  `internal/agent/preview.go`, and `skills/open-code-review/SKILL.md`
- License: Apache-2.0

If Review Craft later incorporates specific source or text, add the applicable Apache-2.0
license, retained notices, modified-file notice, and file-level provenance before
distribution. Until then, this entry records the conceptual adaptation and remaining
learning boundary.

## Understand Anything

Review Craft tracks the public Understand Anything project as a non-normative reference
candidate (`tracked`), not an absorbed runtime or a verified quality endorsement. The
bounded review read selected source, test content, and the source-evidence audit; it did
not execute upstream tests or verify host support or performance. No upstream source or
text has been copied into Review Craft's runtime, and no runtime capability is added by
this registration.

- Project: `Egonex-AI/Understand-Anything`
- Source: <https://github.com/Egonex-AI/Understand-Anything>
- Reviewed revision: `6df3065f1d8ddc2ce3615314d1d493f36d6b1c80` (2026-09-21)
- License: MIT; upstream copyright holders are Yuxiang Lin and Infinite Universe, Inc.
- Reviewed source paths (exact Git blobs are pinned in `contracts/upstreams.json`):
  - `LICENSE`
  - `docs/incremental/source-evidence-audit.md`
  - `understand-anything-plugin/packages/core/src/plugins/symbol-evidence.ts`
  - `understand-anything-plugin/packages/core/src/plugins/symbol-scopes.test.ts`
  - `understand-anything-plugin/packages/core/src/staleness.ts`
  - `understand-anything-plugin/packages/core/src/fingerprint.ts`
  - `understand-anything-plugin/packages/core/src/change-classifier.ts`
  - `understand-anything-plugin/skills/understand-diff/SKILL.md`
  - `understand-anything-plugin/agents/graph-reviewer.md`

Watch candidates are the distinction between analyzer omissions and verified symbol
removal, scope invariants and composition tests, dependency-guided change-impact
investigation, and project-scoped freshness with explicit dirty, stale, and unknown
states. These may inform future bounded improvements to Review Craft's existing
contracts; they do not establish a second inventory or evidence authority.

The structural fingerprint's `COSMETIC` classification can include internal logic
changes when structural signatures match. It must not authorize skipping code review.
Graph schema validity, referential integrity, and nonempty graph structures do not prove
canonical per-file review coverage. Relationship graphs remain investigation aids;
missing static edges do not establish the absence of dynamic consumers.

Excluded surfaces include the knowledge-graph runtime and dashboard, mandatory
multi-agent orchestration, automatic update hooks, target-repository artifact writes,
and independent inventory, scoring, or reporting. Upstream runtime, host-support, and
performance claims are not local validation evidence.

Any future adoption must record the specific adapted surfaces and matching validation
before changing the status to `selective_absorbed`. If source or substantial text is
copied, retain the applicable MIT copyright and permission notice before distribution.
