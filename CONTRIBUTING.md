# Contributing

Keep changes scoped to Review Craft's evidence-driven engineering-review workflow.
Before proposing a change:

1. Preserve `skills/review-craft/` as the canonical runtime.
2. Add tests for contract or behavior changes.
3. Add a negative fixture when a rule could encourage over-review or unnecessary rewrites.
4. Run `uv run --locked python scripts/complexity_budget.py` and
   `python3 scripts/release_gate.py` after `uv sync --locked --group dev`.
5. Do not commit real repository findings, credentials, local paths, caches, or run artifacts.

Planned features should remain in issues or roadmap text until a real implementation,
contract, and validation path exist.
