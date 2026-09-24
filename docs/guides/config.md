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
