# Cache & repeat runs

SQLite caching is automatic. Successful assessments are committed immediately
to `.jevotron/cache.sqlite3`.

```sh
jevotron scan data-v1.csv --guidance-file rules.md -o v1.jsonl
jevotron scan data-v2.csv --guidance-file rules.md -o v2.jsonl
```

Only changed assessments need new API calls. Entries can move within a file or
appear under a different filename and still reuse their saved assessments.
The stderr summary reports how many entries were cached.

## Share a cache between directories

```sh
jevotron scan data.csv --cache "$HOME/.cache/jevotron/project.sqlite3"
```

Use the same cache path for later versions. The default path is relative to
your working directory.

Concurrent scans can share a local cache. If their initial cache setup contends
for a SQLite lock, jevotron retries for up to 30 seconds before reporting an error.

## Control reassessment

```sh
# Replace saved assessments even when the request is unchanged.
jevotron scan data.csv --refresh

# Make a run without reading or writing the cache.
jevotron scan data.csv --no-cache
```

| Change | Fresh assessment? |
| --- | --- |
| Entry content, including unselected context fields | Yes |
| Selected field set | Yes |
| Guidance, exemplars, criteria, or model | Yes |
| File name, position, reporting ID | No, unless included in entry content |
| Threshold, sorting, output format | No |
| Object-key or field-selection order | No |
| Parser code | Only if the model input changes |
| Batch membership, record order, or batch size within the shared layout | No |
| Switching between per-entry and shared layouts | Yes: the prompt changes |

The default model is pinned to `jev-1.13.0`. If you choose a moving alias such
as `jev-latest`, the saved result stays in use until you refresh or change the
requested model.

## Resume after an interruption

Rerun the same command. Completed entries replay from the cache; unsuccessful
or malformed API responses are not stored as successful assessments. The CLI
stops on a permanent API failure and retries transient failures with bounded
backoff. A request gets at most three attempts. `Retry-After` can specify a delay
in seconds or an HTTP date; retry waits use that value with a 30-second cap and
the ordinary backoff as a minimum.

A run consisting entirely of cache hits does not need `TYPESAFE_API_KEY`. Any
cache miss does. The cache stores full requests and responses for inspection,
but never the API key. Changed inputs create new records; `--refresh` replaces
the matching record. This is a cache, not an assessment-history database.

## Batched request provenance

Shared-layout assessments have a versioned, per-entry identity that includes the
model, complete entry, selected fields, guidance, exemplars, and criteria. The
result's `request_hash` identifies that canonical assessment, independent of the
actual batch. Per-entry requests retain their historical cache keys. Automatic
layout selection may differ from an explicitly forced layout; only identical
layouts and inputs share cached assessments.

The `assessments` table stores canonical per-entry requests and complete answers.
The `batch_requests` table stores actual shared or split API requests and responses
under their wire request hashes, including the original usage. Shared or split assessments
reference those records through `usage.batch_request_hashes`, so a shared token
bill is not repeated as per-entry token totals. Preview uses `request_hash` for
the actual planned request and `entries[].assessment_hash` for each cache key.

An entry split across several API requests is cached only after all its fields
validate successfully. Complete entries from earlier successful requests remain
cached if a later request fails. Partial entries are reassessed on the next run.
The stderr usage summary counts new successful responses once per request and
excludes cache hits; reported API calls count logical submissions, including
context-splitting attempts, but exclude HTTP retries internal to the client.
