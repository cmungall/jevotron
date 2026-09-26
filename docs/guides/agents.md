# Agent skills

When the task is to review a massive data file, let jt assess records and let
the agent reason about a focused set of cases. The **jt-triage** skill provides
that loop: preview, pilot, shortlist, investigate, refine, and expand coverage.
It is useful for semantic inconsistencies and cases requiring judgment. Exact
lookups, counts, and joins are still better handled with deterministic tools.

## Install

```sh
npx skills add cmungall/jevotron --skill jt-triage
```

Choose your agent in the installer, or add `--agent codex` or
`--agent claude-code`. Add `--global` to use the skill across projects. To see
the catalog first, run `npx skills add cmungall/jevotron --list`. These commands
use the [skills CLI](https://github.com/vercel-labs/skills).

For local development before publishing, run `npx skills add . --skill
jt-triage` from this checkout. The GitHub command installs the published version.

The skill installs instructions and a Python helper for selecting a bounded
shortlist from saved results. Install the runtime separately:

```sh
uv tool install jevotron
jt --help
```

Requires Python 3.12+. `pip install jevotron` works the same way, and
`uvx --from jevotron jt ...` runs it without installing. In a checkout,
`uv run jt` works too. `jt` is an alias for `jevotron`. [Configure `TYPESAFE_API_KEY`](../quickstart.md#set-your-api-key)
for live scans; preview and complete cache replays do not require a key.
Uncached scans send full parsed entries, guidance, and exemplars to Jev.

### Claude Code marketplace

As an alternative installation route, add this repository as a
[Claude Code marketplace](https://code.claude.com/docs/en/plugin-marketplaces):

```text
/plugin marketplace add cmungall/jevotron
/plugin install jt-skills@jevotron
```

Use one installation route per agent to avoid duplicate skills.

## Give the agent a review question

For example:

> Use jt to review this large airport CSV for country codes inconsistent with
> the name and location. Start with a pilot, investigate the strongest cases,
> refine the rules if needed, and scan the full file. Return a short review queue
> with IDs, field values, and source locations.

Or, for cases of interest rather than errors:

> Use jt to find inventory descriptions that appear to require cold storage.
> Treat those as the cases to flag. Check a pilot and some low-scoring records,
> then give me the strongest candidates and explain the evidence.

The skill defines the requested cases as the anomaly class so that high scores
rank them for review. It does not treat those cases as incorrect data.

## How the loop works

| Stage | Agent action | Artifact |
| --- | --- | --- |
| Preview | Inspect a few records; select IDs and fields. | Checked parser and field paths |
| Pilot | Define task-specific guidance and assess a limited set. | Rules file, JSONL report, stderr log |
| Review | Rank the saved report locally; read a bounded shortlist and relevant original records. | Candidates with source locations |
| Refine | Correct ambiguous guidance, add verified examples, or restrict a follow-up to candidate IDs. | Revised rules and a new report |
| Expand | Scan the intended scope, reusing identical cached requests. | Full report and final review queue |

`--limit` assesses the first N entries, not a representative sample. A pilot
checks the framing, not full-file correctness. The agent should distinguish
pilot, subset, and full-file findings, and check exit status before treating
any report as complete.

For massive runs the skill saves streaming JSONL and uses a bounded-memory
helper to find the highest-scoring candidates locally. `--sort-score` buffers
all scan results. JSON and some other parsers also load whole documents; use
JSONL or a streaming custom parser when necessary.

## What iteration saves

Changing a review threshold or selecting a different shortlist from a saved
report requires no new inference. Repeating identical requests reuses the
cache. Changing guidance, exemplars, fields, or entry content requires fresh
assessments. A candidate-only follow-up reduces the number of assessments;
`--warnings-only` only reduces the output.

Each record is assessed independently. Cross-record relationships require
additional analysis or context supplied by a custom parser. Scores prioritize
review and must be checked against the source evidence.

See [field selection](files.md), [guidance and exemplars](guidance.md),
[caching](cache.md), and [custom parsers](../advanced/parsers.md).
