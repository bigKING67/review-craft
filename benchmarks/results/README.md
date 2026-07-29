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

`pythonAllocatedPeakBytes` covers only in-process Python operations. Subprocess preflight
and validation memory is marked `NOT_CAPTURED_FOR_SUBPROCESS`; do not treat it as zero.
