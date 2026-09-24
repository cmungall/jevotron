---
name: jt-triage
description: Use jt (jevotron) to triage large data files for semantic anomalies, inconsistent fields, or cases needing review. Preview a few records, scan with task-specific guidance, inspect a bounded shortlist, and iteratively refine the assessment instead of reading or repeatedly searching the entire dataset into context. Use for CSV, JSONL, JSON, YAML, OBO, logs, and other supported data formats; exact lookups and deterministic counts are better handled directly.
---

# Triage large datasets with jt

Let jt assess records and bring a small, traceable review queue into the agent's
context. Use your reasoning to define the question, investigate candidates, and
refine the next pass. Do not infer that a high anomaly score proves an error.

## Establish the question and input

- Translate the user's question into concrete assessment guidance: relevant
  fields, expected relationships, units, and permitted exceptions. For finding
  cases rather than errors, explicitly define the cases of interest as `ANOMALY`
  and other records as `NORMAL` so high scores rank the desired cases.
- Check `jt --help` and `jt formats`. `jt` and `jevotron` are the same CLI. In a
  checkout use `uv run jt`; with an older installation use `jevotron` if needed.
  To install the CLI with Python 3.12+ and uv:
  `uv tool install git+https://github.com/cmungall/jevotron.git`.
- `preview` is local and requires no credential. Uncached scans send each full
  parsed entry, guidance, and exemplars to Jev and require `TYPESAFE_API_KEY` in
  the environment. Field selection limits scoring, not the entry context sent.
  If credentials are unavailable, finish the preview and prepare the scan
  command; report that live assessment has not run.
- Preview a few entries to discover structure and source locations. Prefer a
  stable, unique `--id-column` when one exists. Repeated `--field` flags take
  exact JSON Pointers; selected paths must exist in every processed entry.

```sh
jt preview data.csv --limit 3
jt preview data.csv --id-column id --field /description --limit 3
```

Adapt filenames and field names to the actual input. `--format-option
records=/items` selects a nested collection in JSON/YAML/TOML; it is not a row
predicate. Consult `jt formats FORMAT` for options. For memory-heavy formats,
varying schemas, or candidate-only follow-ups, read
[the iteration reference](references/iteration.md).

## Pilot, investigate, and refine

1. Write focused guidance to a task-local file. Start with a limited pilot to
   check the framing before committing to a large scan. `--limit` takes the
   first N entries, not a random sample or the top N matches. Use a separately
   prepared representative sample when file order would bias the pilot.
2. Save all assessments to JSONL on disk and preserve stderr and exit status.
   Read a bounded shortlist using the bundled script. It scans the saved report
   locally and keeps only K candidates in memory; it makes no API calls.
3. Investigate the shortlisted IDs and `source` locations in the original data.
   Read the fields and nearby context needed to check each case. Also spot-check
   some low-scoring records: a plausible shortlist alone does not show recall.
4. Refine ambiguous rules or add a few verified exemplars when evidence supports
   doing so. Rerun the pilot, then widen to the intended dataset when the framing
   is useful. For targeted follow-up, filter by candidate IDs in a parser while
   preserving complete records and source locations (see the reference).

```sh
jt scan data.csv --id-column id --field /description \
  --guidance-file rules.md --limit 50 \
  --cache .jevotron/triage.sqlite3 -o pilot.jsonl 2>pilot.log
# Check the scan's exit status before treating the report as complete.
python /path/to/installed/jt-triage/scripts/shortlist.py pilot.jsonl \
  --top 10 --threshold 0.5
```

Resolve the helper path relative to this installed skill, not the user's
working directory. Keep each iteration's guidance and report identifiable.
After a successful pilot, remove `--limit` and write to a new output path for
full coverage. Do not pipe a live scan to `head`: early pipe closure interrupts
the scan. For a massive run, omit `--sort-score`, which buffers **all** results,
even with `--warnings-only`.

## Iterate without unnecessary inference

- Reuse the same cache path. Threshold, output formatting, sorting, filenames,
  and reporting IDs do not change assessments. Adjust the shortlist's threshold
  locally to narrow or widen review without rescanning.
- Changes to entry content, selected fields, guidance, exemplars, criteria, or
  model require new assessments. Narrowing scored fields does not reuse a
  broader assessment. Restrict a changed follow-up to candidates if that scope
  answers the question; do not describe it as a new full-file assessment.
- `--warnings-only` filters emitted results, not input or API calls. Retain a
  complete report when you may need to lower the threshold later.
- Each entry is assessed independently. Cross-record duplicates, joins, global
  distributions, and graph consistency require deterministic analysis or a
  custom parser that supplies the needed context; jt does not retrieve it.
- Resume an interrupted scan with the same settings and cache. Successful
  entries replay, but a nonzero exit means the report is incomplete. Do not
  interpret missing results as normal records or silently treat a failed run
  as complete.

Stop when the requested review scope is covered, further iterations add no
useful evidence, or the task's budget is reached. Report the assessed scope
(pilot, subset, or full file), guidance and threshold used, candidate IDs,
field values and source locations, verified findings versus unresolved
suspicions, and paths to saved reports. Scores rank review priority; a lack of
warnings is not proof that the dataset is correct.
