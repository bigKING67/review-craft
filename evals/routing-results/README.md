# Routing Evaluation Results

Review Craft keeps implicit invocation disabled until a current real-host routing result is
bound to the exact `SKILL.md` description and `agents/openai.yaml` interface and every
repetition passes the published thresholds.

The bilingual suite contains 60 cases: 30 Chinese, 30 English, 50 implicit, and 10 explicit.
It distinguishes Review Craft from native review, visual design critique, deep security
validation, and direct implementation or explanation tasks. High-cost negative cases make
accidental activation observable rather than treating all false positives equally.
Where a bounded request can reasonably use either direct execution or native review, both
routes are accepted while `REVIEW_CRAFT` remains forbidden. Workflow accuracy therefore
measures safe routing rather than forcing an artificial distinction between low-cost routes.

Every repetition must satisfy all thresholds independently:

- implicit precision: at least 95%;
- implicit recall: at least 85%;
- explicit activation: 100%;
- workflow accuracy: at least 90%;
- high-cost false-trigger rate: at most 3%.

Run a real-host result outside the repository because it can incur model cost:

```text
uv run --locked python scripts/run_evals.py run-routing \
  --output-root <external-directory> \
  --repetitions 2 \
  --adapter-command python3 scripts/codex_eval_adapter.py \
  --model <model> --reasoning <reasoning>

uv run --locked python scripts/run_evals.py validate-routing \
  --result <routing-result.json>
```

`run-routing` always preserves a structurally valid result. It exits `0` with status
`PASSED` only when every repetition passes the thresholds; a valid result that misses any
threshold exits `2` with status `FAILED`. `validate-routing` checks artifact integrity and
bindings independently, so a threshold-failing result can still be structurally valid.

The result measures the adapter's structured `ROUTING_DECISION`. Codex JSONL does not expose
a stable Skill-activation receipt, so this is a route-selection evaluation, not proof that a
particular host loaded the Skill. Results are also bound to one suite, metadata revision,
model, reasoning profile, adapter, and repetition count; they do not establish cross-model
or cross-version stability.

The current content-bound real-host result is published in `current/result.json` with its
exact suite in `current/suite.json`. It completed on 2026-08-19 using `gpt-5.6-sol` at
`medium` reasoning: both repetitions reached 100% implicit precision, recall, and explicit
activation, with 100% and 98.33% workflow accuracy and no high-cost false triggers.
Synthetic adapter results remain contract tests only and cannot enable implicit invocation.
