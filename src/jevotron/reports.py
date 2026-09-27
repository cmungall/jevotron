"""Typed report views and DuckDB expressions over assessment results."""

import json
from itertools import islice

import duckdb

from jevotron.models import Result, json_text

COMMON_COLUMNS = {
    "id": "VARCHAR",
    "source": "VARCHAR",
    "label": "VARCHAR",
    "score": "DOUBLE",
    "confidence": "DOUBLE",
    "warning": "BOOLEAN",
    "model": "VARCHAR",
    "assessed_at": "VARCHAR",
    "request_hash": "VARCHAR",
    "cached": "BOOLEAN",
    "absent": "JSON",
    "usage": "JSON",
}
ENTRY_COLUMNS = {**COMMON_COLUMNS, "fields": "JSON"}
FIELD_COLUMNS = {
    **COMMON_COLUMNS,
    "path": "VARCHAR",
    "value": "JSON",
    "probabilities": "JSON",
    "entry_label": "VARCHAR",
    "entry_score": "DOUBLE",
    "entry_confidence": "DOUBLE",
    "entry_warning": "BOOLEAN",
}


def report_rows(result: Result, rows: str, threshold: float):
    """Keep original values intact; field rows carry their parent summary too."""
    entry = result.to_dict()
    entry["confidence"] = max(result.fields, key=lambda field: field.score).confidence
    if rows == "entries":
        yield entry
        return
    fields = entry.pop("fields")
    parent = {
        f"entry_{key}": entry[key]
        for key in ("label", "score", "confidence", "warning")
    }
    for field in fields:
        yield {**entry, **parent, **field, "warning": field["score"] >= threshold}


def _check_row_expression(connection, clause: str, expression: str) -> None:
    """Use DuckDB's parser, not a second SQL grammar, to exclude cross-row SQL."""
    tree = json.loads(
        connection.execute(
            "SELECT json_serialize_sql(?)",
            [f"SELECT * FROM report_rows {clause} {expression}"],
        ).fetchone()[0]
    )

    def visit(node):
        if isinstance(node, dict):
            if node.get("class") in {"SUBQUERY", "WINDOW"}:
                raise ValueError(
                    f"{clause} accepts row expressions, not subqueries or window functions"
                )
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(tree)


class ReportQuery:
    """Validate before inference; filter in batches or order the complete report.

    Values with heterogeneous shapes stay JSON. An independent payload preserves
    the original Python representation when DuckDB returns the selected rows.
    """

    batch_size = 256

    def __init__(self, rows: str, where: str | None, order_by: str | None):
        self.columns = ENTRY_COLUMNS if rows == "entries" else FIELD_COLUMNS
        self.where = where
        self.order_by = order_by
        self.connection = duckdb.connect(
            ":memory:",
            config={
                "enable_external_access": False,
                "autoload_known_extensions": False,
                "autoinstall_known_extensions": False,
            },
        )
        try:
            columns = {**self.columns, "__ordinal": "BIGINT", "__payload": "JSON"}
            self.schema = ", ".join(
                f'"{name}" {kind}' for name, kind in columns.items()
            )
            self.connection.execute(f"CREATE TABLE report_rows ({self.schema})")
            relation = self.connection.table("report_rows")
            if where is not None:
                relation = relation.filter(where)
                _check_row_expression(self.connection, "WHERE", where)
            if order_by is not None:
                relation = relation.order(order_by)
                _check_row_expression(self.connection, "ORDER BY", order_by)
            # Binding catches unknown columns and invalid types even on empty input.
            relation.limit(0).fetchall()
        except Exception:
            self.close()
            raise

    def close(self):
        self.connection.close()

    def _insert(self, batch):
        records = []
        for ordinal, row in batch:
            record = {
                key: json_text(row[key]) if kind == "JSON" else row[key]
                for key, kind in self.columns.items()
            }
            records.append(
                {**record, "__ordinal": ordinal, "__payload": json_text(row)}
            )
        self.connection.execute(
            f"INSERT INTO report_rows SELECT r.* FROM unnest(?::STRUCT({self.schema})[]) AS t(r)",
            [records],
        )

    def _selected(self):
        relation = self.connection.table("report_rows")
        if self.where is not None:
            relation = relation.filter(self.where)
        # Explicit tie breaking also preserves source order for unsorted filters.
        order = (
            f"{self.order_by}\n, __ordinal"
            if self.order_by is not None
            else "__ordinal"
        )
        cursor = relation.order(order).project("__payload").execute()
        while batch := cursor.fetchmany(self.batch_size):
            for (payload,) in batch:
                yield json.loads(payload)

    def select(self, rows):
        numbered = enumerate(rows)
        while batch := list(islice(numbered, self.batch_size)):
            self._insert(batch)
            if self.order_by is None:
                yield from self._selected()
                self.connection.execute("DELETE FROM report_rows")
        if self.order_by is not None:
            yield from self._selected()
