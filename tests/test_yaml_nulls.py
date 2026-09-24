import json

import pytest

from jevotron.cli import main
from jevotron.parsers import JSON, YAML
from jevotron.runner import preview


@pytest.mark.parametrize("text", ["null", "Null", "NULL", "~", "!!null ''", "!!null"])
def test_explicit_null_documents_are_entries(tmp_path, text):
    path = tmp_path / "records.yaml"
    path.write_text(text + "\n")
    (row,) = preview(YAML()(path))
    assert row["id"] == "1:1"
    assert row["source"] == f"{path}:document:1:entry:1"
    assert row["fields"] == [""]
    assert row["request"]["state"]["entry"] is None


@pytest.mark.parametrize("text", ["", "# comment\n", "---\n", "---\n# comment\n---\n"])
def test_absent_documents_still_skipped(tmp_path, text):
    path = tmp_path / "records.yaml"
    path.write_text(text)
    assert list(YAML()(path)) == []


def test_empty_and_null_documents_preserve_document_numbers(tmp_path):
    path = tmp_path / "records.yaml"
    path.write_text(
        "---\n# empty first document\n---\nnull\n---\n\n"
        "---\nvalue: present\n---\n~\n---\n# empty final document\n"
    )
    chunks = list(YAML()(path))
    assert [c.id for c in chunks] == ["2:1", "4:1", "5:1"]
    assert [c.source for c in chunks] == [
        f"{path}:document:{i}:entry:1" for i in (2, 4, 5)
    ]
    assert [c.data for c in chunks] == [None, {"value": "present"}, None]


def test_yaml_and_json_null_entries_have_matching_requests(tmp_path):
    yaml_path = tmp_path / "records.yaml"
    yaml_path.write_text("---\nnull\n---\nvalue: present\n---\n~\n")
    json_path = tmp_path / "records.json"
    json_path.write_text('[null, {"value": "present"}, null]')
    yaml_requests = [r["request"] for r in preview(YAML()(yaml_path))]
    json_requests = [r["request"] for r in preview(JSON()(json_path))]
    assert yaml_requests == json_requests
    assert len(yaml_requests) == 3


def test_quoted_empty_string_remains_a_scalar_entry(tmp_path):
    path = tmp_path / "records.yaml"
    path.write_text("''\n")
    (chunk,) = YAML()(path)
    assert chunk.data == ""


def test_records_selection_is_applied_to_explicit_null(tmp_path):
    path = tmp_path / "records.yaml"
    path.write_text("---\n# skipped\n---\nnull\n")
    with pytest.raises(ValueError, match="Field does not exist"):
        list(YAML(records="/items")(path))


def test_preview_and_scan_include_null_documents(tmp_path, capsys, monkeypatch, fake):
    path = tmp_path / "records.yaml"
    path.write_text("null\n---\nvalue: present\n---\n~\n")
    assert main(["preview", str(path)]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["id"] for row in rows] == ["1:1", "2:1", "3:1"]
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    assert main(["scan", str(path), "--no-cache"]) == 0
    assert fake.requests == [row["request"] for row in rows]
