"""ID selection is consistent for file adapters, databases, and custom parsers."""

import csv
import json

import pytest

from jevotron import Chunk, Config
from jevotron.cli import main
from jevotron.config import load_config
from jevotron.parsers import CSV, JSON, JSONL, TOML, YAML

ADAPTERS = {"csv": CSV, "json": JSON, "jsonl": JSONL, "yaml": YAML, "toml": TOML}


def write_record(path, record):
    if path.suffix == ".csv":
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=record)
            writer.writeheader()
            writer.writerow(record)
    elif path.suffix == ".toml":
        path.write_text(
            "\n".join(f"{key} = {json.dumps(value)}" for key, value in record.items())
        )
    else:
        # A JSON object is also a valid YAML document and JSONL record.
        path.write_text(json.dumps(record) + "\n")


@pytest.mark.parametrize("format", ADAPTERS)
@pytest.mark.parametrize("old_id", [None, "", "previous-id"])
@pytest.mark.parametrize("command", ["preview", "scan"])
def test_override_precedes_builtin_validation(
    tmp_path, monkeypatch, capsys, fake, format, old_id, command
):
    record = {"current_id": "current-id", "value": "ok"}
    if old_id is not None:
        record["old_id"] = old_id
    path = tmp_path / f"input.{format}"
    write_record(path, record)
    config_path = tmp_path / "config.py"
    config_path.write_text(
        f"from jevotron import Config\nfrom jevotron.parsers import {ADAPTERS[format].__name__}\n"
        f"config = Config(parser={ADAPTERS[format].__name__}(id_column='old_id'))\n"
    )
    config = load_config(config_path)
    monkeypatch.setattr("jevotron.cli.load_config", lambda _: config)
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    args = [command, str(path), "--config", str(config_path)]
    if command == "scan":
        args.append("--no-cache")
    assert main([*args, "--id-column", "current_id"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["id"] == "current-id"
    assert config.parser.id_column == "old_id"
    # Reusing the same config must retain its original ID behavior.
    assert main(args) == (0 if old_id else 1)
    result = capsys.readouterr()
    if old_id:
        assert json.loads(result.out)["id"] == "previous-id"
    else:
        assert not result.out and "identifier" in result.err.lower()


@pytest.mark.parametrize("format", ["json", "jsonl", "yaml", "toml"])
@pytest.mark.parametrize("value", [[], ["a"], {"nested": "a"}, ""])
def test_structured_ids_reject_nonscalar_and_empty_values(
    tmp_path, capsys, format, value
):
    path = tmp_path / f"input.{format}"
    if format == "toml" and isinstance(value, dict):
        path.write_text('id = { nested = "a" }\n')
    else:
        write_record(path, {"id": value})
    with pytest.raises(ValueError, match="(scalar|identifier)"):
        list(ADAPTERS[format](id_column="id")(path))
    assert main(["preview", str(path), "--id-column", "id"]) == 1
    assert not capsys.readouterr().out


@pytest.mark.parametrize("format", ["json", "jsonl", "yaml", "toml"])
@pytest.mark.parametrize("value", [0, False, 1.5, "0001"])
def test_scalar_ids_preserve_zero_false_and_strings(tmp_path, capsys, format, value):
    path = tmp_path / f"input.{format}"
    write_record(path, {"id": value})
    assert list(ADAPTERS[format](id_column="id")(path))[0].id == str(value)
    assert main(["preview", str(path), "--id-column", "id"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == str(value)


@pytest.mark.parametrize("format", ["json", "jsonl", "yaml"])
def test_null_ids_rejected_by_parser_and_cli(tmp_path, capsys, format):
    path = tmp_path / f"input.{format}"
    write_record(path, {"id": None})
    with pytest.raises(ValueError, match="identifier"):
        list(ADAPTERS[format](id_column="id")(path))
    assert main(["preview", str(path), "--id-column", "id"]) == 1
    assert not capsys.readouterr().out


def test_custom_parser_id_is_overridden_after_yield(tmp_path, monkeypatch, capsys):
    config = Config(parser=lambda _: [Chunk("original", {"id": "chosen"})])
    monkeypatch.setattr("jevotron.cli.load_config", lambda _: config)
    args = [
        "preview",
        str(tmp_path / "input.custom"),
        "--config",
        str(tmp_path / "config.py"),
    ]
    assert main([*args, "--id-column", "id"]) == 0
    assert json.loads(capsys.readouterr().out)["id"] == "chosen"
    config.parser = lambda _: [Chunk("original", {"id": ["invalid"]})]
    assert main([*args, "--id-column", "id"]) == 1
    assert "nonempty scalar" in capsys.readouterr().err
