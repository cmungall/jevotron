# Review & export

Show anomalies sorted by confidence, as YAML:

```sh
jt scan data.csv -a -s -O yaml
# Long form:
jt scan data.csv --anomalies-only --sort-confidence --output-format yaml
```

`--anomalies-only` selects entries with at least one field classified as
`ANOMALY` (or your configured `anomaly_label`). `--sort-confidence` puts the
entry with the most confident anomalous field first. All assessed fields remain
in each entry for context. Without `--anomalies-only`, confidence sorting uses
the confidence of the highest-scoring field. Ties retain input order.

To sort warnings by anomaly probability instead:

```sh
jevotron scan data.csv --warnings-only --sort-score \
  --output-format csv -o warnings.csv
```

Keep the same guidance, fields, and exemplars as your first run to reuse its
cache. Only reporting options change here.

## Filter and order with SQL

Use DuckDB row expressions to select classifications, probabilities, or metadata:

```sh
jt scan data.csv --where "label = 'ANOMALY'" --order-by "confidence DESC" -O yaml
jt scan data.csv --where "label = 'NORMAL'"
jt scan data.csv --where "score >= 0.8 AND NOT cached" --order-by "score DESC, id ASC"
```

Quote the whole expression for your shell, and use SQL single quotes for string
values. `--order-by` defaults to ascending order unless you specify `DESC`.
Ties retain input order. It cannot be combined with `--sort-score` or
`--sort-confidence`.

These expressions filter **assessment results**, after inference or cache lookup.
They do not filter input records or reduce the number of API requests. Syntax
errors and unknown columns fail before inference or opening the output file;
data-dependent errors, such as an invalid cast of a field value, can occur later.
`--where` combines with `-a`, `-w`, and `-t` using AND.

Labels are ordinary strings. A custom config with labels such as `HARMFUL` and
`MISLEADING` can use the same reporting options:

```sh
jt scan traces.jsonl --config trace_config.py --rows fields \
  --where "label IN ('HARMFUL', 'MISLEADING') AND confidence >= 0.9" \
  --order-by "confidence DESC" -O yaml
```

### Choose what a row means

`--rows entries` is the default. Each row contains the entire entry assessment,
including its nested `fields`. Its `label`, `score`, and `confidence` come from
the highest anomaly-scoring field. Ties use the first field in the assessment.
Filtering an entry retains all its assessed fields for context.

`--rows fields` emits one row per field assessment. Now `label`, `score`,
`confidence`, and `warning` describe that individual field:

```sh
jt scan data.csv --rows fields \
  --where "label = 'ANOMALY' AND confidence >= 0.9" \
  --order-by "confidence DESC, score DESC" -O yaml
```

| Columns | Available in |
| --- | --- |
| `id`, `source`, `label`, `score`, `confidence`, `warning` | Both views |
| `model`, `assessed_at`, `request_hash`, `cached`, `absent`, `usage` | Both views |
| `fields` | Entries: nested field assessments |
| `path`, `value`, `probabilities` | Fields: assessed field and its distribution |
| `entry_label`, `entry_score`, `entry_confidence`, `entry_warning` | Fields: the parent entry's summary |

`id` is the entry ID in both views; combine it with `path` to identify a field.
`score` and `confidence` are numeric; `warning` and `cached` are booleans.
IDs, source locations, model names, hashes, and timestamps are strings.
`fields`, `value`, `probabilities`, `absent`, and `usage` are JSON columns in SQL,
preserving arbitrary nested values in the output. For example:

```sh
jt scan data.csv --rows fields \
  --where "CAST(probabilities ->> 'ANOMALY' AS DOUBLE) >= 0.8"
```

In field mode, `-a` selects fields bearing the configured anomaly label, and
`-t` / `-w` filter by field score. In entry mode, `-a` means **any** field has the
anomaly label, which can differ from `--where "label = 'ANOMALY'"` with custom
multiclass criteria. Likewise, `-a -s` orders entries by their most confident
anomalous field, whereas `--order-by "confidence DESC"` uses the entry summary.

Expressions are limited to individual rows: subqueries, aggregates, window
functions, and additional SQL statements are not supported. Filtering runs in
batches of up to 256 report rows. Global ordering buffers the full report in an
in-memory DuckDB database. Scans without SQL options retain ordinary streaming.

