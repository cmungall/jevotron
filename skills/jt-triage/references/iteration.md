# Iteration recipes

## Keep large inputs manageable

CSV/TSV and JSONL parse record by record. Plain JSON loads the document before
chunking; YAML loads each document, TOML loads the document, and text with
`split=file` loads the file. A small `--limit` does not avoid that initial load.
For inputs too large for those parsers, use a streaming conversion to JSONL or
a custom parser with an appropriate streaming library. Preserve original IDs
and source locations. Avoid splitting CSV or structured documents by raw lines.

Streaming reports avoid buffering every result, but jt still tracks IDs for
uniqueness and the cache grows with requests. Do not promise constant-memory
scans or sublinear inference: an uncached full scan assesses each entry.

## Narrow follow-up to saved candidates

Save the helper's JSON output as `candidates.json`:

```sh
python /path/to/installed/jt-triage/scripts/shortlist.py results.jsonl \
  --top 20 --threshold 0.7 > candidates.json
```

For CSV with a stable `id` column, save the following as `followup.py` beside
`candidates.json`. This filters before inference and keeps the complete source
record as context. Adapt the parser and ID column to the original scan.

```python
import json
from pathlib import Path

from jevotron import Config
from jevotron.parsers import CSV

HERE = Path(__file__).parent
selected = {
    row["id"]
    for row in json.loads((HERE / "candidates.json").read_text())["candidates"]
}


def parse(path):
    for chunk in CSV(id_column="id")(path):
        if chunk.id in selected:
            yield chunk


config = Config(parser=parse)
```

```sh
jt preview data.csv --config followup.py --field /description --limit 3
jt scan data.csv --config followup.py --field /description \
  --guidance-file refined-rules.md --cache .jevotron/triage.sqlite3 \
  -o followup.jsonl 2>followup.log
```

Check exit status and coverage against the chosen IDs; an input changed since
the first scan can remove or alter candidates. The parser still reads the
source sequentially, but only selected entries need inference. Do not add
`--format` or `--format-option` to a custom parser command; configure the parser
in Python. Positional IDs are unsafe across reordered file versions.

## Add verified exemplars

`--exemplars exemplars.json` accepts a JSON list, for example:

```json
[
  {
    "entry": {"id": "sample-1", "description": "Free sample", "price": 0},
    "assessment": {"/price": "NORMAL"}
  }
]
```

Use verified examples relevant to the user's rules, including valid exceptions.
These are context repeated for every entry, not additional records to scan.
Keep them short. Changing exemplars invalidates matching cached requests.

## Interpret and retain evidence

JSONL contains one result per assessed entry, with `id`, `source`, `label`,
`score`, `warning`, `fields`, `absent`, `model`, `assessed_at`, `request_hash`,
`cached`, and `usage`. `absent` lists selected paths the entry did not carry.
Each field includes its JSON Pointer `path`, `value`, `label`, `probabilities`,
`confidence`, and `score`. There is no generated prose explanation: use the
original record and domain evidence to explain why a candidate matters.

Entry score is the maximum field anomaly probability. Confidence is separate.
The helper applies its own threshold to scores; saved `warning` flags retain
the threshold from the original scan. Its counts cover report rows only: a
pilot, filtered report, or incomplete run cannot establish full-file coverage.
Cached `usage` describes the original call, not new spending.
