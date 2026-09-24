import csv
import io
import json
import sys

import pytest

from jevotron.cli import main
from jevotron.config import load_config


@pytest.fixture
def input_file(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("id,value\na,ok\nb,BAD\nc,ok\n")
    return path


def test_preview_no_credentials_network_or_cache(
    input_file, monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    assert main(["preview", str(input_file), "--limit", "1"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 1 and rows[0]["fields"] == ["/id", "/value"]
    assert not (tmp_path / ".jevotron").exists()


def test_format_discovery_and_help(capsys):
    assert main(["formats"]) == 0
    output = capsys.readouterr().out
    assert "toml" in output and ".ndjson" in output and ".gz" in output
    assert main(["formats", "csv"]) == 0
    output = capsys.readouterr().out
    assert "delimiter=" in output and "encoding=" in output
    assert main(["formats", "unknown"]) == 1
    assert "Unknown format" in capsys.readouterr().err


def test_format_options_shared_by_preview_and_scan(tmp_path, fake, monkeypatch, capsys):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    path = tmp_path / "data.unknown"
    path.write_text("id;value\na;ok\nb;BAD\n")
    args = [
        str(path),
        "--format",
        "csv",
        "--format-option",
        "delimiter=;",
        "--field",
        "/value",
    ]
    assert main(["preview", *args]) == 0
    previews = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert previews[1]["request"]["state"]["entry"] == {"id": "b", "value": "BAD"}
    assert main(["scan", *args, "--no-cache"]) == 0
    capsys.readouterr()
    assert fake.requests == [p["request"] for p in previews]


@pytest.mark.parametrize(
    "options,message",
    [
        (["--format-option", "delimiter"], "KEY=VALUE"),
        (
            ["--format-option", "delimiter=;", "--format-option", "delimiter=,"],
            "Repeated",
        ),
        (["--format-option", "records=/items"], "Unknown option"),
        (["--format", "invented"], "Unknown format"),
    ],
)
def test_format_mistakes_fail_without_output(input_file, capsys, options, message):
    assert main(["preview", str(input_file), *options]) == 1
    result = capsys.readouterr()
    assert not result.out
    assert message in result.err


def test_custom_parser_flags_are_not_silently_ignored(input_file, tmp_path, capsys):
    config = tmp_path / "config.py"
    config.write_text(
        "from jevotron import Config\nfrom jevotron.parsers import CSV\nconfig=Config(parser=CSV())\n"
    )
    assert (
        main(["preview", str(input_file), "--config", str(config), "--format", "csv"])
        == 1
    )
    assert "custom config parser" in capsys.readouterr().err


def test_truncated_gzip_reports_error_without_traceback(tmp_path, capsys):
    import gzip

    path = tmp_path / "data.json.gz"
    path.write_bytes(gzip.compress(b'{"name":"item"}')[:-5])
    assert main(["preview", str(path)]) == 1
    output = capsys.readouterr()
    assert "Error:" in output.err
    assert not output.out


def test_scan_warnings_csv_then_cached_json(
    input_file, fake, monkeypatch, tmp_path, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    args = [
        "scan",
        str(input_file),
        "--warnings-only",
        "--sort-score",
        "--output-format",
        "csv",
    ]
    assert main(args) == 0
    output = capsys.readouterr()
    rows = list(csv.DictReader(io.StringIO(output.out)))
    assert len(rows) == 2 and {row["id"] for row in rows} == {"2"}
    assert "3 entries (0 cached)" in output.err
    assert "1 warnings" in output.err
    assert main(["scan", str(input_file)]) == 0
    output = capsys.readouterr()
    results = [json.loads(line) for line in output.out.splitlines()]
    assert all(r["cached"] for r in results)
    assert len(fake.requests) == 3
    assert "3 cached" in output.err


def test_local_config_custom_parser_and_relative_guidance(tmp_path, capsys):
    (tmp_path / "guide.md").write_text("Local guidance")
    config = tmp_path / "jev_config.py"
    config.write_text("""from pathlib import Path
from jevotron import Chunk, Config
def parse(path):
    for i, line in enumerate(path.read_text().splitlines()):
        yield Chunk(str(i), {"content": line}, source=str(path))
config = Config(parser=parse, guidance=(Path(__file__).parent / "guide.md").read_text())
""")
    source = tmp_path / "data.custom"
    source.write_text("one\ntwo\n")
    assert main(["preview", str(source), "--config", str(config)]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 2
    assert rows[0]["request"]["state"]["guidance"] == "Local guidance"


def test_config_supports_sibling_imports_and_restores_sys_path(tmp_path):
    (tmp_path / "jev_test_helper.py").write_text("GUIDANCE = 'helper text'\n")
    path = tmp_path / "config.py"
    path.write_text(
        "from jevotron import Config\nfrom jev_test_helper import GUIDANCE\nconfig = Config(guidance=GUIDANCE)\n"
    )
    before = sys.path[:]
    try:
        assert load_config(path).guidance == "helper text"
        assert sys.path == before
    finally:
        sys.modules.pop("jev_test_helper", None)


def test_limit_never_consumes_extra_entry(tmp_path, fake, monkeypatch, capsys):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    data = tmp_path / "data.csv"
    data.write_text("a,b\n1,2\ninvalid\n")
    assert main(["scan", str(data), "--limit", "1", "--no-cache"]) == 0
    assert len(fake.requests) == 1
    capsys.readouterr()


def test_failure_marks_partial_output_incomplete(tmp_path, fake, monkeypatch, capsys):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    data = tmp_path / "data.csv"
    data.write_text("a,b\n1,2\ninvalid\n")
    assert main(["scan", str(data), "--cache", str(tmp_path / "cache.db")]) == 1
    output = capsys.readouterr()
    assert len(output.out.splitlines()) == 1
    assert "INCOMPLETE: 1 entries assessed" in output.err


def test_output_cannot_overwrite_input(input_file, capsys):
    original = input_file.read_bytes()
    assert main(["preview", str(input_file), "-o", str(input_file)]) == 1
    assert input_file.read_bytes() == original
    assert "must not overwrite" in capsys.readouterr().err


def test_invalid_threshold_and_missing_api_key_fail(input_file, tmp_path, capsys):
    assert main(["scan", str(input_file), "--threshold", "nan", "--no-cache"]) == 1
    assert "threshold" in capsys.readouterr().err
    assert main(["scan", str(input_file), "--no-cache"]) == 1
    assert "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_rapid_config_edits_always_read_current_source(tmp_path):
    import os

    path = tmp_path / "config.py"
    path.write_text("from jevotron import Config\nconfig = Config(guidance='before')\n")
    stamp = path.stat()
    assert load_config(path).guidance == "before"
    path.write_text("from jevotron import Config\nconfig = Config(guidance='after!')\n")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert load_config(path).guidance == "after!"


def test_typer_help_and_invalid_arguments():
    from rich.text import Text
    from typer.testing import CliRunner

    from jevotron.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["scan", "--help"])
    assert result.exit_code == 0
    help_text = Text.from_ansi(result.output).plain
    assert "--field" in help_text and "--warnings-only" in help_text
    for args in (
        ["scan", "data.csv", "--limit", "0"],
        ["scan", "data.csv", "--output-format", "xml"],
        ["scan", "data.csv", "--typo"],
    ):
        assert runner.invoke(app, args).exit_code == 2


def test_cli_fields_and_ids_preserve_context(input_file, fake, monkeypatch, capsys):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    assert (
        main(
            [
                "scan",
                str(input_file),
                "--id-column",
                "id",
                "--field",
                "/value",
                "--no-cache",
            ]
        )
        == 0
    )
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in rows] == ["a", "b", "c"]
    assert [f["path"] for f in rows[0]["fields"]] == ["/value"]
    assert fake.requests[0]["state"]["entry"] == {"id": "a", "value": "ok"}


def test_repeated_cli_fields_override_config(input_file, tmp_path, capsys):
    config = tmp_path / "config.py"
    config.write_text(
        'from jevotron import Config\nfrom jevotron.parsers import CSV\nconfig = Config(parser=CSV(fields=["/id"]))\n'
    )
    assert (
        main(
            [
                "preview",
                str(input_file),
                "--config",
                str(config),
                "--field",
                "/id",
                "--field",
                "/value",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["fields"] == ["/id", "/value"]


def test_cli_missing_selected_field_fails_before_inference(
    input_file, fake, monkeypatch, capsys
):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    assert main(["scan", str(input_file), "--field", "/missing", "--no-cache"]) == 1
    assert not fake.requests
    assert "Field does not exist" in capsys.readouterr().err


def test_inline_and_file_guidance_share_request_identity(input_file, tmp_path, capsys):
    guidance = tmp_path / "rules.md"
    guidance.write_text("Quantity must be nonnegative.")
    assert (
        main(
            [
                "preview",
                str(input_file),
                "--guidance",
                guidance.read_text(),
                "--limit",
                "1",
            ]
        )
        == 0
    )
    inline = json.loads(capsys.readouterr().out)
    assert (
        main(
            [
                "preview",
                str(input_file),
                "--guidance-file",
                str(guidance),
                "--limit",
                "1",
            ]
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert inline["request"]["state"]["guidance"] == guidance.read_text()
    assert inline["request_hash"] == document["request_hash"]


def test_guidance_overrides_config_and_rejects_ambiguous_input(
    input_file, tmp_path, capsys
):
    config = tmp_path / "config.py"
    config.write_text(
        'from jevotron import Config\nconfig = Config(guidance="original")\n'
    )
    assert (
        main(
            [
                "preview",
                str(input_file),
                "--config",
                str(config),
                "--guidance",
                "rules.md",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    assert (
        json.loads(capsys.readouterr().out)["request"]["state"]["guidance"]
        == "rules.md"
    )
    assert (
        main(
            [
                "preview",
                str(input_file),
                "--guidance",
                "text",
                "--guidance-file",
                "missing.md",
            ]
        )
        == 1
    )
    assert "not both" in capsys.readouterr().err
    assert main(["preview", str(input_file), "--guidance-file", "missing.md"]) == 1
    assert "missing.md" in capsys.readouterr().err


def test_guidance_file_is_protected_from_output_overwrite(input_file, tmp_path, capsys):
    guidance = tmp_path / "rules.md"
    guidance.write_text("Preserve me")
    assert (
        main(
            [
                "preview",
                str(input_file),
                "--guidance-file",
                str(guidance),
                "-o",
                str(guidance),
            ]
        )
        == 1
    )
    assert guidance.read_text() == "Preserve me"
    assert "must not overwrite" in capsys.readouterr().err
