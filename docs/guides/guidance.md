# Guidance & examples

Start with a single sentence on the command line:

```sh
jevotron scan data.csv --guidance "Quantity must be nonnegative."
```

For longer instructions, use a plain UTF-8 document:

```sh
jevotron scan data.csv --guidance-file curation-guide.md
```

`--guidance` always means literal text. `--guidance-file` always means a path.
You can combine them to add a task-specific instruction to a reusable guide:

```sh
jevotron scan mondo-edit.obo --guidance-file MONDO-DPS \
  --field /def --field /intersection_of --field /name \
  --guidance "Find text definitions inconsistent with intersection_of. Ignore other problems."
```

When both are supplied, the file text comes first, followed by two newline
characters and the inline text, regardless of option order. Neither text is
trimmed. Either option, or their combination, replaces guidance in a project
config. Identical combined text produces the same cached request as supplying
that text through just one option. This also works with `preview`.

Use concrete rules, units, permitted exceptions, and the meaning of “anomaly.”
For example:

```text
Entries describe physical stock. Quantity is a count and cannot be negative.
Unit prices are in US dollars and must be nonnegative. A zero price is valid
for a free sample. Category should agree with the item description.
```

Every entry receives the same document. V1 assesses each chunk independently;
it does not retrieve neighboring records or assemble additional context.

## Batching and large guidance files

Choose per-entry requests, automatic packing, or an explicit entry cap. The
default is `--batch-size 1`. Shared-guidance packing changes the prompt layout
and can change scores; the [benchmark report](../analysis/batching.md) describes
the observed differences. That comparison did not isolate layout changes from
batching itself.

```sh
# Keep the original per-entry layout (also the default).
jevotron scan data.csv --guidance-file rules.md --batch-size 1

# Choose economical batches automatically.
jevotron scan data.csv --guidance-file rules.md --batch-size auto

# Force shared guidance, capped at 16 entries and 24,000 estimated input tokens.
jevotron scan data.csv --guidance-file rules.md --batch-size 16 --batch-tokens 24000

# Inspect proposed requests and estimates without credentials or API calls.
jevotron preview data.csv --guidance-file rules.md --batch-size auto --limit 100
```

| Option | Behavior |
| --- | --- |
| `--batch-size auto` | Choose an economical layout for each entry; pack up to 64 entries per request within token budgets. |
| `--batch-size 1` | Use the original per-entry layout. Many field questions can still require several requests. |
| `--batch-size N` | Use shared guidance, with at most N entries per request. Token budgets may force smaller batches. |
| `--batch-tokens N` | Target estimated total input size; default 48,000, maximum 64,000. State plus the largest question must fit within half this budget. |

Each selected field remains a separate question. In the shared layout, guidance
and exemplars appear once in `state`; each question includes its own complete
entry and selected field. Other entries are not included in that question's
context. Entries with many selected fields repeat their entry in each question,
which can outweigh the guidance savings. Automatic mode compares estimated costs
for two similarly sized entries and uses shared guidance only when savings are
at least 256 tokens and 10%. The layout choice is independent of neighboring
entries and cache hits; an explicit N greater than 1 forces the shared layout.

Requests run sequentially, and results retain input order. The planner buffers
at most the entry cap plus one lookahead entry and flushes at the token budget or
end of input. Cache hits are removed before packing network requests. `--limit`
limits entries assessed, not requests; `--where` filters completed results and
does not reduce inference work.

### Token limits and estimation

Jev 1.13 documents **64k tokens for state plus all questions**, and **32k for
state plus the largest individual question**. Both limits apply. The defaults
leave 25% headroom. See [TypeSafe's model limits](https://docs.typesafe.ai/models)
and [parallel questions example](https://docs.typesafe.ai/cookbooks/parallel_questions).

No tokenizer or token-count endpoint is documented in the public API. Jevotron
estimates one token per two UTF-8 bytes of canonical JSON, plus overhead. These
estimates intentionally err high for ordinary English/JSON, but are not exact
counts or guaranteed upper bounds. The scan summary shows returned usage and
estimated input tokens for successful requests, allowing comparison on your data.

A confirmed server token/context rejection splits a request's questions in half
and retries, with a bounded recursion depth. Ordinary validation errors stop the
scan; transient failures retain the normal bounded retry behavior. If one
question and its context cannot fit, the scan fails with guidance to reduce the
input. It never silently trims guidance or entry content. Raising `--batch-tokens`
also raises the individual-context budget, up to 32,000 estimated tokens.

### Inspect batching and compare scores

Preview emits one JSON object per planned API request, assuming every entry is a
cache miss. Its `request` is the exact payload. `entries` maps assessments back
to input records, and `question_entries` maps individual questions to records and
fields. `estimated_tokens`, `estimated_context_tokens`,
`estimated_unbatched_tokens`, and `estimated_tokens_saved` describe packing.
Actual scan batches can differ because of cache hits, duplicate content, or
server-requested splitting.

```sh
jevotron preview data.csv --guidance-file rules.md --batch-size auto --limit 100 \
  | jq '{layout, entries: (.entries | length), estimated_tokens, estimated_tokens_saved}'
```

Moving entry content from state into question instructions changes the prompt.
Batching can therefore change probabilities and labels compared with per-entry
requests, even though questions within a request are independent. The layouts
have separate [cache identities](cache.md). Benchmark on representative records
before relying on shared-layout scores or thresholds. The repository includes
`scripts/benchmark_batching.py`; it reports packing estimates offline, or cost,
latency, and score differences with `--live`. Its long-guidance cases repeat
rules synthetically and are not a MONDO validation set.

## Supply a few exemplars

Create a JSON list of selected examples. You may include expected field labels:

```json
[
  {
    "entry": {"item": "Free sample", "quantity": 2, "unit_price_usd": 0},
    "assessment": {"/quantity": "NORMAL", "/unit_price_usd": "NORMAL"}
  }
]
```

Save it as `exemplars.json`, then run:

```sh
jevotron scan data.csv --guidance-file curation-guide.md \
  --exemplars exemplars.json
```

These are in-context examples, not extra entries to scan. The CLI still scans
all input entries unless you set `--limit`. It never selects exemplars on your
behalf. Changing the document or exemplars changes the request and requires
fresh assessments.

## Inspect the exact input

With [jq](https://jqlang.org/) installed:

```sh
jevotron preview data.csv --guidance-file curation-guide.md \
  --exemplars exemplars.json --limit 1 | jq '.request'
```

Use [project configuration](config.md) to keep these settings together when
you reuse them across many runs.
