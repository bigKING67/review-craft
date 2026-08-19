# Routing Evaluation Results

Review Craft keeps implicit invocation disabled until a current real-host routing result is
bound to the exact `SKILL.md` description and `agents/openai.yaml` interface and every
repetition passes the published thresholds.

The bilingual suite contains 60 cases: 30 Chinese, 30 English, 50 implicit, and 10 explicit.
It distinguishes Review Craft from native review, visual design critique, deep security
validation, and direct implementation or explanation tasks. High-cost negative cases make
accidental activation observable rather than treating all false positives equally.

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

The result measures the adapter's structured `ROUTING_DECISION`. Codex JSONL does not expose
a stable Skill-activation receipt, so this is a route-selection evaluation, not proof that a
particular host loaded the Skill. Results are also bound to one suite, metadata revision,
model, reasoning profile, adapter, and repetition count; they do not establish cross-model
or cross-version stability.

No real-host current result is published yet. Synthetic adapter results are contract tests
only and are not eligible to enable implicit invocation.
