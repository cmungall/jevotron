"""Create equivalent synthetic SQLite and DuckDB examples in a new directory.

Usage: uv run python examples/databases/create.py /tmp/jt-database-demo
"""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

import duckdb


def create(folder: Path):
    folder.mkdir(parents=True, exist_ok=False)
    for engine, connect in (("sqlite", sqlite3.connect), ("duckdb", duckdb.connect)):
        path = folder / f"inventory.{engine}"
        with closing(connect(str(path))) as connection:
            connection.execute(
                "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price DOUBLE)"
            )
            connection.execute(
                "INSERT INTO products VALUES (1, 'Notebook', 4.50), "
                "(2, 'Pencill', 1.25), (3, 'Stapler', -9.99)"
            )
            connection.execute(
                "CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT)"
            )
            connection.execute("INSERT INTO suppliers VALUES (1, 'Example Stationery')")
            connection.execute(
                "CREATE VIEW product_names AS SELECT id, name FROM products"
            )
            connection.commit()
        print(path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", type=Path, help="New directory for the example files"
    )
    create(parser.parse_args().directory)
