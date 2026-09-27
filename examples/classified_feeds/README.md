# Classified feeds: bounded local preview

The public feed implementation now ships as the optional `jevotron-feeds`
extension; see the [installed-command guide](../../docs/guides/feeds.md).
This directory retains compatible example commands, synthetic email experiments,
and the historical pilot. The adapter, four-question preset, decoder and ranking
implementation are shared with the installed package.
`research-items.jsonl` adds two explicitly synthetic paper/event records for the
installed local adapter; `research-posts.jsonl` retains the three synthetic
Bluesky posts. Both can use the same preview, classification and digest commands.
See the [adapter design](../../docs/advanced/feed-adapters.md) for dependency and
source boundaries. No Scholar or X retrieval is implemented.
The collection/preview commands make **no inference calls and have no Gmail
client**. Their queue uses recency and author/text diversity; every item is
explicitly unassessed. A separate, explicitly invoked public-data pilot runner
is described in [the pilot report](PILOT.md). It completed ten classifications
after recovery from an initial provider HTTP 402. The classified digest and
evaluation are separate from the preview outputs below.

Run from the repository root, with Python 3.12+ and uv:

```sh
uv sync --locked --group docs

# Fully offline: three synthetic posts and six synthetic email threads.
uv run python -m examples.classified_feeds synthetic

# Public unauthenticated GETs only: select an account or query.
uv run python -m examples.classified_feeds collect --actor bsky.app --limit 5
uv run python -m examples.classified_feeds collect --query bioinformatics --limit 5

# Continue older pages from the saved source-specific cursor.
uv run python -m examples.classified_feeds collect --actor bsky.app --limit 5 --resume

# Explicitly refresh up to 25 saved post/context URIs for this source.
uv run python -m examples.classified_feeds recheck --actor bsky.app

uv run pytest -q tests/test_classified_feeds.py
```

The public search attempt returned HTTP 403 during validation. The command fails
closed and never tries credentials or a different host. Account collection works.
Use `--interests 'your reader interests'` to change the request rubric context.
This changes preview hashes, not source checkpoint identity.

Outputs live under the already ignored `.jevotron/classified-feeds/` directory:

| Directory/file | Meaning |
| --- | --- |
| `synthetic/` | Outputs from checked-in synthetic fixtures only |
| `public/<source-hash>/` | Real collected public data; separate per account/query |
| `records.jsonl` | Normalized records; URI, CID, text, context status/version |
| `preview.jsonl` | Exact proposed requests and hashes; collection commands never send them |
| `queue.jsonl` | All active records in local ranked order; no fabricated predictions |
| `digest.html` | Escaped static local preview; no scripts, images, external requests, or links |
| `mail-proposals.jsonl` | Synthetic shadow proposals only; unassessed threads go to `JT/Review` |
| `state.json` | Public source checkpoint and bounded current state |
| `manifest.json` | Provenance, counts, request count, cursor, inference status, content hashes |

Limits: 1–25 items/page, 1–3 pages/run, one optional context fetch of at most 25
URIs, 2 MB per response, 20-second HTTP timeout, 200 retained records/source.
There are no automatic retries, background jobs, or recursive context walks.
Fresh collection starts from the head and merges by URI. `--resume` is historical
pagination, **not a reliable new-post watermark**. Opaque search cursors may not
cover all results. Handles are convenient selectors; use a DID for durable source
selection if an account changes handles.

Current outputs are overwritten on each successful run. Do not run the same source
concurrently: `.lock` rejects a second writer. After a killed process, inspect and
remove a stale `.lock` manually before retrying. Individual writes are atomic;
the output directory is not a multi-file transaction. The checkpoint is saved
last, and manifest hashes allow detection of mixed JSONL generations after a disk
failure. Hashes are SHA-256 of canonical JSON arrays, not raw JSONL bytes. Re-run
from the unchanged checkpoint after a failed write. Keep backups out of public
artifacts and include them in any later deletion/purge policy.

Fixtures use `.invalid` addresses/handles and explicit synthetic markers. Expected
email labels are human-authored test expectations, not inference results. The
mail adapter accepts only explicitly marked synthetic threads; that marker is an
accident guard, not a security boundary for arbitrary input. The CLI reads only
the bundled mail fixture and never shells out to gog.

The initial [design and validation note](DESIGN.md) describes integration
boundaries, native feed requirements, email privacy, limitations, and next steps.
The [pilot report](PILOT.md) records the subsequent authorization, frozen sample,
cost bound, completed results, and limitations.
