# jevotron

**Find the fields worth a second look.** A CLI for field-level anomaly detection
in tabular, structured, and text files, SQLite, and DuckDB, powered by [Jev](https://docs.typesafe.ai/api).

Preview the input, scan every entry, and export a focused review queue. Each
chunk is independent and sees the same optional guidance and exemplars.
SQLite caching reuses assessments across file versions.

## Install and run

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/). From this checkout:

```sh
uv tool install .
jevotron --help

# Preview is free and needs no API key.
jevotron preview examples/airports/spiked.csv \
  --id-column ident --field /iso_country --limit 1

# Get a key from https://console.typesafe.ai/ and set it in this shell.
export TYPESAFE_API_KEY="your-api-key"

jevotron scan examples/airports/spiked.csv --guidance "Check airport locations."
```

`jt` is a short alias for `jevotron`; both accept the same commands and options.
Database files are detected by their
headers, even with an unfamiliar extension. No export or database configuration
is needed; DuckDB support is included in the installation:

```sh
jt tables inventory.db                          # List tables, columns, and keys
jt preview inventory.db                         # Preview rows, no API key
jt scan warehouse.duckdb --table products        # Select a table
jt scan inventory.db                            # Scan all user tables
```

Databases are opened read-only. Each row is an entry, each column is a field,
and primary keys supply table-qualified IDs. See the
[database guide](docs/guides/databases.md) for examples and selection rules.

**API key:** Live scans require `TYPESAFE_API_KEY` from the
[TypeSafe dashboard](https://console.typesafe.ai/). An existing environment
variable is used automatically; preview and fully cached runs need no key.
[Full setup instructions](docs/quickstart.md#set-your-api-key).

For development, use `uv sync` and `uv run jevotron ...`. The CLI uses **Typer**,
with grouped help and shell completion:

```sh
jevotron scan --help
jevotron formats
jevotron formats csv
jevotron --install-completion
```

## Agent skills

Give your agent a large data file and a review question. The **jt-triage** skill
teaches it to preview a few records, run a focused pilot, inspect a small queue
of candidates, refine the guidance, and expand the scan. Full results stay on
disk; the agent reads the cases that need reasoning. Cached assessments make
repeat runs cheaper when the requests are unchanged.

```sh
npx skills add cmungall/jevotron --skill jt-triage
```

The skill installer supports multiple agents; it installs instructions and a
local shortlist helper. Install the CLI separately with
`uv tool install git+https://github.com/cmungall/jevotron.git` and set
`TYPESAFE_API_KEY` for live scans.

Try: “Use jt to find inconsistent country assignments in this large airport
file. Pilot the rules, investigate the strongest cases, then scan the full file
and give me a review queue with source locations.”

[Skill catalog](skills/README.md) ·
[Installation and workflow guide](docs/guides/agents.md) ·
[Skill instructions](skills/jt-triage/SKILL.md)

## Documentation

Read the [published documentation](https://cmungall.github.io/jevotron/).

A Material for MkDocs site with light/dark modes, search, downloadable examples,
and a CLI reference generated from the actual commands:

```sh
uv run --group docs mkdocs serve
```

Open **http://127.0.0.1:8000**. Build static files with
`uv run --group docs mkdocs build --strict`. Changes on `main` are published to
GitHub Pages by the documentation workflow.

- [First scan](docs/quickstart.md)
- [Files and field selection](docs/guides/files.md)
- [SQLite and DuckDB](docs/guides/databases.md)
- [File format reference](docs/reference/formats.md) — CSV/TSV, JSON/JSONL, YAML,
  TOML, text/Markdown, OBO, FASTA, GMT, and gzip; defaults and parser options
- [Guidance and examples](docs/guides/guidance.md)
- [Review and export](docs/guides/reports.md)
- [Caching across versions](docs/guides/cache.md)
- [Shell pipelines](docs/guides/shell.md)
- [Project configuration](docs/guides/config.md)

## Examples

| Example | Includes |
| --- | --- |
| [Airports](examples/airports/) | Public-domain CSV, baseline and spiked versions, provenance, known changes |
| [Inventory](examples/inventory/) | Synthetic YAML with written rules and a selected exemplar |
| [Units](examples/units/) | Synthetic OBO with repeated tags and a custom local config |

Flags handle common tasks. For custom formats or reusable settings, pass
`--config jev_config.py`; a parser is an ordinary Python function yielding chunks.
The [advanced docs](docs/advanced/parsers.md) cover that contract.

## Development

```sh
uv sync --group docs
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run --group docs mkdocs build --strict
uv build
```

Unit tests use fake responses and remove the API key; they make no paid calls.