## Read the scores

Each field receives a `NORMAL` / `ANOMALY` classification, the full label
probability distribution, and Jev's confidence. Field questions are packed into
requests according to the [batching options](guidance.md#batching-and-large-guidance-files).
One request may cover several entries, and a large entry may span several requests.

| Result | Meaning |
| --- | --- |
| Field `score` | Probability of the anomaly label |
| Entry `score` | Maximum field anomaly probability |
| Entry `label` | Chosen label of the highest-scoring field |
| Entry `confidence` | Confidence of that same highest-scoring field |
| `warning` | Whether the entry score meets or exceeds the threshold |

The combined score is a review priority, not a calibrated probability that any
field is wrong. Confidence is a separate API value; it is not used as the score.

## Adjust the review threshold

```sh
jt scan data.csv -t 0.8 --sort-score
```

An explicit `--threshold` / `-t` now filters output to entries whose anomaly
score is **at least** that value; you no longer need `--warnings-only` as well.
It also sets the `warning` flag. This uses anomaly probability, independently of
the chosen label and the separate confidence value. Combined with
`--anomalies-only`, both filters apply.

Without an explicit threshold or output filter, all entries are emitted. The
default warning threshold is `0.5`, or the value in your config. Use
`--warnings-only` / `-w` to filter by that configured threshold. Changing the
threshold does not trigger fresh inference: cached results are filtered again.

Useful shortcuts: `-l` for `--limit`, `-f` for `--field` (repeatable), `-O` for
`--output-format`, and `-o` for the output file. For example:

```sh
jt scan data.csv -l 100 -f /name -f /description -a -s -O yaml -o anomalies.yaml
```

`--limit` caps entries assessed, before output filtering and sorting. Omit it to
assess all entries; filtering only reduces the report, not inference work.

## Pick an output format

=== "JSONL"

    ```sh
    jevotron scan data.csv -o results.jsonl
    ```

    One JSON object per entry, with nested field results. Includes `id`, `source`,
    `label`, `score`, `warning`, `fields`, `absent`, `model`, `assessed_at`,
    `request_hash`, `cached`, and `usage`. `absent` lists selected paths the
    entry did not carry (see [sparse fields](files.md#entries-that-do-not-all-carry-the-same-fields)). Each field includes its path, value, label, probabilities,
    confidence, and anomaly score.

=== "CSV"

    ```sh
    jevotron scan data.csv --output-format csv -o results.csv
    ```

    One row per assessed field, repeating the entry score and metadata.
    Nested field values, probabilities, and the `absent` list are JSON text. `--warnings-only`
    retains all assessed fields of warning entries for review.

    In the default entry view, `label`, `score`, and `warning` describe the entry;
    `field_label`, `field_score`, and `confidence` describe the field, and
    `entry_confidence` supplies the entry confidence. Filtering and ordering act
    on entry summaries before this CSV expansion.

    With `--rows fields`, the CSV columns match the field view above: `path`,
    `label`, `score`, `confidence`, and `warning` describe the field, and the
    `entry_*` columns retain the parent summary. SQL filtering and ordering then
    act on individual CSV rows.

=== "YAML"

    ```sh
    jt scan data.csv -O yaml -o results.yaml
    ```

    A single YAML list with the same nested entry and field results as JSONL.
    Entries stream as they complete; an empty report is `[]`.

Output files must be distinct from the input, Python config, guidance document,
exemplars, and assessment cache. Both preview and scan reject symlink or hardlink
aliases of those files before opening the output.

`assessed_at` is the original assessment timestamp. `usage` on cached results
describes the original assessment and does not represent new token usage.
For shared or split assessments, `usage.batch_request_hashes` references actual
requests in the cache's `batch_requests` table. Use the stderr scan summary for
new request-level token totals; it avoids counting shared guidance repeatedly.

## Streaming and incomplete runs

By default, results stream as entries complete; SQL filtering emits completed
batches. `--sort-score`, `--sort-confidence`, and `--order-by` buffer results and
emit them only after a successful scan. Choose one sort option per run. If a scan fails, stderr reports
`INCOMPLETE` and the command exits with status 1. Streaming output may contain
partial results; sorted output will not contain the buffered rows. Successful
assessments remain cached in either case.

See [shell pipelines](shell.md) for scripts that check exit status before using
the output.
