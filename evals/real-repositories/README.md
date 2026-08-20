# Real Repository Benchmark v1

This benchmark uses eight public repositories at immutable commits. Each benchmark
revision is the direct parent of a real upstream fix commit. The suite covers two Python
projects, two Node.js projects, Electron, Go, Rust, and JVM code, including mature
compatibility-sensitive projects.

Each repository defines five treatment-safe probes:

- a real finding bound privately to an upstream fix;
- a justified `KEEP` decision;
- a false-positive decoy;
- a claim that requires measurement;
- a claim that must remain blocked by an evidence gap.

`evals/specs/real-repositories.json` is the oracle-bearing suite. Never pass that file to a
review treatment. Generate or use `current/blind-suite.json`, which excludes upstream fix
identities, expected dispositions, decisions, rationales, and other answer-bearing data.

`current/materialization.json` records the live source verification performed on
2026-08-20. All eight source checkouts were clean, every declared scope existed, and each
benchmark revision was verified as the direct parent of its bound upstream fix. This is a
source receipt, not a model-quality result.

The benchmark is not Golden until all declared treatments, repetitions, and model
configurations complete, the outputs are independently adjudicated, and the stability
report validates. Do not use the materialization receipt alone to claim review accuracy,
cross-model stability, or human agreement.

```text
uv run --locked python scripts/real_repository_benchmark.py validate-suite

uv run --locked python scripts/real_repository_benchmark.py materialize \
  --workspace-root <external-empty-directory>

uv run --locked python scripts/real_repository_benchmark.py blind-suite \
  --output <external-path>/blind-suite.json

uv run --locked python scripts/real_repository_benchmark.py run \
  --materialization evals/real-repositories/current/materialization.json \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --workspace-root <materialized-workspace-root> \
  --adapter-config <outside-adapters.json> \
  --run-dir <external-campaign-directory>

uv run --locked python scripts/real_repository_benchmark.py validate-campaign \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --campaign <campaign.json>

uv run --locked python scripts/real_repository_benchmark.py prepare-adjudication \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --campaign <campaign.json> --output-dir <empty-adjudication-directory> \
  --adjudicator human-a --adjudicator human-b

# Give each reviewer only packet-<id>.json and submission-<id>.json. Keep the
# coordinator mapping private. After each reviewer fills every label/rationale:
uv run --locked python scripts/real_repository_benchmark.py \
  finalize-adjudication-submission \
  --packet <packet-human-a.json> --submission <submission-human-a.json>

uv run --locked python scripts/real_repository_benchmark.py assemble-adjudication \
  --campaign <campaign.json> --mapping <coordinator-mapping.json> \
  --submission <submission-human-a.json> --submission <submission-human-b.json> \
  --output <independent-adjudication.json>

uv run --locked python scripts/real_repository_benchmark.py validate-adjudication \
  --campaign <campaign.json> --adjudication <independent-adjudication.json>

uv run --locked python scripts/real_repository_benchmark.py analyze-stability \
  --blind-suite evals/real-repositories/current/blind-suite.json \
  --campaign <campaign.json> --adjudication <independent-adjudication.json> \
  --output <stability.json>
```

The full matrix is eight repositories by three treatments by at least two real-host model
configurations by three repetitions. `--repository`, `--treatment`, and `--repetitions` may
produce a labeled partial smoke, but partial output is never Golden. The runner stores exact
prompt/output hashes, usage, wall time, adapter provenance, completion state, and before/after
Git state; any target mutation fails the sample. Adapter processes run through the canonical
process-lifecycle boundary, so timeout terminates the inherited process tree. The Codex adapter
streams JSONL and atomically checkpoints sanitized tool traces and usage while it runs. A timed
out sample therefore retains partial stdout and completed tool-call evidence when available;
token counts remain null when no complete host usage event was emitted.

Adjudication v2 covers every probe response plus every additional finding from completed
samples. Reviewer packets use reviewer-specific opaque item IDs and omit sample, treatment,
model, and repetition identities. Packet order is independently deterministic per reviewer.
The coordinator-only mapping is required to assemble the two completed submissions; a blank
template or a single reviewer cannot produce a valid v2 adjudication. Legacy v1 adjudication
remains validation-compatible for historical campaigns, but it covers additional findings only.
