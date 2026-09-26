---
hide:
  - navigation
  - toc
---

<div class="hero" markdown>

# One-shot anomaly detection on the command line

<p class="lead">Give jevotron a file and a little guidance. Get field-level anomaly scores, a focused review queue, and a cache that makes the next run cheaper.</p>

[Run your first scan](quickstart.md){ .md-button .md-button--primary }
[Explore the commands](reference/cli.md){ .md-button }
[Install agent skills](guides/agents.md){ .md-button }

</div>

<div class="terminal" role="region" aria-label="Example terminal workflow">
<div class="terminal-title">jevotron / airports.csv</div>
<pre><span class="prompt">$</span> jevotron scan airports.csv --guidance "Check airport locations."</pre>
</div>

!!! important "First, set your API key"
    Get a key from the [TypeSafe dashboard](https://console.typesafe.ai/) and set it in your shell:

    ```sh
    export TYPESAFE_API_KEY="your-api-key"
    ```

    Already set? You're ready to scan. `preview` needs no key.
    [API key setup →](quickstart.md#set-your-api-key) · [See the actual airport results →](examples/airports.md#actual-output)

<div class="workflow-grid" markdown>
<div markdown>
<span class="step">01 / PREVIEW</span>

### See what goes in

Inspect chunks, field paths, and exact model requests before making an API call.

[Preview a file →](quickstart.md#preview-the-input)
</div>
<div markdown>
<span class="step">02 / SCAN</span>

### Assess every entry

Jev scores selected fields together. Each entry gets the same guidance and optional examples.

[Choose your fields →](guides/files.md)
</div>
<div markdown>
<span class="step">03 / REVIEW</span>

### Start with the warnings

Sort suspicious entries, export CSV, or pipe JSONL into your existing shell workflow.

[Build a review queue →](guides/reports.md)
</div>
</div>

## Small setup. Useful defaults.

**CSV, YAML, JSON, TOML, text, OBO, and more** work out of the box, including
gzip files. The [format reference](reference/formats.md) covers defaults and
format-specific options. Select fields with
`--field`, add a sentence with `--guidance`, and run. Longer instructions can
come from `--guidance-file`. A local Python config is
available when a project needs custom parsing or reusable settings.

**Unchanged input reuses its assessment.** SQLite saves each successful result as
it arrives. Reorder a file, change a reporting threshold, or resume a failed run
without reassessing unchanged entries.

**A review aid with visible evidence.** Reports retain each field's probabilities,
the entry score, source location, and assessment date. The warning score is the
highest field anomaly probability; you choose the threshold.

## Try a complete example

| Example | What you'll do |
| --- | --- |
| [Airports / CSV](examples/airports.md) | Find two injected country errors in public data, then compare versions. |
| [Inventory / YAML](examples/inventory.md) | Apply written rules and a chosen exemplar to stock records. |
| [Measurement units / OBO](examples/obo.md) | Score definitions and repeated synonyms within independent stanzas. |
| [Agent traces / JSONL](examples/agent-traces.md) | Classify public agent traces and individual steps, then compare with published labels. |

### Agent traces: a measured pilot

On a small, length-filtered sample of **24 public traces**, jt matched **130 of
163 step-quality labels (79.8%)**. Harmful-step precision was **89.7%**, with
**70.3% recall**. The example uses original messages and tool definitions, with
human labels withheld from the model.

[Try the trace example →](examples/agent-traces.md) ·
[Read the full analysis and limitations →](analysis/agent-traces/index.md)
