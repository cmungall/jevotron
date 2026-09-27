# Optional research feeds

`jevotron-feeds` is an experimental extension shipped with the package. It has a
separate command and module (`jevotron.extensions.feeds`), uses existing
dependencies, and is not imported by the normal `jevotron` CLI. There is no
third-party plugin discovery, extra install, hosted service, or background job.
Source adapters are selected explicitly and loaded lazily. Classification presets
are independent of the source: the same research rubric can assess a post or a
normalized paper record.

The `research-feed` preset asks four independent questions: primary topic,
relevance to your interests, content kind, and actionability. It retains
probabilities, confidence, probability margin and review flags. The questions
do not treat irrelevant content as an anomaly. Existing `scan` requests, scores,
warnings, output formats and cache identities are unchanged.

## Start offline

From the checkout:

```sh
uv sync --locked
uv run jevotron-feeds presets
uv run jevotron-feeds adapters
uv run jevotron-feeds preview examples/classified_feeds/research-posts.jsonl --limit 1
uv run jevotron-feeds digest examples/classified_feeds/research-posts.jsonl \
  --output .jevotron/feeds/demo
```

The checked-in JSONL contains three synthetic posts, including reply/quote
context and an instruction-injection example. The digest escapes source text,
loads no remote resources, and executes no scripts. All three items remain
explicitly unassessed until classification results are supplied.

A second fixture contains invented paper/event records with non-Bluesky IDs:

```sh
uv run jevotron-feeds preview examples/classified_feeds/research-items.jsonl
uv run jevotron-feeds digest examples/classified_feeds/research-items.jsonl \
  --as-of 2026-09-24T00:00:00Z --output .jevotron/feeds/local-demo
```

This uses the `local` adapter without importing the Bluesky adapter or retrieving
anything. It demonstrates the shared pipeline, not Scholar integration or measured
classification quality for papers. See [adapter boundaries](../advanced/feed-adapters.md)
for the envelope, dependency policy and scoped plan for further sources.

An installed wheel exposes the same `jevotron-feeds` command from any directory.
It also supports `python -m jevotron.extensions.feeds`. Example fixtures are in
the source checkout/downloadable examples, not required by the installed command.

## Collect selected public posts

```sh
jevotron-feeds collect --actor bsky.app --limit 5 --pages 1
jevotron-feeds collect --query bioinformatics --limit 5
```

`collect` and `recheck` default to `--adapter bluesky`, currently the only network
adapter. `preview`, `classify` and `digest` dispatch validation from each record's
source marker and can accept mixed-source files with unique IDs.

The command prints the source-specific output folder under
`.jevotron/feeds/public/<source-hash>/`. Set `--root` to use a different local
storage root. Prefer an account DID for durable source selection across handle
changes. The public search service may return HTTP 403; the adapter stops and
does not attempt authentication or switch hosts.

Collection is limited to 1–25 items per page, 1–3 pages per invocation, one
nonrecursive context hydration of at most 25 URIs, and 2 MB per HTTP response.
At most 200 records are retained per source. Fresh polling merges by post URI;
`--resume` continues an older saved page cursor. This is bounded sampling, not
complete coverage or a reliable incremental new-post watermark.

Available parent/root/quote text carries URI/CID context identity. Missing,
blocked, version-mismatched and unsupported context remain explicit. Reposts
merge into the original post URI. External links, full papers and media are not
fetched. All source content is untrusted data.

Collection writes normalized `records.jsonl`, exact `preview.jsonl`, an
unassessed `queue.jsonl`, escaped `digest.html`, and `manifest.json`. The source
checkpoint is in `state.json`. Failed fetches leave the checkpoint unchanged.
Individual files are atomically replaced, with the checkpoint saved last;
this is not a multi-file transaction. On a disk-write failure, check the manifest
hashes and regenerate from the checkpoint. `.lock` rejects concurrent writers
to one source folder; inspect/remove a stale lock after a killed process.

## Preview and classify

In the following commands, `records.jsonl` means the normalized file in the
reported source folder. Use the same interests throughout preview, classification
and digest generation:

