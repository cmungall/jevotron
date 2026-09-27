# Compare versions & gate CI

Compare original datasets under the same rules, then fail a check only when a
field warning is new:

```sh
jt compare examples/airports/baseline.csv examples/airports/spiked.csv \
  --id-column ident --field /iso_country \
  --guidance-file examples/airports/guidance.md \
  --fail-on new-warning -o changes.jsonl --markdown changes.md
```

This uses the same Jev service and cache as `scan`. A cache miss requires
`TYPESAFE_API_KEY`; a fully cached comparison needs no key. With the bundled
offline fixture below, the result is two new country warnings (`EGLL`, `KJFK`)
and six unchanged entries. Live model probabilities may differ.

## Identity and changes

Entries match by stable ID, never by row position. Output is sorted by ID, so
reordering either input does not create added or removed entries. Entry status
is `added`, `removed`, `changed`, or `unchanged`; content comparisons include
unselected context fields and preserve JSON types. Source paths and row numbers
are provenance, not identity or content.

- CSV, JSON, JSONL, YAML, and TOML require `--id-column` or an explicitly configured
  parser with `id_column`. An explicit flag overrides the configured column.
- OBO stanza IDs, FASTA record IDs, and GMT names work directly. OBO stanzas must
  have exactly one nonempty ID; fallback line numbers are rejected.
- Databases preserve table namespaces and use non-null primary keys or
  `--id-column`. Positional database row fallbacks are rejected. Use `--table`
  to select tables or views, as with `scan`.
- A custom parser can supply its own stable `Chunk.id` values with the explicit
  `--parser-ids` assertion. This does not enable positional built-in IDs.

Missing, empty, collection-valued, or duplicate stable IDs stop the comparison.
Scalar IDs use the same string representation as `scan`; collisions after that
conversion are duplicate-ID errors. If a selected field exists in both versions
but is assessed on only one side, comparison rejects the incompatible coverage
instead of claiming that an unassessed warning was resolved. Truly added or
removed fields remain valid changes.
Both complete inputs are parsed and validated before inference. The current
implementation buffers both datasets and their results in memory; there is no
partial-input or `--limit` comparison. Two empty valid inputs produce an empty,
completed comparison; an empty side reports all other entries as added or removed.

Each selected field reports `new`, `resolved`, `persistent`, or `none` for its
warning transition. Added entries/fields with warnings count as new. Removed
entries/fields with warnings count as resolved; the absent side is explicit,
so removal is distinguishable from a corrected value. A warning persists when
both scores meet the threshold, even if its score increases.

## One assessment policy for both versions

One `--config`, guidance, exemplar set, model, and field-selection policy applies
to both inputs. Separate per-side inference configurations are not accepted.
This command compares raw datasets; saved scan reports are not comparison inputs.
If the actual model versions returned by the API/cache differ, comparison fails
with an error. Pin the model and use `--refresh` to make the assessments compatible.

`--threshold` applies to both sides, including cached assessments. Changing it
recomputes both warning states without inference and does not compare an old
threshold to a new one. Existing warnings therefore remain persistent instead
of becoming regressions solely because the threshold changed.

Every distinct request is assessed once per comparison. Unchanged requests use
the ordinary persistent cache; duplicates also share one assessment within a run,
including with `--refresh` or `--no-cache`. `cached_requests` counts unique
persistent-cache hits, not duplicate entries. Per-side `cached` and `usage`
describe that shared original assessment, not new billable calls for each side.

## Reports and exit statuses

JSONL starts with a `type: "comparison"`, `schema_version: 1` metadata record
containing counts, completion status, policy outcome, threshold, resolved models,
input settings, and a fingerprint of the shared inference settings. Each following
`type: "entry"` row includes before/after field values, labels, probabilities,
warning states, score deltas, source locations, request/content hashes, and original
assessment dates. A missing side is `null`, distinct from a field containing null.
Actual selected paths are recorded on each field; the inference fingerprint
excludes thresholds, input paths, and row order.

Use `--output-format markdown` for a concise primary report, or `--markdown PATH`
alongside JSONL. Markdown retains values, score changes, source locations, and
assessment dates for changed entries and persistent warnings. Unchanged fields
without warnings are summarized by counts. Treat JSONL as the complete record.

| Status | Meaning |
| --- | --- |
| `0` | Complete comparison; chosen policy passed |
| `1` | Incomplete comparison: input, configuration, cache, API, or output error |
| `2` | Invalid CLI syntax or option value |
| `3` | Complete comparison; chosen policy violated |
| `130` | Interrupted |

`--fail-on none` is the default. `new-warning` ignores persistent warnings;
`any-warning` fails for either new or persistent candidate warnings. Reports are
published even on status 3. Inference/input failures publish no report rows and
retain existing report files; successful assessments remain cached for retry.
Output paths, including hard links and symlinks, cannot alias either input,
the configuration/guidance/exemplar files, or the cache.

## Run the exact offline regression fixture

The checked-in fixture uses synthetic probabilities and the dedicated
`jev-offline-fixture` model. It is a reproducible software test, not a captured
Jev assessment, and its cache keys cannot replace normal model assessments.
From a source checkout:

```sh
uv sync --locked --group docs
uv run python examples/comparison/seed_cache.py .jevotron/airport-fixture.sqlite3
uv run jt compare examples/airports/baseline.csv examples/airports/spiked.csv \
  --id-column ident --field /iso_country --model jev-offline-fixture \
  --guidance-file examples/airports/guidance.md \
  --cache .jevotron/airport-fixture.sqlite3 --fail-on new-warning \
  -o changes.jsonl --markdown changes.md
# Expected status: 3; two new field warnings and six unchanged entries.
```

The [offline CI workflow](https://github.com/cmungall/jevotron/blob/main/.github/workflows/compare-airports.yml)
executes this fixture without credentials, asserts the expected policy violation,
and publishes its Markdown in the job summary.

For a real dataset gate, populate the normal cache or provide an API key as a CI
secret, omit the fixture model, and allow status 3 to fail the job. This complete
job can be used in a GitHub Actions workflow with `pull_request` or `workflow_dispatch`:

```yaml
name: Airport quality gate
on: [pull_request, workflow_dispatch]
permissions:
  contents: read
jobs:
  compare:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7.0.1
      - uses: astral-sh/setup-uv@v10.2.0
        with:
          python-version: '3.12'
      - run: uv sync --locked
      - name: Compare airport datasets
        env:
          TYPESAFE_API_KEY: ${{ secrets.TYPESAFE_API_KEY }}
        run: |
          set +e
          uv run jt compare examples/airports/baseline.csv examples/airports/spiked.csv \
            --id-column ident --field /iso_country \
            --guidance-file examples/airports/guidance.md \
            --fail-on new-warning -o changes.jsonl --markdown changes.md
          status=$?
          set -e
          if [ "$status" -eq 0 ] || [ "$status" -eq 3 ]; then
            cat changes.md >> "$GITHUB_STEP_SUMMARY"
          fi
          exit "$status"
```

The deliberately spiked candidate demonstrates a failing quality gate. Substitute
the previous and proposed versions of your dataset for ongoing curation. A
fork pull request without access to the API secret needs complete cached results
or the offline fixture; an incomplete scan exits 1 instead of appearing clean.
