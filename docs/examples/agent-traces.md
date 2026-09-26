# Agent traces / JSONL

Classify an agent's individual steps and final result using its original
conversation and tool definitions. This example runs jt on public
[AgentProcessBench](https://huggingface.co/datasets/LulaCola/AgentProcessBench)
JSONL files and compares the predictions with published human labels.

A trace is one entry. Each assistant message is a field to classify; the entire
trace remains available as context. No normalized trace format is required.

## What the run found

We assessed **24 traces and 163 assistant steps** across multi-hop QA, web
research, function calling, and customer service using `jev-1.13.0`.

| Task | Agreement with published labels | Majority-label baseline |
| --- | --- | --- |
| Step quality: useful / neutral / harmful | 130 / 163 (79.8%) | 71.8% |
| Final-result quality | 19 / 24 (79.2%) | 50.0% |

Harmful-step precision was **89.7%**, with **70.3% recall**. The run identified
repeated calls to nonexistent tools and an unsupported baggage-allowance answer.
It missed a travel workflow that ended with a successful support-ticket creation
but left earlier booking and insurance requests unfulfilled.

This is a **small pilot**, balanced between successful and unsuccessful outcomes
and excluding long traces. It tests retrospective classification with the full
trace visible. These figures are not full-benchmark results or a guarantee for
other traces. Human labels and reference answers were withheld from the model.

[Read the full analysis](../analysis/agent-traces/index.md), including per-domain
results, disagreements, sampling decisions, token usage, and limitations.

## Try it from a checkout

The study scripts, configuration, and saved results are in
`analysis/agent-traces/`. Fetch the pinned public dataset and select the same
24 traces:

```sh
uv run python analysis/agent-traces/prepare.py
```

Inspect four requests locally, without an API key:

```sh
uv run jt preview .jevotron/public-traces/sample.jsonl \
  --config analysis/agent-traces/config.py --limit 4 \
  -o .jevotron/public-traces/preview.jsonl
```

The [config](../analysis/agent-traces/config.py) removes evaluation-only columns
and selects original `/messages/N` fields for assistant messages, plus
`/answer_text` for the final result. The message bodies and tool definitions
retain their original structure. This is field selection, not trace conversion.
The [guidance](../analysis/agent-traces/guidance.md.txt) defines three labels:

| Label | Assistant step | Final result |
| --- | --- | --- |
| `POSITIVE` | Correct and materially advances the task | Correct and complete |
| `NEUTRAL` | Reasonable exploration, little progress, or insufficient evidence | Insufficient evidence to decide |
| `NEGATIVE` | Incorrect, counterproductive, or violates constraints | Wrong, incomplete, or noncompliant |

For a live scan, [set `TYPESAFE_API_KEY`](../quickstart.md#set-your-api-key).
Uncached requests send the selected trace entries to Jev.

```sh
uv run jt scan .jevotron/public-traces/sample.jsonl \
  --config analysis/agent-traces/config.py \
  --cache .jevotron/public-traces/cache.sqlite3 \
  -o .jevotron/public-traces/full.jsonl 2> .jevotron/public-traces/full.log
```

After the scan exits successfully, compare its output with the withheld labels:

```sh
uv run python analysis/agent-traces/evaluate.py \
  .jevotron/public-traces/full.jsonl
```

Re-running evaluation uses saved predictions and makes no model calls. Re-running
an unchanged scan reuses the cache. New live predictions may differ from this
recorded run.

## Inspect the saved results

You can examine the completed study without making an API call:

- [Aggregate and per-domain metrics](../analysis/agent-traces/results.json)
- [Every predicted label, probability, and gold label](../analysis/agent-traces/results-fields.jsonl)
- [Sample IDs, source URLs, and file hashes](../analysis/agent-traces/sample-manifest.json)
- [Download the study scripts and results](../downloads/agent-trace-analysis.zip)

The download preserves the `analysis/agent-traces/` directory layout; extract it
into a jevotron checkout to run the commands above. Downloaded source traces,
full local reports, and caches are not bundled.

## Use the pattern on your own traces

Start with a question such as “Which steps repeat a failed action?” or “Does the
final result fulfill all user requests?” Select the relevant fields in the
original format, supply guidance, and keep enough surrounding trace context to
judge them. Treat a high score as a review candidate and inspect some low-scoring
cases too.

For this study, the configured anomaly label is `NEGATIVE`, so scores rank
potentially harmful steps. The accuracy and precision above use the returned
labels, not a probability threshold. A failure during a successful run and an
unsuccessful final outcome are separate questions.
