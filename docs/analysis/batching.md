# Batching benchmark

Shared-guidance requests reduced input tokens but changed some field labels on
the public measurement-unit and airport examples. **Per-entry requests remain
the default.** Use `--batch-size auto` explicitly to evaluate automatic packing
on your own data before adopting its scores or thresholds.

## Method

The checked-in [raw results](../assets/results/batching-benchmark.json) contain
actual Jev responses, usage, timings, and assessment timestamps. The first
assessment was captured at `2026-09-27T03:31:30.059619+00:00`, using `jev-1.13.0`.

The script scans the first three entries from each public example, with each
example's configured fields and guidance. It compares `--batch-size 1`, `8`, and
`auto`, with caching disabled. The long-guidance cases repeat the same guidance
20 times to isolate context costs. There is one run per condition. This is a
small smoke comparison, not a statistically powered accuracy study or MONDO
validation. Timings include client setup and sequential calls.

```sh
# Packing estimates only; no credentials or network calls.
uv run python scripts/benchmark_batching.py

# Real API calls on public example data.
uv run python scripts/benchmark_batching.py --live --output /tmp/batching.json
```

## Observed input usage and label differences

Each per-entry run made three requests. Each forced shared-guidance run made one.

| Example | Guidance repetitions | Per-entry input tokens | Shared input tokens | Reduction | Changed field labels |
| --- | --- | ---: | ---: | ---: | ---: |
| units | 1× | 2,473 | 2,127 | 14.0% | 2/7 |
| units | 20× | 4,639 | 2,849 | 38.6% | 2/7 |
| airports | 1× | 3,110 | 2,846 | 8.5% | 1/9 |
| airports | 20× | 9,722 | 5,050 | 48.1% | 2/9 |

Automatic mode retained per-entry requests for the short-guidance cases and
selected shared guidance for the long-guidance cases. Its long-guidance runs
had the same input-token totals and label-change counts as forced batching.

The shared layout flagged valid broad unit definitions as anomalous, and in the
airport example also flagged coordinates on a record whose intentionally
corrupted field was its country code. These differences are large enough that
we have not enabled automatic mode by default. Changing how entry context is
presented to the model can change assessments; question independence alone does
not establish equivalence between prompt layouts.

## Next validation

Compare repeated runs on representative, reviewed MONDO terms with realistic
short and long guidance. Measure false positives, missed anomalies, probability
and confidence changes, cost, and latency. Keep separate cache identities for the
two layouts throughout. A future default change should be based on that evidence.

See [batching controls and limits](../guides/guidance.md#batching-and-large-guidance-files).
