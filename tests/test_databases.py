"""Integration tests use real database files and an offline model evaluator."""

import gzip
import json
import sqlite3
from contextlib import closing

import duckdb
import pytest

from jevotron.cli import main
from jevotron.databases import Database, DuckDB, SQLite
from jevotron.parsers import for_path
from jevotron.runner import preview


@pytest.fixture(params=["sqlite", "duckdb"])
def database(request, tmp_path):
    engine = request.param
    path = tmp_path / f"input.{engine}"
    connect = sqlite3.connect if engine == "sqlite" else duckdb.connect
    with closing(connect(str(path))) as connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO items VALUES (2, 'BAD'), (1, 'ok')")
        connection.execute("CREATE TABLE other (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO other VALUES (1, 'other')")
        connection.execute("CREATE VIEW item_view AS SELECT * FROM items")
        connection.commit()
    return engine, path, connect


def records(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


@pytest.mark.parametrize("suffix", ["", ".db", ".csv", ".duckdb", ".sqlite3", ".gz"])
def test_header_wins_over_extension(database, suffix):
    engine, path, _ = database
    renamed = path.with_name("renamed" + suffix)
    path.rename(renamed)
    parser = for_path(renamed)
    assert isinstance(parser, Database) and parser.engine == engine
    chunks = list(parser(renamed))
    assert [c.data["value"] for c in chunks] == ["ok", "BAD", "other"]
    assert len({c.id for c in chunks}) == 3
    assert all(c.source.startswith(str(renamed)) for c in chunks)
    assert chunks[0].id == '"main"."items":key:[1]'
    assert chunks[2].id == '"main"."other":key:[1]'


def test_catalog_and_explicit_view_selection(database, capsys):
    _, path, _ = database
    original = path.read_bytes()
    assert main(["tables", str(path)]) == 0
    tables = records(capsys)
    assert {t["name"] for t in tables} == {"items", "other", "item_view"}
    item = next(t for t in tables if t["name"] == "items")
    assert item["columns"] == ["id", "value"] and item["primary_key"] == ["id"]
    assert (
        main(["preview", str(path), "--table", "item_view", "--field", "/value"]) == 0
    )
    rows = records(capsys)
    assert len(rows) == 2 and rows[0]["fields"] == ["/value"]
    assert all('"item_view":row:' in r["id"] for r in rows)
    assert path.read_bytes() == original


def test_preview_scan_and_cached_repeat(database, fake, monkeypatch, tmp_path, capsys):
    _, path, _ = database
    original = path.read_bytes()
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    args = [str(path), "--id-column", "id", "--field", "/value"]
    assert main(["preview", *args]) == 0
    previews = records(capsys)
    assert not (tmp_path / ".jevotron").exists()
    args.extend(["--cache", str(tmp_path / "cache.sqlite3")])
    assert main(["scan", *args]) == 0
    first = records(capsys)
    assert len(first) == 3 and len({r["id"] for r in first}) == 3
    assert sum(r["warning"] for r in first) == 1
    assert fake.requests == [r["request"] for r in previews]
    assert main(["scan", *args]) == 0
    assert all(r["cached"] for r in records(capsys))
    assert len(fake.requests) == 3
    assert path.read_bytes() == original


def test_limit_and_selection_close_connection(database, capsys):
    _, path, connect = database
    assert (
        main(
            [
                "preview",
                str(path),
                "--table",
                "other",
                "--table",
                "items",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    assert records(capsys)[0]["request"]["state"]["entry"]["value"] == "other"
    # DuckDB cannot open a read-write connection while our read-only handle lives;
    # SQLite cannot commit a write while our read transaction remains open.
    with closing(connect(str(path))) as connection:
        connection.execute("INSERT INTO items VALUES (3, 'after')")
        connection.commit()


def test_connections_are_read_only(database):
    engine, path, _ = database
    with Database(engine)._connect(path) as connection:
        with pytest.raises((sqlite3.Error, duckdb.Error)):
            connection.execute("DELETE FROM items")


@pytest.mark.parametrize("flag", ["--cache", "--output"])
@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
def test_input_cannot_be_overwritten(database, tmp_path, capsys, flag, alias):
    _, path, _ = database
    target = path
    if alias != "direct":
        target = tmp_path / "alias"
        if alias == "symlink":
            target.symlink_to(path)
        else:
            target.hardlink_to(path)
    original = path.read_bytes()
    assert main(["scan", str(path), flag, str(target)]) == 1
    assert "must not overwrite" in capsys.readouterr().err
    assert path.read_bytes() == original


def test_selection_validation_before_requests(database, capsys):
    _, path, _ = database
    assert main(["preview", str(path), "--table", "items", "--table", "missing"]) == 1
    output = capsys.readouterr()
    assert not output.out and "Unknown table" in output.err and "other" in output.err
    assert main(["preview", str(path), "--id-column", "missing"]) == 1
    output = capsys.readouterr()
    assert not output.out and "Missing identifier" in output.err


def test_quoted_names_composite_keys_and_nulls(database):
    engine, path, connect = database
    with closing(connect(str(path))) as connection:
        connection.execute(
            'CREATE TABLE "odd""; name" (a TEXT, b INTEGER, "a/b" TEXT, PRIMARY KEY (b, a))'
        )
        connection.execute(
            "INSERT INTO \"odd\"\"; name\" VALUES ('x', 2, NULL), ('x', 1, 'ok')"
        )
        connection.commit()
    parser = Database(engine, tables=['odd"; name'])
    table = next(t for t in parser.catalog(path) if t.name == 'odd"; name')
    assert table.primary_key == ("b", "a")
    chunks = list(Database(engine, tables=[table.selector, table.selector])(path))
    assert len(chunks) == 2
    assert chunks[0].id.endswith('key:[1,"x"]')
    assert chunks[1].data["a/b"] is None
    assert "/a~1b" in chunks[0].field_paths()


def test_duckdb_schemas(tmp_path):
    path = tmp_path / "schemas.db"
    with closing(duckdb.connect(str(path))) as connection:
        connection.execute("CREATE SCHEMA second")
        connection.execute("CREATE TABLE main.items AS SELECT 1 AS id")
        connection.execute("CREATE TABLE second.items AS SELECT 2 AS id")
    with pytest.raises(ValueError, match="Ambiguous"):
        list(DuckDB(tables=["items"])(path))
    assert list(DuckDB(tables=["second.items"])(path))[0].data == {"id": 2}
    assert len(list(preview(DuckDB()(path)))) == 2


def test_sqlite_nullable_primary_key_and_internal_tables(tmp_path):
    path = tmp_path / "nullable.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE nullable (id TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO nullable VALUES (NULL, 'one'), (NULL, 'two')")
        connection.execute("CREATE TABLE auto (id INTEGER PRIMARY KEY AUTOINCREMENT)")
        connection.execute("CREATE VIRTUAL TABLE search USING fts5(content)")
        connection.execute("INSERT INTO search VALUES ('searchable')")
        connection.commit()
    assert {t.name for t in SQLite().catalog(path)} == {"nullable", "auto", "search"}
    assert len(list(preview(SQLite()(path)))) == 3


def test_sqlite_reads_wal_and_uri_punctuation(tmp_path):
    path = tmp_path / "a ?#%.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("CREATE TABLE items (value TEXT)")
        connection.execute("INSERT INTO items VALUES ('uncheckpointed')")
        connection.commit()
        assert list(for_path(path)(path))[0].data == {"value": "uncheckpointed"}


def test_sqlite_blob_and_generated_columns(tmp_path):
    path = tmp_path / "blob.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE data (raw BLOB, size INTEGER GENERATED ALWAYS AS (length(raw)))"
        )
        connection.execute("INSERT INTO data(raw) VALUES (?)", (b"\x00\xff",))
        connection.commit()
    chunk = list(SQLite()(path))[0]
    assert chunk.data == {"raw": "hex:00ff", "size": 2}
    chunk.field_paths()


def test_duckdb_types_are_json_compatible(tmp_path):
    path = tmp_path / "types.duckdb"
    with closing(duckdb.connect(str(path))) as connection:
        connection.execute("""CREATE TABLE data AS SELECT
            12.340::DECIMAL(20,3) AS amount,
            DATE '2026-09-23' AS day,
            TIMESTAMP '2026-09-23 12:30:00' AS stamp,
            TIME '12:30:00' AS clock,
            UUID '12345678-1234-1234-1234-123456789abc' AS uuid,
            from_hex('00ff') AS raw,
            [1, 2, NULL] AS numbers,
            {'nested': 1.20::DECIMAL(10,2)} AS object,
            MAP([1, 2], ['one', 'two']) AS mapping,
            'NaN'::DOUBLE AS nan,
            'Infinity'::DOUBLE AS inf,
            INTERVAL '1 day' AS duration,
            TRUE AS active""")
    row = list(preview(DuckDB()(path)))[0]["request"]["state"]["entry"]
    assert row == {
        "amount": "12.340",
        "day": "2026-09-23",
        "stamp": "2026-09-23T12:30:00",
        "clock": "12:30:00",
        "uuid": "12345678-1234-1234-1234-123456789abc",
        "raw": "hex:00ff",
        "numbers": [1, 2, None],
        "object": {"nested": "1.20"},
        "mapping": [{"key": 1, "value": "one"}, {"key": 2, "value": "two"}],
        "nan": "nan",
        "inf": "inf",
        "duration": "1 day, 0:00:00",
        "active": True,
    }


@pytest.mark.parametrize("engine", ["sqlite", "duckdb"])
def test_missing_corrupt_empty_and_compressed_database(engine, tmp_path, capsys):
    path = tmp_path / f"input.{engine}"
    assert main(["preview", str(path)]) == 1
    assert "does not exist" in capsys.readouterr().err
    assert not path.exists()
    path.write_bytes(b"not a database")
    assert main(["preview", str(path)]) == 1
    assert "Error:" in capsys.readouterr().err
    path.unlink()
    connect = sqlite3.connect if engine == "sqlite" else duckdb.connect
    with closing(connect(str(path))) as connection:
        connection.execute("CREATE TABLE empty (id INTEGER)")
        connection.commit()
    assert main(["preview", str(path)]) == 0
    assert not capsys.readouterr().out
    compressed = path.with_suffix(path.suffix + ".gz")
    compressed.write_bytes(gzip.compress(path.read_bytes()))
    assert main(["preview", str(compressed)]) == 1
    assert "decompress" in capsys.readouterr().err
    with closing(connect(str(path))) as connection:
        connection.execute("DROP TABLE empty")
        connection.commit()
    assert main(["preview", str(path)]) == 1
    assert "no user tables" in capsys.readouterr().err


def test_database_options_not_ignored_for_text(tmp_path, capsys):
    path = tmp_path / "data.csv"
    path.write_text("id,value\n1,ok\n")
    assert main(["preview", str(path), "--table", "items"]) == 1
    assert "--table requires" in capsys.readouterr().err
    assert main(["tables", str(path)]) == 1
    assert "requires" in capsys.readouterr().err
    assert main(["formats", "duckdb"]) == 0
    output = capsys.readouterr().out
    assert "--table" in output


def test_database_encoding_rejected(database, capsys):
    _, path, _ = database
    assert main(["preview", str(path), "--format-option", "encoding=utf-8"]) == 1
    assert "Unknown option" in capsys.readouterr().err


def test_changed_row_only_is_reassessed(database, fake, monkeypatch, tmp_path, capsys):
    _, path, connect = database
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    args = ["scan", str(path), "--cache", str(tmp_path / "cache.db")]
    assert main(args) == 0
    before = records(capsys)
    with closing(connect(str(path))) as connection:
        connection.execute("UPDATE items SET value = 'fixed' WHERE id = 2")
        connection.commit()
    assert main(args) == 0
    after = records(capsys)
    assert len(fake.requests) == 4
    assert [row["cached"] for row in after] == [True, False, True]
    assert [row["id"] for row in before] == [row["id"] for row in after]


def test_error_closes_database_connection(database, capsys):
    _, path, connect = database
    with closing(connect(str(path))) as connection:
        connection.execute("UPDATE items SET value = 'same'")
        connection.commit()
    assert main(["preview", str(path), "--id-column", "value"]) == 1
    assert "Duplicate chunk id" in capsys.readouterr().err
    with closing(connect(str(path))) as connection:
        connection.execute("INSERT INTO items VALUES (3, 'new')")
        connection.commit()


def test_database_config_and_cli_override(database, tmp_path, capsys):
    engine, path, _ = database
    config = tmp_path / "config.py"
    factory = "SQLite" if engine == "sqlite" else "DuckDB"
    config.write_text(
        f"from jevotron import Config\nfrom jevotron.databases import {factory}\n"
        f"config = Config(parser={factory}(tables=['other'], fields=['/value']))\n"
    )
    args = ["preview", str(path), "--config", str(config)]
    assert main([*args, "--id-column", "id"]) == 0
    (row,) = records(capsys)
    assert row["id"] == '"main"."other":key:[1]'
    assert row["fields"] == ["/value"]
    assert main([*args, "--table", "items"]) == 1
    assert "custom config parser" in capsys.readouterr().err


@pytest.mark.parametrize("old_id", [None, ""])
def test_database_id_override_precedes_validation(
    database, tmp_path, monkeypatch, capsys, old_id
):
    engine, path, connect = database
    if old_id == "":
        with closing(connect(str(path))) as connection:
            connection.execute("ALTER TABLE items ADD COLUMN old_id TEXT DEFAULT ''")
            connection.execute("ALTER TABLE other ADD COLUMN old_id TEXT DEFAULT ''")
            connection.commit()
    from jevotron import Config

    parser = Database(engine, id_column="old_id")
    config = Config(parser=parser)
    monkeypatch.setattr("jevotron.cli.load_config", lambda _: config)
    args = ["preview", str(path), "--config", str(tmp_path / "config.py")]
    assert main([*args, "--id-column", "id"]) == 0
    rows = records(capsys)
    assert [row["id"] for row in rows] == [
        '"main"."items":key:[1]',
        '"main"."items":key:[2]',
        '"main"."other":key:[1]',
    ]
    assert parser.id_column == "old_id"
    assert main(args) == 1
    assert "identifier" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("expression", ["[1, 2]", "{'nested': 1}", "NULL", "''"])
def test_duckdb_explicit_id_rejects_nonscalar_or_empty(tmp_path, capsys, expression):
    path = tmp_path / "invalid.duckdb"
    with closing(duckdb.connect(str(path))) as connection:
        connection.execute(f"CREATE TABLE items AS SELECT {expression} AS id")
    with pytest.raises(ValueError, match="nonempty scalar"):
        list(DuckDB(id_column="id")(path))
    assert main(["preview", str(path), "--id-column", "id"]) == 1
    assert not capsys.readouterr().out


@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
def test_preview_output_preserves_database(database, tmp_path, capsys, alias):
    _, path, _ = database
    target = path
    if alias != "direct":
        target = tmp_path / "report.jsonl"
        if alias == "symlink":
            target.symlink_to(path)
        else:
            target.hardlink_to(path)
    original = path.read_bytes()
    assert main(["preview", str(path), "--output", str(target)]) == 1
    assert "Output must not overwrite" in capsys.readouterr().err
    assert path.read_bytes() == original
