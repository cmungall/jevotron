"""Read-only, row-at-a-time adapters for local SQLite and DuckDB databases."""

import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import duckdb

from jevotron.models import Chunk, json_text, scalar_id


def detect_database(path: Path) -> str | None:
    """Check signatures before suffixes, including extensionless database files."""
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        header = stream.read(16)
    if header.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    # DuckDB stores an eight-byte checksum before its magic bytes.
    if header[8:12] == b"DUCK":
        return "duckdb"
    return None


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _value(value):
    """Preserve SQL values as JSON without rounding decimals or discarding blobs."""
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID, timedelta)):
        return str(value)
    if isinstance(value, bytes):
        return "hex:" + value.hex()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: _value(item) for key, item in value.items()}
        # SQL MAP keys need not be strings; don't stringify and collide keys.
        return [
            {"key": _value(key), "value": _value(item)} for key, item in value.items()
        ]
    return value


@dataclass(frozen=True)
class Table:
    schema: str
    name: str
    kind: str
    columns: tuple[str, ...]
    primary_key: tuple[str, ...]

    @property
    def selector(self) -> str:
        return f"{_quote(self.schema)}.{_quote(self.name)}"

    def to_dict(self) -> dict:
        return {
            "table": self.selector,
            "schema": self.schema,
            "name": self.name,
            "type": self.kind,
            "columns": list(self.columns),
            "primary_key": list(self.primary_key),
        }


@dataclass
class Database:
    engine: str
    tables: list[str] | None = None
    id_column: str | None = None
    fields: list[str] | None = None

    @contextmanager
    def _connect(self, path: Path):
        path = Path(path)
        if not path.is_file():
            raise ValueError(f"Database file does not exist or is not a file: {path}")
        if self.engine not in ("sqlite", "duckdb"):
            raise ValueError(f"Unsupported database engine: {self.engine!r}")
        connection = None
        try:
            if self.engine == "sqlite":
                connection = sqlite3.connect(
                    path.resolve().as_uri() + "?mode=ro", uri=True
                )
                connection.execute("PRAGMA query_only = ON")
                connection.execute("PRAGMA trusted_schema = OFF")
            else:
                connection = duckdb.connect(
                    str(path.resolve()),
                    read_only=True,
                    config={"enable_external_access": False},
                )
            # Keep catalog and rows in one consistent snapshot.
            connection.execute("BEGIN TRANSACTION")
            yield connection
        except (sqlite3.Error, duckdb.Error) as exc:
            raise ValueError(f"{path}: {self.engine}: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    def _catalog(self, connection) -> list[Table]:
        if self.engine == "sqlite":
            # table_list excludes FTS/RTree shadow tables by reporting their type.
            relations = sorted(
                (schema, name, kind)
                for schema, name, kind, *_ in connection.execute(
                    "PRAGMA table_list"
                ).fetchall()
                if schema == "main"
                and not name.startswith("sqlite_")
                and kind in ("table", "view", "virtual")
            )
            result = []
            for schema, name, kind in relations:
                columns = connection.execute(
                    "SELECT name, pk FROM pragma_table_xinfo(?) WHERE hidden != 1 ORDER BY cid",
                    (name,),
                ).fetchall()
                result.append(
                    Table(
                        schema,
                        name,
                        kind,
                        tuple(c[0] for c in columns),
                        tuple(
                            c[0] for c in sorted(columns, key=lambda c: c[1]) if c[1]
                        ),
                    )
                )
            return result
        relations = connection.execute(
            "SELECT schema_name, table_name, 'table' FROM duckdb_tables() "
            "WHERE NOT internal AND NOT temporary "
            "UNION ALL SELECT schema_name, view_name, 'view' FROM duckdb_views() "
            "WHERE NOT internal AND NOT temporary ORDER BY 1, 2"
        ).fetchall()
        columns = connection.execute(
            "SELECT schema_name, table_name, column_name FROM duckdb_columns() "
            "WHERE NOT internal ORDER BY schema_name, table_name, column_index"
        ).fetchall()
        keys = connection.execute(
            "SELECT schema_name, table_name, constraint_column_names "
            "FROM duckdb_constraints() WHERE constraint_type = 'PRIMARY KEY'"
        ).fetchall()
        return [
            Table(
                schema,
                name,
                kind,
                tuple(c for s, t, c in columns if (s, t) == (schema, name)),
                next((tuple(k) for s, t, k in keys if (s, t) == (schema, name)), ()),
            )
            for schema, name, kind in relations
        ]

    def catalog(self, path: Path) -> list[Table]:
        """List user tables and views without reading their rows."""
        with self._connect(path) as connection:
            return self._catalog(connection)

    def _select(self, catalog: list[Table]) -> list[Table]:
        if not self.tables:
            selected = [table for table in catalog if table.kind != "view"]
            if not selected:
                raise ValueError(
                    "Database has no user tables; use 'jt tables' to list views"
                )
            return selected
        selected = []
        for name in self.tables:
            # A canonical quoted selector is always unambiguous, even when a
            # different table's literal name happens to equal that selector.
            matches = [t for t in catalog if name == t.selector] or [
                t
                for t in catalog
                if name
                in (
                    t.name,
                    f"{t.schema}.{t.name}",
                    t.selector,
                )
            ]
            if len(matches) != 1:
                reason = "Ambiguous" if matches else "Unknown"
                available = (
                    ", ".join(t.selector for t in matches or catalog) or "(none)"
                )
                raise ValueError(f"{reason} table {name!r}; available: {available}")
            if matches[0] not in selected:
                selected.append(matches[0])
        return selected

    def __call__(self, path: Path):
        with self._connect(path) as connection:
            tables = self._select(self._catalog(connection))
            # Validate the entire selection before yielding any requests.
            if self.id_column is not None:
                for table in tables:
                    if self.id_column not in table.columns:
                        raise ValueError(
                            f"{table.selector}: Missing identifier column {self.id_column!r}"
                        )
            for table in tables:
                keys = (
                    (self.id_column,)
                    if self.id_column is not None
                    else table.primary_key
                )
                order = (
                    " ORDER BY " + ", ".join(_quote(k) for k in keys) if keys else ""
                )
                cursor = connection.execute(f"SELECT * FROM {table.selector}{order}")
                columns = [column[0] for column in cursor.description]
                index = 0
                while (row := cursor.fetchone()) is not None:
                    index += 1
                    data = {
                        name: _value(value)
                        for name, value in zip(columns, row, strict=True)
                    }
                    values = [data[key] for key in keys]
                    if self.id_column is not None:
                        scalar_id(values[0])
                    # SQLite permits NULL in some PRIMARY KEY declarations.
                    if keys and all(value is not None for value in values):
                        identity = "key:" + json_text(values)
                    else:
                        identity = f"row:{index}"
                    yield Chunk(
                        f"{table.selector}:{identity}",
                        data,
                        self.fields,
                        f"{path}:table:{table.selector}:row:{index}",
                    )


def SQLite(**options) -> Database:
    return Database("sqlite", **options)


def DuckDB(**options) -> Database:
    return Database("duckdb", **options)
