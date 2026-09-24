# Cache & repeat runs

SQLite caching is automatic. Successful assessments are committed immediately
to `.jevotron/cache.sqlite3`.

```sh
jevotron scan data-v1.csv --guidance-file rules.md -o v1.jsonl
jevotron scan data-v2.csv --guidance-file rules.md -o v2.jsonl
```

Only changed requests need new API calls. Entries can move within a file or
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

The default model is pinned to `jev-1.13.0`. If you choose a moving alias such
as `jev-latest`, the saved result stays in use until you refresh or change the
requested model.

## Resume after an interruption

Rerun the same command. Completed entries replay from the cache; unsuccessful
or malformed API responses are not stored as successful assessments. The CLI
stops on a permanent API failure and retries transient failures with bounded
backoff.

A run consisting entirely of cache hits does not need `TYPESAFE_API_KEY`. Any
cache miss does. The cache stores full requests and responses for inspection,
but never the API key. Changed inputs create new records; `--refresh` replaces
the matching record. This is a cache, not an assessment-history database.
