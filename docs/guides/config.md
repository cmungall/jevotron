# Project configuration

Most scans need only flags. Use `--config` when a project needs reusable settings,
custom labels, or a parser written by you or an agent:

```sh
jevotron preview data.csv --config jev_config.py
jevotron scan data.csv --config jev_config.py --warnings-only
```

## A small local config

Save this as `jev_config.py`, with `rules.md` next to it:

```python
from pathlib import Path
from jevotron import Config
from jevotron.parsers import CSV

HERE = Path(__file__).parent
config = Config(
    parser=CSV(id_column="ident", fields=["/name", "/iso_country"]),
    guidance=(HERE / "rules.md").read_text(encoding="utf-8"),
    threshold=0.7,
)
```

Config files are explicitly loaded, ordinary Python. They export one `Config`
object named `config`. No plugin registration is required. Install any custom
parser dependencies in the same environment as the CLI.

## Override settings for a run

```sh
jevotron scan data.csv --config jev_config.py \
  --field /iso_country --threshold 0.9 --guidance-file stricter-rules.md
```

CLI guidance, exemplars, model, threshold, field selection, and ID selection
override the corresponding config or parser behavior. Relative CLI paths use
your working directory; config-relative paths should use `__file__` as above.

For built-in file and database parsers, `--id-column` replaces the configured ID
column before parsing and validation, without modifying the reusable config.
An arbitrary custom parser must first yield a chunk successfully; the CLI then
replaces its reporting ID. The flag cannot bypass errors inside custom parser code.

## Use OpenAI Decisions

The [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions)
offers the same question types as Jev. Pick a `gpt-*` model and jevotron sends each
request there instead:

```sh
export OPENAI_API_KEY="your-api-key"
jevotron scan spiked.csv --model gpt-6-luna --guidance "Check airport locations."
```

```python
config = Config(model="gpt-6-luna", guidance="Check airport locations.")
```

Each field is still one choice question. The entry, guidance, and exemplars go in
the single-turn text input. Question keys become question names, so answers route
back to the same fields. Cache, batching, and output formats are unchanged. The
model name is part of every cache key, so OpenAI and Jev assessments never mix.

`jevotron preview` shows the cached (Jev-shaped) `request`, and for OpenAI models
it also shows the translated `wire_request` that is actually sent.

Caveats:

- Pricing differs: OpenAI charges $0.10 per million input tokens and Jev charges
  $0.042, with no output charge from either. Token estimates and `--batch-tokens`
  budgets are based on Jev's tokenizer and context limits.
- If the API declines a question (a `refusal` answer), the scan stops and caches
  nothing from that request. Entries assessed in earlier requests stay cached;
  others in the same batch do not. A rerun usually hits the same refusal, so
  exclude or edit that entry to continue.
- jevotron sends text only. Image input is not used.
- Scores from different models are not directly comparable. Re-check thresholds
  on a labelled sample before switching.

## Customize labels

```python
from jevotron import Config

config = Config(
    labels=["PASS", "REVIEW"],
    criteria={"PASS": "Consistent with the guidance", "REVIEW": "Likely incorrect"},
    anomaly_label="REVIEW",
)
```

`anomaly_label` selects the probability used for scoring and warnings.
Then run the same `preview` and `scan` commands with this config.

For custom formats, see the advanced [parser contract](../advanced/parsers.md).
