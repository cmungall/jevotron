# Classification feeds and email shadow triage

This note records the initial preview investigation. See [the subsequent pilot
report](PILOT.md) for the completed ten-record inference pilot after recovery from
the initial provider HTTP 402.

Investigation and validation: 2026-09-23 America/Los_Angeles
(live runs stamped 2026-09-24 UTC). Outcome: a verified, bounded preview outside
core, with no paid inference, real mail access, publication, or account changes.
Inference quality remains **untested**.

## Repository evidence and scope

The checkout, local main, origin/main, and the remote main advertised by
`git ls-remote origin refs/heads/main` all resolve to
`ab90730a71b42d648f0291c2dfa8668c4e1ca965`. No applicable AGENTS.md or CLAUDE.md was
found in this checkout or its ancestor directories. The canonical gogcli skill at
`/Users/cjm/repos/stuff/.claude/skills/gogcli/SKILL.md`, the Orca CLI guide, and the
existing `jt-triage` repository skill were read before implementation.

The clean sibling checkouts were inspected read-only:

| Work | Inspected head | Existing scope; avoided here |
| --- | --- | --- |
| `jt-use-cases` | `14d6e70` | `jt` CLI alias, installable triage skill, marketplace, shortlist helper, agent docs |
| `jt-databases` | `85be724` | SQLite/DuckDB readers, parser/CLI integration, database examples/tests |

Main currently exposes `jevotron`, not the use-case branch's `jt` alias. No core,
project metadata, existing docs, or sibling files were edited for this experiment.
The example package is run from the checkout and is not a new installed command.

Verified directly in the main source:

- `Config` supports 2–255 custom labels, exact per-label criteria,
  `anomaly_label`, guidance, exemplars, model and threshold.
- `Chunk` exposes `id`, JSON data, selected JSON Pointer fields, and source.
  Each chunk is independent; the runner does not retrieve thread context.
- `make_request` asks about selected fields' **correctness** and applies one
  shared choice vocabulary to every selected field. Selection does not redact
  the rest of the entry sent in state.
- `scan` reports each field's selected label/distribution/confidence, scores it
  by `P(anomaly_label)`, and aggregates the highest field score into a warning.
  JSONL output exists, but the reporting contract is anomaly-centered.
- Request hashing includes endpoint and canonical payload. The SQLite cache stores
  full request/response JSON, validates responses, and commits successful calls.
  Threshold and output ordering are downstream; content/rubric/model changes
  invalidate requests. No classification cache is populated by this demo.

The existing `jevotron preview` was also run successfully against one actual
normalized public record, using `--id-column id --field /text --limit 1`. This
proves parser/preview interoperability, not that its correctness framing is a
good feed classifier. Reusing its anomaly score for every feed dimension would
confound unrelated decisions. Therefore this prototype reuses canonical JSON,
request hashing and choice-response validation but leaves the runner untouched.

## Pipeline and classification contract

```text
fixed public Bluesky GET adapter ─┐
                                ├─ normalized versioned records + context status
synthetic ordered mail adapter ─┘
    → proposed typed questions → request hash → [future authorized evaluator]
    → validated independent dimensions → local ranking / human review
                                        → separate action-permission boundary
```

Bluesky identity is the DID-based AT URI; CID is the post version. Normalization
retains author DID, created time, text, selected media text metadata, reply root
and parent references, and quote references. Available context includes its own
URI/CID and text. Missing, blocked, unsupported, version-mismatched, and available
context remain distinct. A parent/quote CID mismatch never silently substitutes
new text for the version referenced by the post. There is no recursive traversal,
media download, link following, OCR, or attempt to infer absent media contents.

