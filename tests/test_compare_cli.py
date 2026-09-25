import csv
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from jevotron.cli import main
from jevotron.client import JevError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def pair(tmp_path):
    baseline, candidate = tmp_path / "before.csv", tmp_path / "after.csv"
    baseline.write_text("id,value\na,ok\nb,BAD\n")
    candidate.write_text("id,value\nb,BAD\na,BAD\n")
    return baseline, candidate


def test_cli_reports_policy_status_and_both_formats(
    pair, tmp_path, monkeypatch, fake, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    report, markdown = tmp_path / "changes.jsonl", tmp_path / "changes.md"
    cache = tmp_path / "cache.db"
    args = ["compare", *map(str, pair), "--id-column", "id", "--cache", str(cache)]
    assert (
        main(
            [
                *args,
                "--fail-on",
                "new-warning",
                "-o",
                str(report),
                "--markdown",
                str(markdown),
            ]
        )
        == 3
    )
    rows = [json.loads(line) for line in report.read_text().splitlines()]
    assert rows[0]["type"] == "comparison" and rows[0]["complete"]
    assert rows[0]["policy_violated"] and rows[0]["policy"] == "new-warning"
    assert rows[0]["summary"]["field_warnings"] == {
        "new": 1,
        "persistent": 1,
        "resolved": 0,
    }
    assert "Policy violation" in markdown.read_text()
    assert "Score before → after" in markdown.read_text()
    assert "1 changed" in capsys.readouterr().err

    # These are entirely cached, with no environment key and no new evaluator.
    monkeypatch.setattr(
        "jevotron.runner.JevClient", lambda: pytest.fail("network used")
    )
    assert main([*args, "--threshold", "0.95", "--fail-on", "new-warning"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["summary"]["cached_requests"] == 3
    assert main([*args, "--fail-on", "unknown"]) == 2


@pytest.mark.parametrize(
    "case",
    ["missing-option", "missing-field", "empty-id", "duplicate-id", "collection-id"],
)
def test_invalid_stable_ids_fail_before_network(
    pair, tmp_path, monkeypatch, fake, capsys, case
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    before, after = pair
    options = [] if case == "missing-option" else ["--id-column", "id"]
    if case == "missing-field":
        after.write_text("other,value\na,ok\n")
    elif case == "empty-id":
        after.write_text("id,value\n,ok\n")
    elif case == "duplicate-id":
        after.write_text("id,value\na,ok\na,BAD\n")
    elif case == "collection-id":
        after = tmp_path / "after.json"
        after.write_text('[{"id":["a"],"value":"ok"}]')
    assert main(["compare", str(before), str(after), *options, "--no-cache"]) == 1
    output = capsys.readouterr()
    assert "INCOMPLETE" in output.err and not output.out
    assert not fake.requests


def test_configured_builtin_stable_ids_and_field_selection(
    pair, tmp_path, monkeypatch, fake, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    config = tmp_path / "config.py"
    config.write_text(
        "from jevotron import Config\nfrom jevotron.parsers import CSV\nconfig=Config(parser=CSV(id_column='id', fields=['/value']))\n"
    )
    assert (
        main(["compare", *map(str, pair), "--config", str(config), "--no-cache"]) == 0
    )
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[1]["fields"][0]["path"] == "/value"
    assert len(rows[1]["fields"]) == 1
    assert rows[0]["inputs"]["baseline"]["id_column"] == "id"


def test_custom_parser_requires_explicit_stable_id_assertion(
    pair, tmp_path, monkeypatch, fake, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    config = tmp_path / "config.py"
    config.write_text(
        "from jevotron import Chunk, Config\ndef parse(path):\n    yield Chunk('stable', {'value':'ok'})\nconfig=Config(parser=parse)\n"
    )
    args = ["compare", *map(str, pair), "--config", str(config), "--no-cache"]
    assert main(args) == 1
    assert "--parser-ids" in capsys.readouterr().err
    assert main([*args, "--parser-ids"]) == 0
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "kind,content",
    [
        ("CSV", "current_id,value\nx,ok\n"),
        ("JSON", '[{"current_id":"x","value":"ok"}]'),
    ],
)
def test_explicit_id_overrides_configured_parser_before_iteration(
    kind, content, tmp_path, monkeypatch, fake, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    path = tmp_path / "input"
    path.write_text(content)
    config = tmp_path / "config.py"
    config.write_text(
        f"from jevotron import Config\nfrom jevotron.parsers import {kind}\nconfig=Config(parser={kind}(id_column='old_id'))\n"
    )
    assert (
        main(
            [
                "compare",
                str(path),
                str(path),
                "--config",
                str(config),
                "--id-column",
                "current_id",
                "--no-cache",
            ]
        )
        == 0
    )
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[1]["id"] == "x"
    assert len(fake.requests) == 1


@pytest.mark.parametrize(
    "format,content",
    [
        ("fasta", ">seq1\nACGT\n"),
        ("gmt", "set1\tdescription\ta\tb\n"),
        ("obo", "[Term]\nid: X:1\nname: entry\n"),
    ],
)
def test_natural_parser_ids_are_supported(
    format, content, tmp_path, monkeypatch, fake, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    path = tmp_path / f"input.{format}"
    path.write_text(content)
    assert main(["compare", str(path), str(path), "--no-cache"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[1]["change"] == "unchanged"


def test_obo_missing_natural_id_is_rejected(tmp_path, capsys):
    path = tmp_path / "input.obo"
    path.write_text("[Term]\nname: entry\n")
    assert main(["compare", str(path), str(path), "--no-cache"]) == 1
    assert "exactly one nonempty id" in capsys.readouterr().err


def test_database_namespaces_preserve_equal_keys_across_tables(
    tmp_path, monkeypatch, fake, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    paths = [tmp_path / "before.db", tmp_path / "after.db"]
    for path in paths:
        with sqlite3.connect(path) as db:
            for table in ("one", "two"):
                db.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, value TEXT)")
                db.execute(f"INSERT INTO {table} VALUES ('abc:row:x', 'ok')")
    assert main(["compare", *map(str, paths), "--id-column", "id", "--no-cache"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len({row["id"] for row in rows[1:]}) == 2
    assert rows[0]["summary"]["entries"]["unchanged"] == 2
    assert all(':key:["abc:row:x"]' in row["id"] for row in rows[1:])


@pytest.mark.parametrize(
    "schema,values",
    [
        ("id TEXT, value TEXT", "'x','ok'"),
        ("id TEXT PRIMARY KEY, value TEXT", "NULL,'ok'"),
    ],
)
def test_database_row_fallback_is_rejected(tmp_path, capsys, schema, values):
    path = tmp_path / "input.db"
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE entries ({schema})")
        db.execute(f"INSERT INTO entries VALUES ({values})")
    assert main(["compare", str(path), str(path), "--no-cache"]) == 1
    assert "no stable key" in capsys.readouterr().err


@pytest.mark.parametrize("alias", ["same", "symlink", "hardlink"])
@pytest.mark.parametrize(
    "protected", ["baseline", "candidate", "config", "guidance", "exemplars", "cache"]
)
def test_outputs_cannot_alias_any_protected_input(
    pair, tmp_path, capsys, alias, protected
):
    before, after = pair
    files = {"baseline": before, "candidate": after}
    for kind in ("config", "guidance", "exemplars", "cache"):
        files[kind] = tmp_path / f"{kind}.txt"
        files[kind].write_text("must remain unchanged")
    source = files[protected]
    output = source if alias == "same" else tmp_path / "report.jsonl"
    if alias == "symlink":
        output.symlink_to(source)
    elif alias == "hardlink":
        os.link(source, output)
    args = [
        "compare",
        str(before),
        str(after),
        "--id-column",
        "id",
        "--config",
        str(files["config"]),
        "--guidance-file",
        str(files["guidance"]),
        "--exemplars",
        str(files["exemplars"]),
        "--cache",
        str(files["cache"]),
        "--markdown",
        str(output),
    ]
    original = source.read_bytes()
    assert main(args) == 1
    assert source.read_bytes() == original
    assert "must not overwrite" in capsys.readouterr().err


def test_cache_and_report_output_aliases_are_rejected(pair, tmp_path, capsys):
    before, after = pair
    original = before.read_bytes()
    assert (
        main(
            [
                "compare",
                str(before),
                str(after),
                "--id-column",
                "id",
                "--cache",
                str(before),
            ]
        )
        == 1
    )
    assert before.read_bytes() == original
    capsys.readouterr()
    output = tmp_path / "output"
    assert (
        main(
            [
                "compare",
                *map(str, pair),
                "--id-column",
                "id",
                "-o",
                str(output),
                "--markdown",
                str(output),
            ]
        )
        == 1
    )
    assert "must be different" in capsys.readouterr().err


def test_api_failure_keeps_previous_reports_and_emits_no_stdout(
    pair, tmp_path, monkeypatch, capsys
):
    class Failed:
        def evaluate(self, request):
            raise JevError("offline failure")

        def close(self):
            pass

    monkeypatch.setattr("jevotron.runner.JevClient", Failed)
    report = tmp_path / "report.jsonl"
    report.write_text("previous complete report")
    args = ["compare", *map(str, pair), "--id-column", "id", "--no-cache"]
    assert main([*args, "-o", str(report)]) == 1
    assert report.read_text() == "previous complete report"
    assert main(args) == 1
    output = capsys.readouterr()
    assert not output.out and "INCOMPLETE" in output.err


def test_output_directory_is_rejected_before_inference_or_primary_write(
    pair, tmp_path, monkeypatch, fake, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    report = tmp_path / "report.jsonl"
    report.write_text("previous report")
    directory = tmp_path / "markdown"
    directory.mkdir()
    assert (
        main(
            [
                "compare",
                *map(str, pair),
                "--id-column",
                "id",
                "--no-cache",
                "-o",
                str(report),
                "--markdown",
                str(directory),
            ]
        )
        == 1
    )
    assert report.read_text() == "previous report"
    assert fake.requests == []
    assert "not a directory" in capsys.readouterr().err


def test_airport_fixture_reordering_and_cache_only_replay(tmp_path, capsys):
    cache = tmp_path / "fixture.db"
    subprocess.run(
        [sys.executable, str(ROOT / "examples/comparison/seed_cache.py"), str(cache)],
        check=True,
    )
    airport = ROOT / "examples/airports"
    paths = []
    for name in ("baseline", "spiked"):
        with (airport / f"{name}.csv").open() as stream:
            reader = csv.DictReader(stream)
            rows, names = list(reader), reader.fieldnames
        path = tmp_path / f"{name}.csv"
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=names)
            writer.writeheader()
            writer.writerows(
                reversed(rows) if name == "baseline" else rows[3:] + rows[:3]
            )
        paths.append(path)
    args = [
        "compare",
        *map(str, paths),
        "--id-column",
        "ident",
        "--field",
        "/iso_country",
        "--guidance-file",
        str(airport / "guidance.md"),
        "--model",
        "jev-offline-fixture",
        "--cache",
        str(cache),
        "--fail-on",
        "new-warning",
    ]
    assert main(args) == 3
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows[0]["summary"] == {
        "entries": {"added": 0, "removed": 0, "changed": 2, "unchanged": 6},
        "field_warnings": {"new": 2, "resolved": 0, "persistent": 0},
        "unique_requests": 10,
        "cached_requests": 10,
    }
    warnings = [
        (row["id"], field["path"])
        for row in rows[1:]
        for field in row["fields"]
        if field["warning"] == "new"
    ]
    assert warnings == [("EGLL", "/iso_country"), ("KJFK", "/iso_country")]
    assert main([*args, "--output-format", "markdown"]) == 3
    output = capsys.readouterr().out
    assert "EGLL" in output and "KJFK" in output and "6 unchanged" in output
