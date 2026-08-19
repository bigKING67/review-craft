# Runtime Benchmark Results

No release baseline is claimed yet. Run results outside the repository first, validate the
JSON, and only commit an immutable baseline from a clean source revision with the exact
environment and parameters recorded.

The default command runs the 1k-file tier. The 10k and 100k tiers are explicit because they
create and parse large temporary repositories:

```text
uv run --locked python scripts/benchmark_runtime.py run
uv run --locked python scripts/benchmark_runtime.py run --full
uv run --locked python scripts/benchmark_runtime.py validate --result <result.json>
```

CI treats these results as non-blocking telemetry: pull requests run a 1k smoke, the nightly
schedule runs 1k and 10k with five repetitions, and the weekly schedule runs 100k with three
repetitions. The workflow uploads the validated JSON instead of committing mutable runner
results here.

There is no release baseline or pass/fail performance threshold yet. Do not introduce a
percentage gate until an immutable clean-source baseline records the exact source, runner,
Python, platform, parameters, and content hash. When a baseline exists, compare repeated-run
medians and require an explicit baseline update rather than silently accepting a slower run.

`pythonAllocatedPeakBytes` covers only in-process Python operations. Subprocess preflight
and validation memory is marked `NOT_CAPTURED_FOR_SUBPROCESS`; do not treat it as zero.
