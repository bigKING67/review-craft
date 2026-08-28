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

- Project: `tt-a1i/simplify-codebase`
- Source: <https://github.com/tt-a1i/simplify-codebase>
- Reviewed revision: `add872f3db2a96f90081bedc070dde5d723afa95`
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
- Reviewed revision: `397c8660da6d3d873a91e18c2ca2f22cac1f0ac1`
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

Review Craft tracks selected runtime-contract surfaces from Alibaba's public Open Code
Review project as a non-normative learning source. No source or text from the reviewed
revision has been copied or adapted. Registration does not make its Go CLI, provider
runtime, subagent model, default exclusions, automatic mutation behavior, filtering
policy, or benchmark claims part of Review Craft.

- Project: `alibaba/open-code-review`
- Source: <https://github.com/alibaba/open-code-review>
- Reviewed revision: `5d255d160f9707b05537fd933d7adb68ba999c88`
- Watched source paths: `internal/session/manifest.go`,
  `internal/session/resume_identity.go`, `internal/diff/resolver.go`,
  `internal/config/rules/system_rules.go`, `internal/model/preview.go`,
  `internal/agent/preview.go`, and `skills/open-code-review/SKILL.md`
- License: Apache-2.0

If Review Craft later incorporates specific source or text, add the applicable Apache-2.0
license, retained notices, modified-file notice, and file-level provenance before
distribution. Until then, this entry records only the reviewed learning boundary.
