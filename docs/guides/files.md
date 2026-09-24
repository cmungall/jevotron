# Files & field selection

The CLI detects SQLite and DuckDB by file header, and other formats by file
extension. Start by previewing:

```sh
jevotron preview records.csv
jevotron preview records.yaml
jevotron preview ontology.obo
jevotron preview services.toml
jevotron preview events.jsonl.gz
jt preview inventory.db
```

| Format | One entry | Default fields |
| --- | --- | --- |
| CSV / TSV | One row | Each column |
| YAML | Each top-level list item, or one mapping/scalar document | Top-level keys |
| JSON | Each top-level array item, or one object/scalar | Top-level keys |
| JSONL | Each nonblank line | Top-level keys |
| OBO | One stanza | Each tag-value occurrence |
| TOML | One document; use `records` for an array of tables | Top-level keys |
| Text / Markdown | One paragraph (configurable) | `/text` |
| Logs / textlines | One nonblank line (configurable) | `/text` |
| FASTA | One sequence record | ID, description, sequence |
| GMT | One named set | Name, description, members |
| SQLite / DuckDB | One row from each user table | Each column |

YAML supports multiple documents and keeps dates as text. CSV values remain
strings, preserving leading zeros and empty cells. Duplicate headers, duplicate
YAML/JSON keys, and ragged CSV rows are rejected rather than silently dropped.

The [file format reference](../reference/formats.md) explains every parser's
defaults, data shapes, extensions, gzip support, and options. Discover options
without leaving the terminal:

```sh
jevotron formats csv
jevotron preview export.data --format csv --format-option 'delimiter=;'
jevotron preview catalog.json --format-option records=/products
```

`records` selects a collection before chunking. `--field` selects what to
score inside each chunk.

## Choose fields without writing Python

Repeat `--field` for each JSON Pointer you want assessed:

```sh
jevotron scan airports.csv --id-column ident \
  --field /iso_country --field /latitude_deg --field /longitude_deg
```

The entire entry remains visible; only selected fields receive scores.
`--field` overrides a parser or config's selected fields. Omit it to use defaults.

## Name entries

```sh
jevotron preview products.jsonl --id-column sku
```

`--id-column` selects a top-level scalar field as the reporting ID. IDs must be
nonempty and unique within a scan. Without it, tabular parsers use positional
IDs and OBO uses the stanza's `id`. Databases use primary keys when available,
otherwise row positions, and always qualify IDs with the schema and table name
(also with `--id-column`). See [database selection rules](databases.md).
Reporting IDs do not affect the cache, but
the same values inside the entry data do.

## Nested and repeated fields

```sh
jevotron preview people.yaml --field /terms/0/start
jevotron preview units.obo --field /def/0
```

| Path | Selects |
| --- | --- |
| `/name` | Top-level `name` |
| `/terms/0/start` | First term's start date |
| `/synonym/1` | Second OBO synonym |
| `/a~1b` | A key literally named `a/b` |
| `/a~0b` | A key literally named `a~b` |
| `''` | The whole entry (use `--field ''`) |

A selected path must exist in every processed entry. Paths are exact: wildcard
selection is not implemented. For varying structures, use a
[small custom parser](../advanced/parsers.md) through `--config`.

## OBO behavior

Every tag maps to a list, even when it appears once. The stanza type is retained
as `_stanza` context. Raw values retain comments and escapes; continued lines
are joined. File headers are ignored. No identifiers are resolved and no graph
is traversed. Put shared background information in `--guidance`.
