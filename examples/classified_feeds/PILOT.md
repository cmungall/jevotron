# Public-data pilot: completed

On 2026-09-24, the user approved proceeding with the recommended public-data
pilot. The first request returned HTTP 402 and stopped without automatic retry.
After the user confirmed account resolution, an explicit recovery completed
**all ten classifications** with `jev-1.13.0`. The original failed attempt remains
in the ledger. Eleven total HTTP attempts reserve at most **$0.030277632** at
verified published pricing, below the unchanged **$0.05** ceiling.

The ten successful responses report **14,329 input tokens**, implying
**$0.000601818** at the published price. This estimates successful inference usage,
not an invoice; the original rejected attempt returned no billing/usage record.
Its full conservative reservation remains counted.

The result supports further ranking experiments, not automatic exclusion:
the fixed high-relevance filter missed one of two provisionally labeled positives.

## Frozen inputs and evaluation

Local files are under `.jevotron/classified-feeds/pilot-2026-09-24/`:

- `plan.json`: ten complete normalized public records, reader interests,
  pre-inference annotations/rationales, evaluation settings and limitations.
- `ledger.json`: the original failed attempt, explicit recovery authorization,
  ten validated responses, request hashes, reported usage and measured latency.
- `status.json`: completed state and attempt/classification counts.
- `report.json`: agreements, confusions, disagreements, ranking metrics and costs.
- `queue.jsonl` and `digest.html`: all ten classified records in ranked order.

Canonical plan SHA-256:
`32e5160e52c0f11d495642606c4619e6f5a58653da5b6114286c60240ac43d84`.
No labels, selection, rubric, ranking weights or thresholds were changed after
inference. A local replay of all ten saved responses with a client that fails on
any network access confirmed reuse without further provider calls.

