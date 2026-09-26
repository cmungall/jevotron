# Public trace pilot: measured results

Ran `jt` / `jev-1.13.0` on **24 public traces and 163 assistant steps** from
[AgentProcessBench](https://huggingface.co/datasets/LulaCola/AgentProcessBench).
The original JSONL messages and tool definitions were used directly. There is
no analyzer dependency, event normalization, or trace conversion.

The initial experiment supports using jt to shortlist problematic steps. It
also exposes misses: accepting plausible final responses can conceal earlier
failures, and neutral versus useful behavior is difficult to distinguish.

Run date: 2026-09-25 America/Los_Angeles (2026-09-26 UTC).

## Results

All 24 selected traces were successfully assessed. One request per trace scored all
assistant-message fields and the final answer using the full trace as context.

| Task | Correct / assessed | Accuracy | Majority-label baseline |
| --- | --- | --- | --- |
| Step quality: positive / neutral / negative | 130 / 163 | 79.8% | 71.8% |
| Final-result quality | 19 / 24 | 79.2% | 50.0% |

For identifying **negative steps**, precision was **26/29 = 89.7%** and recall
was **26/37 = 70.3%**. Here a prediction is the returned label, not a threshold
on the negative probability. Of nine gold-neutral steps, only three were
classified as neutral. Fifteen gold-positive steps were classified as neutral.

Final-result predictions matched all 12 gold-positive outcomes. Of 12
gold-negative outcomes, seven were negative, four positive, and one neutral.
The neutral prediction counts as a mismatch in the reported accuracy.

| Domain | Step accuracy | Final-result accuracy |
| --- | --- | --- |
| HotpotQA: multi-hop QA | 14 / 21 (66.7%) | 4 / 6 |
| GAIA: research and tool use | 17 / 25 (68.0%) | 5 / 6 |
| BFCL: function calling | 42 / 50 (84.0%) | 4 / 6 |
| tau2: customer service | 57 / 67 (85.1%) | 6 / 6 |

In GAIA, step accuracy was below its 76% majority-positive baseline. The stronger
aggregate result must not be read as uniform performance across domains.

## Inspection of successes and disagreements

These are manual interpretations of the saved traces and predictions; jt returns
labels and probabilities, not written explanations.

- **tau2 trace 940:** identified repeated calls to unavailable diagnostic tools
  as negative, matching the published step labels. This is a useful tool-misuse
  triage case with explicit evidence in the tool responses.
- **tau2 trace 766:** flagged a baggage-allowance answer based on an unverified
  membership tier, matching the negative labels for the answer and its precursor.
- **BFCL trace 740:** predicted a positive final outcome after a successful
  support-ticket creation, despite earlier booking and insurance requests not
  being fulfilled. It also missed several negative steps. This suggests a
  weakness in tracking fulfillment across a multi-turn task.
- **GAIA trace 377:** accepted an answer to a historical roster question despite
  retrieved material referencing later rosters, and despite the requested terse
  output format. Both its final-step and outcome predictions disagreed with gold.
- **Greeting steps:** five customer-service greetings labeled positive by the
  dataset were marked neutral by jt. Some disagreements concern annotation
  conventions rather than dangerous behavior.
- **HotpotQA trace 108:** the predicted answer was accepted by jt but differed
  from the benchmark reference. This is recorded as a benchmark disagreement;
  no external factual adjudication was performed.

## Experimental scope

- Source revision: `cd81f326aece8b0a3f6359e20df370690d3a98bf`, MIT per dataset card.
- Six traces per domain: three positive and three negative final outcomes, each
  from a distinct question within that domain. Neutral final outcomes excluded.
- Eligible traces have at most 20 labeled assistant messages and at most 80,000
  characters under Python's default JSON serialization. Selection uses a fixed
  hash ordering; exact IDs, source URLs, file hashes, and eligible counts are in
  [sample-manifest.json](sample-manifest.json).
- Every assistant message in each selected trace was scored; none were truncated.
- **Human step labels, final labels, ground-truth answers, and tool metrics were
  withheld.** The request preview was checked for those fields. Task descriptions,
  original messages (including system state), tools, and final answer remain.
- The first four traces formed a pilot. The same guidance was then used unchanged
  for all 24; four pilot assessments replayed from cache. No exemplars or tuning.
- The two tasks share the same full-context request. This tests retrospective
  classification, not online early detection or isolated-step classification.
- This is a small, length-filtered, outcome-balanced sample, not the official full
  benchmark. Accuracy means agreement with published labels. Steps within a trace
  are correlated; 163 steps are not 163 independent trials. Training contamination
  was not assessed.

The run used **301,162 input tokens and 8,513 output tokens** across the 24
original calls. The complete report includes four cached calls from the pilot;
their usage is counted once here, not as additional full-run spending. Dollar
cost and per-call latency were not measured.

## Reproduce

From the jevotron checkout, with `TYPESAFE_API_KEY` set for live scans:

```sh
uv run python analysis/agent-traces/prepare.py

uv run jt preview .jevotron/public-traces/sample.jsonl \
  --config analysis/agent-traces/config.py --limit 4 \
  -o .jevotron/public-traces/preview.jsonl

uv run jt scan .jevotron/public-traces/sample.jsonl \
  --config analysis/agent-traces/config.py \
  --cache .jevotron/public-traces/cache.sqlite3 \
  -o .jevotron/public-traces/full.jsonl 2> .jevotron/public-traces/full.log

# Run only after the scan exits successfully.
uv run python analysis/agent-traces/evaluate.py \
  .jevotron/public-traces/full.jsonl
```

The small [config](config.py) selects original `/messages/N` and `/answer_text`
fields and removes evaluation-only columns. [Guidance](guidance.md) supplies the
classification rubric, informed by the
[paper](https://arxiv.org/html/2603.14465v2) and
[annotation guide](https://github.com/RUCBM/AgentProcessBench/blob/main/annotation_platform/ANNOTATION_GUIDE_v1.md).

Checked-in results:

- [Aggregate and per-domain metrics](results.json)
- [Every prediction, gold label, probability, and source field](results-fields.jsonl)
- [Four-trace pilot metrics](pilot-results.json)

Downloaded originals, the unchanged sampled rows, full jt reports (including
field values), request previews, logs, and the cache are local under
`.jevotron/public-traces/`. The checked-in prediction tables omit trace text.
