# Review & export

Start with a sorted warning report:

```sh
jevotron scan data.csv --warnings-only --sort-score \
  --output-format csv -o warnings.csv
```

Keep the same guidance, fields, and exemplars as your first run to reuse its
cache. Only reporting options change here.

## Read the scores

Each field receives a `NORMAL` / `ANOMALY` classification, the full label
probability distribution, and Jev's confidence. All field questions for an entry
are batched into one request.

| Result | Meaning |
| --- | --- |
| Field `score` | Probability of the anomaly label |
| Entry `score` | Maximum field anomaly probability |
| Entry `label` | Chosen label of the highest-scoring field |
| `warning` | Whether the entry score meets or exceeds the threshold |

The combined score is a review priority, not a calibrated probability that any
field is wrong. Confidence is a separate API value; it is not used as the score.

## Adjust the review threshold

```sh
jevotron scan data.csv --threshold 0.8 --warnings-only
```

The default threshold is `0.5`. Changing it does not trigger fresh inference.
The threshold determines warnings independently of the chosen label.

## Pick an output format

=== "JSONL"

    ```sh
    jevotron scan data.csv -o results.jsonl
    ```

    One JSON object per entry, with nested field results. Includes `id`, `source`,
    `label`, `score`, `warning`, `fields`, `model`, `assessed_at`, `request_hash`,
    `cached`, `usage`, and the full original `entry`. Each field includes its path,
    value, label, probabilities, confidence, and anomaly score.

=== "CSV"

    ```sh
    jevotron scan data.csv --output-format csv -o results.csv
    ```

    One row per assessed field, repeating the entry score and metadata.
    Nested field values and probabilities are JSON text. `--warnings-only`
    retains all assessed fields of warning entries for review.

`assessed_at` is the original assessment timestamp. `usage` on cached results
describes the original call and does not represent new token usage.

## Streaming and incomplete runs

By default, results stream as entries complete. `--sort-score` buffers results
and emits them only after a successful scan. If a scan fails, stderr reports
`INCOMPLETE` and the command exits with status 1. Streaming output may contain
partial results; sorted output will not contain the buffered rows. Successful
assessments remain cached in either case.

See [shell pipelines](shell.md) for scripts that check exit status before using
the output.

For durable human decisions, use the [human review queue](review.md). It works
offline and exports deliberately selected decisions as exemplars.
