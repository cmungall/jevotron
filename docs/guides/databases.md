# SQLite and DuckDB

Pass a local database directly to `jt` (the short alias for `jevotron`). Both
engines work with the normal installation; no export or connection string is
needed. SQLite and DuckDB are identified by their file headers, including files
without extensions and DuckDB databases named `.db`.

```sh
jt tables inventory.db
jt preview inventory.db
jt scan inventory.db --guidance "Check product names and prices."
```

`tables` lists user tables and views as JSONL, including their columns, primary
keys, and exact quoted selectors. It reads catalog metadata, without counting
or scanning rows. Like `preview`, it needs no API key.

## Try both engines

From the repository checkout, create two small synthetic databases in a new
directory:

```sh
uv run python examples/databases/create.py /tmp/jt-database-demo
uv run jt tables /tmp/jt-database-demo/inventory.sqlite
uv run jt preview /tmp/jt-database-demo/inventory.sqlite --table products
uv run jt preview /tmp/jt-database-demo/inventory.duckdb --table products
```

Both contain `products` and `suppliers` tables, plus a `product_names` view.
The product rows deliberately contain a negative price and a misspelled name.
Once your [API key is set](../quickstart.md#set-your-api-key), scan either file:

```sh
uv run jt scan /tmp/jt-database-demo/inventory.duckdb \
  --table products --field /name --field /price \
  --guidance "Product names should be correctly spelled. Prices must be positive." \
  --warnings-only --output-format csv -o product-warnings.csv
```

## Defaults and selection

With no `--table`, scan processes **all user tables**, alphabetically by schema
and table. System tables, SQLite FTS/RTree shadow tables, and views are excluded
from this default. Empty tables produce no entries; a database with no user
tables produces a descriptive error.

Each row becomes an entry whose keys are column names. All columns are assessed
by default. `--field` selects JSON Pointers exactly as for files, and the entire
row remains visible as context. Rows are assessed independently; joins, foreign
key traversal, and database-wide consistency checks are not automatic.

Repeat `--table` to select several tables, in the order given. Views can be
selected explicitly:

```sh
jt preview warehouse.duckdb --table products --table suppliers
jt preview warehouse.duckdb --table analytics.products
jt preview warehouse.duckdb --table product_names
jt preview inventory.sqlite --table 'odd table name'
```

An unqualified name must identify exactly one table or view. For duplicate
names across DuckDB schemas, use `schema.table`. For names containing dots or
quotes, copy the exact quoted `table` selector from `jt tables`, preserving
the double quotes inside shell single quotes, for example `--table '"main"."a.b"'`.
Unknown or ambiguous names produce an error listing available choices.

`preview` defaults to three rows **total**, and `scan` defaults to all rows.
`--limit N` caps the total across selected tables; it is not a per-table limit.
Use `--table` to preview a later table directly.

## IDs, ordering, and caching

Primary keys supply entry IDs automatically, including composite keys. Rows
with primary keys are ordered by those keys. For example, product 1 has ID
`"main"."products":key:[1]`; supplier 1 has a different table-qualified ID.

Use `--id-column sku` to override the primary key. This column must exist in
every selected table and contain nonempty scalar values unique within each
table. The schema and table qualifier is retained. Duplicate IDs fail the run.

Without a usable primary key, IDs use row positions, such as
`"main"."products":row:1`. This also covers views and SQLite rows with NULL
primary-key components. Unkeyed row order is engine-defined; these positional
IDs are not stable across database changes. Each result's `source` names the
input, schema, table, and row position.

Caching uses the request's values, fields, guidance, exemplars, and model, just
as for files. Reporting IDs and source paths do not affect the cache. Identical
row requests can reuse assessments across tables and files. Schema definitions
and table names are not added to model context; use guidance when that context
matters.

## SQL values in JSON

SQL NULL becomes JSON null. Strings, integers, finite floating-point values,
and booleans retain their Python/JSON types. SQLite uses its stored value types;
it does not infer dates or booleans from declared column types.

DuckDB values that JSON cannot represent directly use these conversions:

| SQL value | JSON representation |
| --- | --- |
| DECIMAL | Exact decimal string, preserving scale |
| DATE, TIME, TIMESTAMP | ISO-format string |
| UUID | String |
| INTERVAL | Python driver's duration string |
| BLOB (both engines) | `hex:` followed by hexadecimal bytes |
| NaN, positive/negative infinity | `"nan"`, `"inf"`, `"-inf"` strings |
| LIST, ARRAY, STRUCT | Arrays or objects, recursively converted |
| MAP with non-string keys | Array of `{"key": ..., "value": ...}` objects |

JSON stored in a text column remains text; it is not parsed automatically.

## Read-only behavior

Connections open read-only and use a transaction for a consistent snapshot.
Rows are fetched incrementally; `--limit`, errors, and completed scans close the
connection. The engine may still sort or buffer data to execute a query. SQLite
reads committed WAL data; DuckDB may require a concurrent writer to close its
connection before the file can be opened.

The input cannot also be used as the report or assessment cache, including
through symlinks or hardlinks. DuckDB external file/network access is disabled;
views that depend on external sources cannot be scanned. Files must be local,
unencrypted, and readable by the installed engine version. Decompress `.gz`
database files before using them. Missing files are never created by a scan.

The adapters use [SQLite read-only URI mode](https://www.sqlite.org/uri.html)
and [DuckDB's read-only connection option](https://duckdb.org/docs/current/clients/python/dbapi).

## Python configuration

```python
from jevotron import Config
from jevotron.databases import DuckDB

config = Config(
    parser=DuckDB(tables=["products"], id_column="sku", fields=["/name"]),
    guidance="Check product names.",
)
```

`SQLite` accepts the same arguments. As with other custom parser configs, put
table selection in the config rather than combining it with CLI parser flags.
Guidance-only configs work with automatic detection and `--table`.
