# Human review & selected exemplars

Use `jt review` (or `jevotron review`) to review assessed fields offline and keep
human decisions in `.jevotron/reviews.sqlite3`. The model cache and probabilities
remain separate. Use a separate review store for each dataset or project, and
choose stable entry IDs with `--id-column` or a project config when scanning.
Positional IDs cannot track records reliably after reordering.

## Try the bundled inventory without an API key

From the checkout:

```sh
jt review import docs/assets/results/inventory.jsonl \
  --source examples/inventory/items.yaml --config examples/inventory/jev_config.py
jt review list --unreviewed --warnings-only
jt review start --unreviewed --warnings-only
```

The terminal interface shows the full entry, selected field and model probabilities,
assessment date, model, request hash, and any reference context. Choose **c** to
confirm an error, **v** for a valid exception, **d** to defer, **s** to skip, or **q**
to quit. You can add a note. Each decision is saved immediately and survives a
process restart. Deferred fields remain in the unreviewed queue.

`--warnings-only` includes **all assessed fields of warning entries**, including
fields whose model score is low. It follows the warning flag recorded in the scan
report. Without that flag, every imported field is available for review.

The captured inventory report predates full-entry reports, so the import command
above reconstructs its entries from the source and config. A reconstructed request
must match the report's request hash. It never assumes that selected fields are a
complete entry. If the source or guidance has changed, use the original source
and config, or recover the original request from a cache:

```sh
jt review import results.jsonl --cache .jevotron/cache.sqlite3
```

New JSONL scan reports include `entry` and can be imported directly, including
reports that assessed only a subset of fields:

```sh
jt review import results.jsonl
```

Review import accepts JSONL reports. A CSV field export does not provide the full
entry context required for this workflow.

## Record decisions in scripts

```sh
jt review decide cable /quantity confirmed-error --note 'Count verified against stock.'
jt review decide mug /category confirmed-error --note 'Ceramic mugs are kitchenware.'
jt review export > decisions.jsonl
```

`decide` prints a JSON object containing its durable `decision_id`. Supported
values are `confirmed-error`, `valid-exception`, and `deferred`. The exact JSON
Pointer is required; use `''` for a whole-entry field. To require the assessment
seen by your script, add `--request-hash HASH`. The command rejects a decision if
the current request has changed.

`review list` and `review export` write JSONL to stdout. Each exported decision
contains `decision_id`, `entry_id`, `field_path`, original `value`, full `entry`,
`request_hash`, `assessed_at`, `model`, `source`, `warning`, original field
`assessment` (including probabilities), human `decision`, `note`, and `reviewed_at`.
Optional assessment `context` is preserved, including reference provenance when
available. Source/config recovery retains the preview context envelope. A model
cache stores only model-visible reference data; when it is the only source of
context, the review marks its provenance as unavailable rather than inventing
file locations. Decisions form an append-only history:
recording another decision adds a record rather than rewriting the original one.

## Reuse reviews and reconsider changed requests

Importing an unchanged request reuses its field decisions. Changing entry content,
guidance, exemplars, or another model input changes the request hash. Previously
reviewed fields then appear as `reconsider`, with no inherited approval. Use
`review list --unreviewed` to find them and `review list --history` to inspect
superseded assessments. Reassessing the same request retains its decisions; each
human record still contains the assessment provenance seen at the time of review.
Moving or reordering unchanged reference files can update current provenance
without invalidating a decision, because the model-visible context is unchanged.

An import updates the entries present in that report. Entries absent from a partial
or warnings-only report remain in the store. Use stable IDs and the same project
store for subsequent versions.

## Choose exemplars explicitly

Select the actual decision IDs returned by `decide` or `export`:

```sh
jt review exemplars --decision 1 --decision 2 > exemplars.json
jt preview examples/inventory/items.yaml --config examples/inventory/jev_config.py \
  --exemplars exemplars.json --limit 1
```

For a fresh store, the first two decisions have IDs 1 and 2. Export maps confirmed
errors to `ANOMALY` and valid exceptions to `NORMAL`, preserving the original entry
and selected field paths. If the judgment used reference context, that model-visible
data is included as `context` in the exemplar; path-only provenance is excluded.
Examples are grouped by entry and reference data, so differing evidence stays
separate while mere file-location changes coalesce. For custom classification labels, supply `--normal-label`
and `--anomaly-label`. Deferred decisions, superseded requests, missing IDs, and
contradictory labels for the same entry and field are rejected with an explanation.
Nothing is selected automatically, and export does not change the original data.

The [complete inventory review script](../downloads/review/inventory.sh) deliberately
reviews the two known synthetic errors, exports their decision history and selected
exemplars, and runs `preview` with those exemplars. It requires `jq` and makes no
API calls. Run it from a checkout:

```sh
uv run bash examples/review/inventory.sh .jevotron/review-demo
```

Every command accepts `--store PATH` to choose a different review database.
