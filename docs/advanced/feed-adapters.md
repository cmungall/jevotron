# Feed adapters and dependency boundaries

One repository hosts the shared classification/review workflow and explicitly
registered source adapters. Provider SDKs belong to their adapters, not the core
classifier. Installing one provider must not require all providers' dependencies.
This is a small built-in adapter table, not a third-party plugin framework.

## Implemented boundary

| Layer | Owns | Does not own |
| --- | --- | --- |
| `jevotron.classification` | Named typed questions, response validation, cache | Sources, ranking, action permissions |
| `extensions.feeds.records` and `storage` | Common envelope, bounded JSONL, snapshot output | Provider identifiers, authentication, polling cursors |
| `extensions.feeds.preset` and `ranking` | Research rubric, uncertainty review, recency/diversity | Source acquisition |
| `extensions.feeds.adapters.bluesky` | AT URI validation, collection/rechecks, Bluesky checkpoints | Other providers |
| `extensions.feeds.adapters.local` | Already-normalized public/synthetic imports | Retrieval, export-format parsing, credential access |

`jevotron-feeds adapters` lists descriptors without importing provider modules.
Only trusted module paths in the built-in table can be loaded; a source field
cannot name a Python module or install a package. Unknown source markers fail.
Selecting a local file never imports the Bluesky adapter. The ordinary
`jevotron` CLI does not import the feed extension.

Existing Bluesky command defaults, checkpoint locations, normalized records and
classification request hashes remain compatible. The old example's
`storage.collect_source()` delegates lazily to Bluesky. No inference request or
rubric changed as part of the adapter split.

## Normalized local records

The local adapter accepts `source: public-import` for explicitly reviewed public
data and `source: synthetic-feed` for invented fixtures. Both use the same shape:

```json
{
  "id": "urn:synthetic:paper:example",
  "source": "synthetic-feed",
  "status": "active",
  "version": "synthetic-v1",
  "author": "urn:synthetic:research-group:example",
  "created_at": "2026-09-24T00:00:00Z",
  "text": "Synthetic paper abstract about ontology evaluation.",
  "context": {},
  "metadata": {"kind": "paper", "provenance": "invented example"}
}
```

- Use stable, globally namespaced IDs, such as canonical source URIs or URNs.
  The loader rejects duplicate IDs, including across sources; callers must merge
  multiple versions first. Author identities should likewise be namespaced to
  avoid accidental diversity penalties across providers. Cross-provider author
  resolution and paper/post entity deduplication are not implemented.
- `version` is a provider revision or deterministic content digest, not retrieval
  time. `created_at` is a timezone-aware source date; do not invent a publication
  time where only a year is known. Partial-date bibliography support needs an
  explicit recency policy before an importer is added.
- `text` may be empty when evidence is absent. Optional `context` is an object
  containing available context and explicit missing/blocked status. Its contents
  and all metadata are untrusted data; no instructions or links are executed.
  Provider adapters own detailed context validation and hydration.
- Optional `observed_at` must have a timezone. As with Bluesky, it and the display
  `handle` are excluded from inference identity; other fields, including context
  and metadata, participate in the request hash. Keep volatile retrieval metadata
  outside records to avoid unnecessary cache invalidation.
- A tombstone contains only `id`, `source`, `status: unavailable` and optional
  `observed_at`. It has no content or metadata. Digests omit it and reject saved
  assessments for it. Local files are caller-managed snapshots; the local adapter
  performs no polling, deletion detection, or automatic cache/export purge.

Both adapters share the 200-row/16 MB input limits. Preview and digest stay
offline; classification is cache-only without `--execute`. Mixed-source files
use the same rubric and bind assessments to source plus exact request hash.
Source markers declare provenance; they cannot prove a file contains public data.
Do not pass private mail through `public-import`. Real-mail inference still needs
provider data-handling review and separate authorization; synthetic mail triage
remains an example, with no Gmail adapter or action permissions.

## Dependencies and the next source

The two implemented adapters need no additional dependencies: Bluesky reuses
HTTPX from the base package; local imports use the standard library. No dependency
extras are currently declared, and `jevotron[x]` is not an available integration.

For an adapter that needs a new SDK or parser:

1. Add only that provider's dependencies under a named optional extra in
   `pyproject.toml`; do not add them to base dependencies or create an all-providers
   dependency requirement.
2. Register a descriptor with an explicit module path and source markers. Keep
   SDK imports inside its lazily loaded module. Catch missing optional dependency
   errors specifically and name the extra to install; do not silently auto-install
   or mask unrelated import failures.
3. Keep credentials, ingestion budgets, rate limits, pagination, context fetches,
   versioning and deletion semantics inside the adapter. Future collectors should
   namespace checkpoint folders by adapter and source selection; preserve existing
   Bluesky paths. Provider-specific CLI options need their own parser rather than
   overloading Bluesky's actor/query flags.
4. Test normalization and lifecycle behavior with synthetic fixtures and mocked
   transport, missing-SDK behavior, and that unrelated commands still run without
   that extra. Evaluate the preset on each new content type before filtering by it.

An adapter can instead become a separate distribution/repository when dependency
conflicts or independent release ownership justify that cost. There is no need
for automatic plugin discovery or a new repository at this stage.

### X/Twitter

No adapter or API access is implemented. A future adapter needs developer/app
credentials and its own ingestion budget, distinct from TypeSafe inference.
X documents [developer access](https://docs.x.com/x-api/getting-started/getting-access)
and [usage-based pricing](https://docs.x.com/x-api/getting-started/pricing).
Start with a bounded read-only collection and synthetic contract tests; user
actions are outside the classifier's permissions. A new SDK is optional even for
this integration if HTTPX covers the selected endpoints.

### Google Scholar and bibliographic imports

No Scholar search/scraping adapter is implemented. Scholar documents citation
exports and explicitly does not provide bulk access in its
[official help](https://scholar.google.com/intl/en/scholar/help.html).
The scoped next step is a file importer for a supported citation format, or an
adapter for the underlying scholarly data source with documented API access.
Such an importer must address partial dates, stable IDs, author lists, absent
abstracts and licensing/provenance. The local normalized JSONL boundary already
supports the resulting records; it is not a BibTeX parser or a Scholar API.

## Validation on 2026-09-25

- Full suite: **184 tests passed**. New coverage exercises mixed-source
  classification with a fake evaluator, cache replay, source/version mismatch
  rejection, local tombstones, and malformed envelopes. A fresh Python process
  blocks Bluesky imports and inference-client construction while running local
  discovery, preview and digest.
- Ruff lint/format and `git diff --check` passed. Strict MkDocs build passed.
- Source distribution and wheel built. The wheel was installed offline into a
  workspace-local virtual environment and exercised from `/tmp`, with socket
  creation disabled. Synthetic local preview/digest passed; ten existing public
  pilot records replayed entirely from cache, producing an identical review queue.
- No new inference calls, source API calls, real-mail access, commits or publishing
  were used for this extension split. Classification quality on paper/event records
  remains untested; the earlier public pilot's limitations still apply.

Local smoke outputs are in `.jevotron/extension-validation/adapter-split/`, separate
from the checked-in synthetic fixtures. They are ignored by version control.