```sh
jevotron-feeds preview records.jsonl --preset research-feed \
  --interests "Bioinformatics, ontologies, and open-source research software" \
  --limit 10 --output requests.jsonl

# Paid inference is explicitly opt-in. Requires TYPESAFE_API_KEY on cache misses.
jevotron-feeds classify records.jsonl --preset research-feed \
  --interests "Bioinformatics, ontologies, and open-source research software" \
  --limit 10 --execute --output assessments.jsonl

# Omit --execute to allow only cached classifications, without credentials.
jevotron-feeds classify records.jsonl \
  --interests "Bioinformatics, ontologies, and open-source research software" \
  --limit 10 --output assessments.jsonl
```

The default preview limit is 3; classification defaults to 10. Both accept
1–200 active records. Local inputs must fit within 200 JSONL rows and 16 MB;
duplicate IDs, unsupported sources and malformed records are rejected.
These limits do not enforce a monetary budget. The classifier uses the standard
Jev client, including its bounded retries; the separately approved historical
pilot's dollar ledger remains an example rather than a general billing system.

By default, assessments use `.jevotron/feeds/classification.sqlite3`; choose
another path with `--cache`. Successful responses are validated before caching.
Partial successes remain cached after a failed run, while the output file is
replaced only when the selected set completes. A nonzero exit means the new
classification output is incomplete/not published; a previous output file may
still be present. `--refresh --execute` replaces cached answers deliberately.

The preset pins `jev-1.13.0`. Hashes include the full normalized content and
context, schema, interests, rubric and model. Changing observation time or a
display handle alone does not invalidate inference. Rank and review policy
changes are local. The cache contains full request/response data; never treat
field selection or the absence of cached credentials as content redaction.

## Build the review queue

```sh
jevotron-feeds digest records.jsonl --assessments assessments.jsonl \
  --interests "Bioinformatics, ontologies, and open-source research software" \
  --output .jevotron/feeds/review
```

The digest validates each result against the exact current request hash and
question vocabulary. Changed content/context, different interests, a different
model, duplicate result IDs or unavailable records cause an error before output
replacement. Serialized uncertainty flags are recomputed from validated answers.
You can provide classifications for a subset; all remaining active records stay
in the queue as unassessed. No default relevance threshold removes posts.

The directory contains `queue.jsonl`, `digest.html` and a manifest with classified
and unassessed counts. `--as-of 2026-09-24T00:00:00Z` fixes the recency clock for
reproducible comparisons. Without assessments, ranking uses recency and diversity.
With assessments, it also uses relevance and actionability probabilities. The
author/text penalties and review thresholds remain experimental.

The ten-record pilot agreed with 38 of 40 provisional agent labels, but its
high-relevance cutoff missed one of two labeled positives. That miss was not
caught by uncertainty routing. This motivates retaining every active record and
human review. The small, source/time-confounded sample does not establish
general accuracy, calibration or a reliable exclusion threshold.

## Recheck availability and scope

```sh
jevotron-feeds recheck --actor bsky.app
```

This rotates through at most 25 retained post/context URIs. An explicit public
lookup omission suppresses unavailable text from the source snapshot; a page
omission alone never establishes deletion. Stale assessment hashes prevent
silently reusing an older classification in a newly generated digest.

Separately saved assessments, caches, digests, exports and backups are **not**
automatically purged. Production deletion handling needs a reverse reference
index, expiry, transactional storage, and an explicit cache/export retention
policy. Separate source folders also need global deduplication for a combined
digest. No native Bluesky feed is registered or published.

Installed commands accept public/synthetic Bluesky records and normalized local
records marked `public-import` or `synthetic-feed`. Local imports require no extra
dependency; they do not fetch URLs or convert raw BibTeX/provider responses.
Synthetic email
triage and the one-off evaluation runner remain in `examples/classified_feeds/`.
There is no Gmail adapter or action execution. A source marker is an input-shape
check, not proof that an arbitrary user-supplied file is public; inspect the exact
preview before authorizing inference on your own files.