The original ten-post sample was mostly an apple-themed Q&A and entertainment.
Five were selected as negative/borderline cases: cultivar genetic identification,
apple pigments, television commentary, a social-platform poll, and sports-themed
brand banter. Five consecutive research posts were collected in one bounded
public GET from `biorxivpreprint.bsky.social`, the account linked by
[bioRxiv's announcement](https://connect.biorxiv.org/news/2023/09/27/biorxiv_expands_on_social_platforms).
An initial misspelled plural handle returned HTTP 400 and produced no records.

The five research posts concern spatial metabolomics, physiological network
analysis, experimental therapy, single-molecule measurement, and protein
simulation. Only public post text and already available context were retained;
no papers or external links were fetched. The new source snapshot is separately
stored at `.jevotron/classified-feeds/public/d4a3516c5d6b1a34/`.

Annotations are **Codex agent judgments made before TypeSafe predictions**, not
independent human gold labels. The relevance labels contain two high, four medium
and four low cases. Primary purpose is sometimes ambiguous for quoted questions;
the rationale records that ambiguity. This is a purposive ten-record pilot,
not a representative holdout. Source and recency are confounded: the research
posts are newer. No confidence calibration or generalization claim is justified.

The runner calculated exact label agreement/confusion for the four
dimensions, disagreement IDs, uncertainty review count, and high-relevance
precision/recall at a frozen `P(high) >= 0.5` cutoff. NDCG@5 uses relevance grades
high=2, medium=1, low/unclear=0 and exponential gains. It compares the classified
ranking with both plain recency and the original recency/diversity policy, using
the same frozen timestamp. All ten cases, including low-ranked ones, enter the
comparison.

## Measured results and interpretation

| Dimension | Agreement with pre-inference annotations |
| --- | --- |
| Topic | 10/10 |
| Relevance | 9/10 |
| Content kind | 9/10 |
| Actionability | 10/10 |

These are agreement counts against one agent's provisional judgments, not
validated human-label accuracy. The actionability sample contains only `read`
and `none`, so it says nothing about recognizing genuine `act` cases. All topics
are science or other; software and unclear have no labeled coverage.

At `P(high relevance) >= 0.5`, there was one true positive, zero false positives
and one false negative: precision **1/1**, recall **1/2**. The denominator is too
small to support a reliability claim. Seven of ten records were flagged for
review by the pre-existing confidence/margin rule.

| Ranking | NDCG@5 against the provisional relevance grades |
| --- | --- |
| Plain recency | 0.9214 |
| Recency plus author/text diversity | 0.6652 |
| Classification plus recency/diversity | 0.9578 |

The classified ranking moved the computational protein-simulation post to first
place. The benefit over plain recency is small in this sample; the recent research
source already dominates the chronological baseline. The unconditional author
penalty in the original baseline elevates off-topic posts over several useful
research posts. A later, separately evaluated experiment should apply diversity
within relevance bands or use weaker penalties. These weights were not tuned
on the pilot after seeing its outcomes.

Two disagreements merit review:

- Spatial metabolomics post `3mwcecookiy26`: annotated high relevance, predicted
  medium (`P(high)=0.15`, `P(medium)=0.72`, confidence 0.63). This is a plausible
  boundary disagreement between domain biology and direct bioinformatics utility.
  It was **not flagged by the uncertainty rule**, despite being the filter's
  false negative relative to the provisional labels. It remains at rank three
  in the full queue. Uncertainty routing alone does not ensure recall.
- Voting reminder `3mw2cdr44fc2a`: annotated request, predicted announcement
  (`P(announcement)=0.56`, `P(request)=0.34`, confidence 0.45). Both communicative
  readings are plausible. It was flagged for review, with low relevance and no
  reader actionability. A better rubric should distinguish a direct ask from an
  announcement containing an ask; independent annotators should adjudicate it.

Observed request-plus-validation time for the ten successful sequential calls
was 1.022 seconds total, median 0.095 seconds, range 0.070–0.160 seconds. This is
one small local measurement, not a throughput benchmark or latency guarantee.

## Billing bound and failure behavior

The pinned model remains `jev-1.13.0`. The [TypeSafe model page](https://docs.typesafe.ai/models),
checked on 2026-09-24, lists $0.042 per million input tokens, free output tokens,
and a 64k total input context window. The pilot reserves 65,536 input tokens per
HTTP attempt, rounding the window upward. At that documented price, each attempt
reserves $0.002752512; eleven, including the rejected one, reserve $0.030277632,
below the selected $0.05 ceiling.
This is a conservative bound based on published pricing, not a provider-side
spend limit or an invoice. Reverify pricing before a later execution date.

Reservations are written before each POST. Failures and unknown outcomes retain
their reservation. The runner uses one fixed endpoint, the existing environment
credential, no redirects, and no automatic retries. No credential files are read
or credentials printed/stored. No private or synthetic mail enters this runner.

Successful responses must match the pinned model, exact questions and labels,
valid probabilities, and expected usage before being persisted as complete.
Saved results are reusable only against the exact plan/request hashes. A changed
plan cannot reset the existing budget. A prior failed/unknown attempt blocks
automatic resubmission; do not delete or edit the ledger to reset limits. The
explicit recovery operation accepts one recorded HTTP 402 only, logs its
authorization, and permits one extra total attempt without increasing the budget.
The original response code was backfilled from the recorded CLI/status evidence;
the original attempt, timestamp and reservation were preserved.

The preflight command makes no provider requests:

```sh
uv run python -m examples.classified_feeds.pilot \
  .jevotron/classified-feeds/pilot-2026-09-24
```

The initial command used `--execute`. After the user's confirmation, it was
resumed with `--execute --recover-request` and the exact failed request hash
`73b07ecabfd1bc18ab066f71b4e3fecf7d58865368dfda96d58f161f5793879e`.
The ten completed responses now replay without additional network requests;
the CLI still expects the environment credential when entered with `--execute`.
The preflight command above and direct saved-file inspection need no credential.

## Validation and next step

The offline tests exercise durable pre-send reservations, result reuse without
another call, changed-plan rejection, failed-attempt replay prevention,
public-only and record-count guards, budget exhaustion, reference-label coverage,
metric calculation, and truthful rendering of classified versus preview output.
The full suite passes **153 tests**, including **28** feed/pilot tests. The added
recovery tests verify retention of the failed attempt, no duplicate replay,
unchanged budget enforcement and refusal to retry unknown outcomes. Ruff lint
and formatting also pass. The original strict docs and package builds passed
during the preview phase; core/package configuration remains unchanged.

Next, obtain independent human labels and a broader, source/time-balanced sample
with software topics, genuine action requests, missing context and ambiguous
cases. Define the high/medium relevance boundary before collecting another
holdout. Compare ranking policies and inspect low-ranked/excluded positives;
do not deploy a hard high-relevance filter based on this pilot. Any broader paid
run is outside the completed ten-record scope. Email remains synthetic-only;
needs-reply quality remains untested. No Gmail actions, feed registration,
public artifact, scheduled task, commit, push or PR was performed.