The [current TypeSafe API](https://docs.typesafe.ai/api) supports a shared state
and named typed questions (`choice`, `noul`, `score`). The prototype uses choice
questions, each with its own instructions and criteria, and validates complete
probability distributions before decoding. Question names are not interpreted
by the underlying model, so the meaning is explicit in each question's text.

| Dimension | Prototype vocabulary | Purpose |
| --- | --- | --- |
| Primary topic | science, software, other, unclear | Organize subject matter |
| Relevance | high, medium, low, unclear | Utility to explicit reader interests |
| Content kind | resource, discussion, request, announcement, other | Communicative purpose |
| Actionability | act, read, none, unclear | Candidate next step, never action permission |
| Needs reply (mail only) | yes, no, unclear | Unresolved responsibility across a thread |

These are independent questions, not a cross-product of labels. A scientific
announcement can be relevant yet need no reply. Topic is a single primary topic
in this bounded prototype; a later multi-topic taxonomy should use independent
binary questions and evaluation for each topic. Content kind is deliberately
coarse; ambiguous mixtures should be surfaced by uncertainty rather than forced
into dozens of compound classes.

The model is pinned to `jev-1.13.0`, listed in the [current model documentation](https://docs.typesafe.ai/models).
Aliases can move; cache reuse across such moves requires explicit invalidation.
Provider confidence is retained separately from maximum probability and the
top-two probability margin. A confidence below 0.6, margin below 0.2, or an
`unclear` label flags review. These are illustrative thresholds, **not calibrated
error estimates or acceptance thresholds**. No request was sent to the provider.

The default interests (open science, bioinformatics, ontologies, research
software) are an explicit starting assumption and editable by CLI. Requests
include schema version, complete normalized content, context states and
versions, interests, criteria and pinned model. Observation time and mutable
display handle are excluded from request identity. Rank policy changes must
not trigger inference; rubric, normalization, context, CID, interests and model
changes must. A future assessment store must bind every decoded result to the
exact request hash before ranking; the current CLI never loads predictions.

Every active record enters the review queue. With no assessments, ranking uses
48-hour recency decay, a repeated-author penalty, and an exact normalized-text
penalty. No confidence or semantic label is fabricated. The tested decoder/ranker
can combine relevance probability (weight 0.6), actionability (0.15), and recency
(0.25), then apply diversity penalties. Weights are experimental. Future-dated
timestamps get no bonus beyond age zero; invalid timestamps fail validation.
Cross-posts are deprioritized rather than silently discarded. Exact URI matches
merge before ranking; semantic near-duplicate grouping and conversation quotas
remain future work. A separate review queue should retain uncertain items even
if a future filtered digest excludes them.

## Public collection, checkpoints and deletions

The [author-feed schema](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/getAuthorFeed.json)
allows unauthenticated account feeds including posts/reposts and cursor paging.
The demo selects `posts_with_replies` and excludes pins, merging repeated post
URIs. Repost events are not separate classification units; reposter identity and
event time are not used for ranking. The [search schema](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/searchPosts.json)
allows provider-dependent authentication and explicitly does not guarantee
exhaustive pagination. Search `since` uses a sort timestamp that may differ from
post creation time, so a createdAt watermark would be unsafe.

The current hosted API docs redirect to new Bluesky documentation domains; the
official repository lexicons above were read directly to verify schemas.
The implementation fixes the public host and allowlists three GET methods,
disables redirects/environment-derived proxies, and never loads credentials.
Up to 25 missing context URIs are hydrated through
[getPosts](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/getPosts.json),
whose request maximum is 25. Inline reply and quote views are used when present;
the [post/feed definitions](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/defs.json)
and [quote view schema](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/embed/record.json)
include blocked/not-found alternatives. Further context stays explicitly absent.

State is isolated by account/query. A fresh poll starts at the head and upserts
URIs, so overlaps do not duplicate records. `--resume` follows a saved older-page
cursor, and fails if no cursor exists. Checkpoints advance only after fetches,
normalization and output writes succeed; HTTP failures leave prior state intact.
There are no background polls or retries. At most 200 most recently observed
records are retained per source; this is a storage cap, not a complete historical
archive or time-based deletion guarantee. Separate source directories can still
contain the same post; a future combined digest needs global URI deduplication.

`recheck` rotates through at most 25 retained post/context URIs. A successful
getPosts omission suppresses the unavailable item and purges its text from the
current state, previews, queue and embedded context in that source's outputs.
This is **unavailability, not proof of deletion**: moderation and indexing can
produce the same observation. Missing items on feed/search pages never become
tombstones. A later positive fetch can restore a record. Tombstones have no text.
These transitions are tested with synthetic responses; no real deletion was
performed. Recheck cannot guarantee prompt deletion discovery, cross-source purge,
or purge of exported/copied files. Immutable source versions alone cannot detect
changes in referenced context, hence its independent hash contribution.

For production: use a transactional store with source checkpoints, a reverse
context-reference index, confirmed delete/takedown events where available,
bounded refresh/expiry, response-cache purges, and a retention policy covering
raw snapshots, previews, exports and backups. Atomic per-file writes plus a final
manifest/checkpoint are adequate for this local preview but not a full
multi-file transaction. Disk failure can leave a mixed output generation; verify
manifest hashes and regenerate from the previous checkpoint.

## Local digest versus native Bluesky feed

| Concern | Local prototype | Native custom feed |
| --- | --- | --- |
| Consumption | Local JSONL and escaped static HTML | Bluesky clients request a feed skeleton |
| Service | CLI invocation only | Public HTTPS service, service DID/DID document |
| Response | Full review context and dimensions | Ordered post URIs plus permitted metadata and cursor |
| Registration | None | Account repository `app.bsky.feed.generator` declaration |
| Operations | Bounded manual poll/recheck | Availability, stable pagination, monitoring, deletion/moderation policy |
| Personalization | Local interests | Verify service authentication when using requester-specific state |

The official [feed-generator starter](https://github.com/bluesky-social/feed-generator)
describes HTTPS serving, service identity, registration, and skeleton hydration.
The current [getFeedSkeleton](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/getFeedSkeleton.json),
[describeFeedGenerator](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/describeFeedGenerator.json),
and [generator record](https://raw.githubusercontent.com/bluesky-social/atproto/main/lexicons/app/bsky/feed/generator.json)
define the wire contracts. A whole-firehose consumer is an implementation option,
not required to prove the classification idea. No feed service, DID registration,
repository declaration, or public artifact was created. A reviewed local queue
could later supply ordered URIs without changing the source adapters/classifier.

## Synthetic email and gog boundary

Only `gog --version` and help for `gmail search`, `gmail thread get`, and
`gmail thread modify` were invoked. Installed version: `v0.11.0 (91c4c15)`.
The canonical skill and local help confirm that search returns threads,
`thread get` retrieves thread messages (`--full` for full bodies; `--download`
downloads attachments), and `thread modify --add/--remove` changes all messages
in a thread. No auth listing, credential inspection, real search/get, label
modification or send command was run. Skill examples that permit account actions
do not override this task's stricter synthetic-only scope.

A future read-only adapter would retrieve a bounded thread list and then the
necessary ordered messages, without downloading attachments by default. The
synthetic normalized schema here is not claimed to be actual gog JSON: a real
adapter must verify JSON envelopes, Gmail IDs/history IDs, headers, MIME parts,
body encodings, dates, sent/draft state, and completeness against a separately
approved redacted sample. Search snippets alone are insufficient for triage.

Thread identity must include account namespace and thread ID; version must
reflect message membership, bodies, sender/recipient roles, completeness, and
reader identity. The demo hashes that synthetic thread content. Sort by actual
message time with stable ID tie-breaks; reconsider the whole thread on any new
message, draft/sent transition, deletion or redaction. A final outgoing message
does not necessarily close an earlier unanswered sub-question. Existing labels
are routing hints, not truth about reply obligations.

The only proposals are local `JT/Needs-reply` and `JT/Review` additions. Unassessed,
incomplete, ambiguous and low-confidence cases remain for review; no labels are
removed and no archive/read/delete/send actions are proposed or executed. A
future classifier may suggest `JT/Reference`, `JT/Waiting`, or topic labels only
after their own definitions and evaluation. A separate action component would
require explicit permission, re-read the current thread version, validate an
allowlist, record an audit trail and reject stale proposals. Model output never
grants that permission, even if it sounds urgent or claims authorization.

Needs-reply evaluation must be false-negative-sensitive. Construct a human-labeled
thread-level holdout including direct requests, deadlines, CC ambiguity, partial
answers, multiple obligations, forwards/quoted history, unanswered follow-ups,
auto-replies, incomplete threads, non-English mail and malicious instructions.
Measure recall and false negatives on **all** needs-reply threads, including
those ranked low or predicted no; report precision, review volume, abstention
rate, and uncertainty intervals separately. Split by thread/conversation, not
message, to avoid leakage. Calibrate thresholds on a development set and freeze
them for holdout evaluation. Do not equate high confidence with safe automation.
The six authored fixtures test mechanics only, using mocked expected choices;
they provide no measured recall or inference-quality evidence.

## Privacy and untrusted-source boundary

Public GETs contact only the fixed Bluesky AppView. Collected text stays in ignored
local output directories and is separate from checked-in synthetic fixtures.
No public content was sent to TypeSafe either. All source text—including
instructions embedded in posts, quotes, emails, links or alt text—is data. The
request rubric states this explicitly; the HTML escapes content and uses a
restrictive content-security policy without active links or remote media. Those
controls reduce execution risk; prompt wording does not prove model immunity.

Real-mail inference requires a separate provider/account data review **before
transmission**: applicable DPA and privacy terms, actual retention/logging and
deletion rules, subprocessors/processing locations, organization authorization,
access control, redaction/minimization, and treatment of sensitive attachments,
quoted correspondence and third-party information. TypeSafe's [legal index](https://docs.typesafe.ai/legal)
links its policies and describes enterprise zero-data-retention availability;
that does not establish ZDR for this account. No account terms or DPA were
validated here, and no real-mail inference is approved.

Core caches store full inputs, so field selection or a credential-free cache
does not make it safe to persist private messages. Any future mail store needs
its own access controls, retention/deletion policy and logs without message
bodies. A classifier should run without Gmail write credentials; a separate
action process gets only explicitly approved operations. Provider data review,
inference budget approval and action permission are distinct gates.

## Actual validation and remaining work

Validation used Python 3.12.9 with the locked local environment:

| Check | Result |
| --- | --- |
| Offline synthetic CLI | 3 posts + 6 mail threads; 9 previews; all unassessed |
| Public `bsky.app`, first page | 5 posts, 1 unauthenticated GET |
| Public `bsky.app`, saved-cursor resume | 5 more posts, 1 GET; 10 unique retained URIs |
| Explicit recheck | 1 GET; all 10 still available; request hashes unchanged |
| Public `bioinformatics` search | HTTP 403; no records/checkpoint created; no auth fallback |
| Core `jevotron preview` interoperability | 1 actual public record previewed; no inference |
| New offline tests | 21 passed (normalization, request identity, choice validation, uncertainty, ranking, HTTP limits/errors, repeated cursors, checkpoint failure, bounded rechecks, unavailability, mail shadow proposals) |
| Full repository tests | 146 passed |
| Ruff lint / format | Passed |
| Strict MkDocs build | Passed |
| Source distribution and wheel | Built successfully |
| Saved output audit | Manifest hashes matched; public URIs unique; all 19 public/synthetic queue items explicitly unassessed |

The live public sample is at
`.jevotron/classified-feeds/public/a70ac0009c824414/`; synthetic output is at
`.jevotron/classified-feeds/synthetic/`. These outputs are not fixtures, committed
files or published artifacts. `manifest.json` records the final operation,
provenance and content hashes. There was no live context hydration needed in the
small account sample; reply/quote hydration behavior was tested with mocks.
No live inference, inference cost/latency, semantic relevance quality, real-mail
schema compatibility, needs-reply recall, or native-feed deployment was tested.

Recommended bounded follow-up:

1. Agree on reader interests, selected account DIDs, taxonomy and a labeled public
   holdout. Keep the successful account path; investigate current public search
   availability independently rather than expanding credentials here.
2. Obtain a specific inference budget and public-data approval, then run a small
   pinned-model pilot. Save validated distributions keyed by exact request hash;
   compare a recency-only baseline and assess recall on excluded/low-ranked items.
3. If useful, propose a small neutral `QuestionSpec`/`ClassificationResult` layer
   exposing request construction, validation and cache reuse. Keep anomaly
   `Config`/`scan`/CLI behavior compatible. Do not merge feed adapters into core.
4. Add transactional incremental state, reverse context references, expiry/purge,
   and cross-source deduplication before broadening collection. A stream consumer
   or native feed server is a separate scoped project, not this prototype.
5. For email, complete the provider/privacy review and a separately approved
   redacted gog schema pilot, then evaluate thread-level shadow triage. Retain
   human review and no-write behavior until recall and operational controls are
   independently acceptable. Real Gmail writes still need explicit authorization.

No commit, push, PR, feed registration, published artifact, scheduled job or
user-global skill installation was performed.
