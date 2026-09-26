# Custom parsers

Use this when the [built-in formats](../reference/formats.md), `--format-option`,
and `--field` options are not enough.
Users still run the same CLI:

```sh
jevotron preview data.custom --config jev_config.py
jevotron scan data.custom --config jev_config.py --warnings-only
```


A parser is a callable accepting a `Path` and yielding `Chunk` objects. No
inheritance, registration, decorators, or package installation is required for
a local adapter. Import existing parsing libraries or wrap a built-in parser.

```python
import csv
from jevotron import Chunk, Config


def parse(path):
    with path.open(encoding="utf-8", newline="") as stream:
        for index, row in enumerate(csv.DictReader(stream), 1):
            yield Chunk(
                id=row["ident"],
                data=row,
                fields=["/name", "/iso_country"],
                source=f"{path}:row:{index}",
            )


config = Config(parser=parse)
```

| Chunk property | Contract |
| --- | --- |
| `id` | Nonempty string, unique within a scan; used in reports. |
| `data` | JSON-compatible object, array, or scalar; the entry shown to Jev. Object keys must be strings. |
| `fields` | Optional list of JSON Pointers to score. Defaults to top-level object keys, or the root for other values. |
| `source` | Optional string locating the original entry; carried into reports. |
| `absent` | Optional list of selected JSON Pointers the entry does not carry; reported as `absent`, never sent to Jev or hashed. |

Field paths use JSON Pointer syntax: `/name`, `/terms/0/start`, `/synonym/1`.
Escape a literal `/` in a key as `~1` and a literal `~` as `~0`. The empty path
selects the whole entry. Missing paths, duplicate selected paths, and empty
field selections are errors. Filter an entry out in your parser to skip it.

## Reuse a built-in parser

Format-specific settings are ordinary constructor arguments. For example,
`--format-option 'delimiter=;'` corresponds to `CSV(delimiter=";")`:

```python
from jevotron import Config
from jevotron.parsers import CSV

config = Config(parser=CSV(delimiter=";", id_column="sku", fields=["/price"]))
```

Other adapters are `JSON`, `JSONL`, `YAML`, `TOML`, `Text`, `OBO`, `FASTA`, and
`GMT`. All accept `encoding`; JSON/YAML/TOML accept `records`, `Text` accepts
`split`, and `OBO` accepts `stanza`. CSV/JSON/JSONL/YAML/TOML also accept
`id_column` and `fields`. The [format reference](../reference/formats.md)
documents their behavior.

For automatic detection inside a wrapper, call
`for_path(path, format=None, options=None)` from `jevotron.parsers`. It returns
a callable. `options` is a dictionary of strings using the same keys as
`--format-option`:

```python
from jevotron import Config
from jevotron.parsers import for_path


def parse(path):
    parser = for_path(path, options={"records": "/products"})
    for chunk in parser(path):
        if chunk.data.get("active", True):
            yield chunk


config = Config(parser=parse)
```

The parser in a config controls parsing completely; set its options there.
The CLI rejects `--format`, `--format-option`, and `--table` alongside a custom parser.
`--field` and `--id-column` still override fields and IDs on its yielded chunks.
Built-in adapters apply an ID override before validating the configured ID column.
Custom parser code must successfully yield a chunk before its ID can be replaced.
ID columns must contain nonempty scalar values; lists, mappings, and nulls are
rejected consistently in Python parser settings and CLI options.
This keeps local Python adapters simple: no registration or plugin interface
is needed, and existing ETL libraries can be used directly.

OBO is a lightweight tokenizer: it preserves repeated tags, value text,
comments, and escapes, and joins continued lines. It ignores the file header
and does not resolve identifiers, interpret axioms, or traverse the graph.
Supply relevant general information through guidance. The
[units config](../downloads/units/jev_config.py) demonstrates wrapping OBO to score
only definitions and synonyms while keeping the whole stanza visible.
