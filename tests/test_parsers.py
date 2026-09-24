import gzip
import json

import pytest

from jevotron import Chunk
from jevotron.models import resolve
from jevotron.parsers import (
    CSV,
    FASTA,
    GMT,
    JSON,
    JSONL,
    OBO,
    TOML,
    YAML,
    Text,
    for_path,
)


def test_csv_preserves_strings_quotes_unicode_and_empty_cells(tmp_path):
    path = tmp_path / "airports.csv"
    path.write_text(
        '\ufeffid,name,note,code\n01,"Paris, France","line one\nline two",\n02,Zürich,ok,001\n'
    )
    chunks = list(CSV(id_column="id")(path))
    assert [c.id for c in chunks] == ["01", "02"]
    assert chunks[0].data == {
        "id": "01",
        "name": "Paris, France",
        "note": "line one\nline two",
        "code": "",
    }
    assert chunks[1].data["code"] == "001"
    assert chunks[1].source.endswith(":row:2")


@pytest.mark.parametrize(
    "text", ["a,a\n1,2\n", "a,\n1,2\n", "a,b\n1\n", "a,b\n1,2,3\n", ""]
)
def test_csv_rejects_lossy_shapes(tmp_path, text):
    path = tmp_path / "bad.csv"
    path.write_text(text)
    with pytest.raises(ValueError):
        list(CSV()(path))


def test_yaml_lists_documents_dates_and_top_level_fields(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text(
        "- id: a\n  birthday: 1900-01-02\n  terms:\n    - start: 1920-01-01\n---\nid: b\nname: Other\n"
    )
    chunks = list(YAML(id_column="id")(path))
    assert [c.id for c in chunks] == ["a", "b"]
    assert chunks[0].field_paths() == ["/id", "/birthday", "/terms"]
    assert chunks[0].data["birthday"] == "1900-01-02"
    assert chunks[0].data["terms"][0]["start"] == "1920-01-01"


def test_yaml_single_mapping_is_one_chunk(tmp_path):
    path = tmp_path / "record.yml"
    path.write_text("name: One\nvalue: 2\n")
    chunks = list(for_path(path)(path))
    assert len(chunks) == 1
    assert chunks[0].field_paths() == ["/name", "/value"]


@pytest.mark.parametrize("text", ["name: a\nname: b\n", "12: value\n"])
def test_yaml_rejects_lost_keys(tmp_path, text):
    path = tmp_path / "bad.yaml"
    path.write_text(text)
    with pytest.raises(ValueError):
        list(YAML()(path))


def test_obo_preserves_repeated_tags_raw_values_and_locations(tmp_path):
    path = tmp_path / "units.obo"
    path.write_text(
        "format-version: 1.2\n\n[Term]\nid: U:1\nname: metre\n"
        'synonym: "meter" EXACT []\nsynonym: "m" EXACT []\n'
        'def: "A unit of \\\nlength." []\nis_a: U:0 ! unit\n\n'
        "[Typedef]\nid: part_of\nname: part of\n"
    )
    first, second = list(OBO()(path))
    assert first.id == "U:1"
    assert first.source.endswith(":3")
    assert first.data["synonym"] == ['"meter" EXACT []', '"m" EXACT []']
    assert first.data["def"] == ['"A unit of length." []']
    assert first.data["is_a"] == ["U:0 ! unit"]
    assert "/synonym/0" in first.field_paths()
    assert "/synonym/1" in first.field_paths()
    assert "/_stanza" not in first.field_paths()
    assert second.data["_stanza"] == "Typedef"


@pytest.mark.parametrize(
    "adapter,suffix,text",
    [
        (JSON(), ".json", '[{"name":"a"},{"name":"b"}]'),
        (JSONL(), ".jsonl", '{"name":"a"}\n\n{"name":"b"}\n'),
    ],
)
def test_json_formats(tmp_path, adapter, suffix, text):
    path = tmp_path / ("records" + suffix)
    path.write_text(text)
    assert [c.data["name"] for c in adapter(path)] == ["a", "b"]


def test_json_pointer_escaping_and_nested_selection():
    chunk = Chunk(
        "x", {"a/b": {"~name": ["first", "second"]}}, fields=["/a~1b/~0name/1"]
    )
    assert chunk.field_paths() == ["/a~1b/~0name/1"]
    assert resolve(chunk.data, chunk.fields[0]) == "second"
    assert Chunk("x", {"": 1, "a/b": 2}).field_paths() == ["/", "/a~1b"]


@pytest.mark.parametrize(
    "path", ["name", "/missing", "/a/-1", "/a/01", "/a/2", "/a/~2"]
)
def test_invalid_field_paths_fail_before_inference(path):
    with pytest.raises(ValueError):
        Chunk("x", {"a": [1, 2]}, fields=[path]).field_paths()


@pytest.mark.parametrize(
    "data", [{1: "value"}, {"value": float("nan")}, {"value": {1, 2}}]
)
def test_non_json_data_rejected(data):
    with pytest.raises((ValueError, TypeError)):
        Chunk("x", data).field_paths()


def test_example_files_parse():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for filename in (
        "examples/airports/baseline.csv",
        "examples/airports/spiked.csv",
        "examples/inventory/items.yaml",
        "examples/units/units.obo",
    ):
        path = root / filename
        chunks = list(for_path(path)(path))
        assert chunks
        for chunk in chunks:
            chunk.field_paths()
            json.dumps(chunk.data, allow_nan=False)


@pytest.mark.parametrize("adapter", [JSON(), JSONL()])
def test_json_duplicate_keys_do_not_silently_disappear(tmp_path, adapter):
    path = tmp_path / "data.json"
    path.write_text('{"name":"a","name":"b"}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        list(adapter(path))


def test_yaml_merge_override_is_valid(tmp_path):
    path = tmp_path / "data.yaml"
    path.write_text("- &base\n  id: first\n  value: 1\n- <<: *base\n  id: second\n")
    chunks = list(YAML(id_column="id")(path))
    assert chunks[1].data == {"id": "second", "value": 1}


def test_unfinished_obo_continuation_rejected(tmp_path):
    path = tmp_path / "data.obo"
    path.write_text("[Term]\nid: X:1\n\\")
    with pytest.raises(ValueError, match="Unfinished OBO"):
        list(OBO()(path))


@pytest.mark.parametrize(
    "suffix,content",
    [
        (".json", '{"results":[{"sku":"001"},{"sku":"002"}]}'),
        (".yaml", 'results:\n  - sku: "001"\n  - sku: "002"\n'),
        (".toml", '[[results]]\nsku="001"\n[[results]]\nsku="002"\n'),
    ],
)
def test_nested_records_selection(tmp_path, suffix, content):
    path = tmp_path / ("data" + suffix)
    path.write_text(content)
    assert list(for_path(path)(path))[0].field_paths() == ["/results"]
    chunks = list(for_path(path, options={"records": "/results"})(path))
    assert [c.data for c in chunks] == [{"sku": "001"}, {"sku": "002"}]
    assert [c.field_paths() for c in chunks] == [["/sku"], ["/sku"]]
    with pytest.raises(ValueError, match="does not exist"):
        list(for_path(path, options={"records": "/missing"})(path))


def test_yaml_records_applies_per_document_and_escaped_pointer(tmp_path):
    path = tmp_path / "records.yamll"
    path.write_text("a/b:\n  - id: one\n---\na/b:\n  - id: two\n")
    chunks = list(for_path(path, options={"records": "/a~1b"})(path))
    assert [c.data for c in chunks] == [{"id": "one"}, {"id": "two"}]
    assert [c.id for c in chunks] == ["1:1", "2:1"]


def test_toml_typed_values_dates_and_arrays(tmp_path):
    path = tmp_path / "service.toml"
    path.write_text("""enabled = true
port = 443
ratio = 0.25
date = 2026-09-23
time = 09:30:00
stamp = 2026-09-23T09:30:00Z
[service]
names = ["a", "b"]
""")
    (chunk,) = TOML()(path)
    assert chunk.data == {
        "enabled": True,
        "port": 443,
        "ratio": 0.25,
        "date": "2026-09-23",
        "time": "09:30:00",
        "stamp": "2026-09-23T09:30:00+00:00",
        "service": {"names": ["a", "b"]},
    }
    chunk.field_paths()


@pytest.mark.parametrize(
    "split,texts,ids",
    [
        ("paragraphs", ["  First.\nContinued.", "Second."], ["2", "5"]),
        ("lines", ["  First.", "Continued.", "Second."], ["2", "3", "5"]),
        ("file", ["\n  First.\nContinued.\n\nSecond.\n"], ["1"]),
    ],
)
def test_text_chunk_boundaries_preserve_content_and_locations(
    tmp_path, split, texts, ids
):
    path = tmp_path / "notes.md"
    path.write_text("\n  First.\nContinued.\n\nSecond.\n")
    chunks = list(for_path(path, options={"split": split})(path))
    assert [c.data["text"] for c in chunks] == texts
    assert [c.id for c in chunks] == ids
    assert all(c.field_paths() == ["/text"] for c in chunks)
    assert [c.source for c in chunks] == [f"{path}:{i}" for i in ids]


@pytest.mark.parametrize("split", ["paragraphs", "lines", "file"])
def test_blank_text_has_no_entries(tmp_path, split):
    path = tmp_path / "empty.txt"
    path.write_text(" \n\t\n")
    assert list(Text(split=split)(path)) == []


def test_fasta_chunking_and_case_preservation(tmp_path):
    path = tmp_path / "seq.fa"
    path.write_text(">seq1 Example sequence\nAcGT\nN N\n\n>seq2\nMKW*\n")
    first, second = FASTA()(path)
    assert first.id == "seq1"
    assert first.data == {
        "id": "seq1",
        "description": "Example sequence",
        "sequence": "AcGTNN",
    }
    assert first.source == f"{path}:1"
    assert second.data == {"id": "seq2", "description": "", "sequence": "MKW*"}
    assert second.field_paths() == ["/id", "/description", "/sequence"]


@pytest.mark.parametrize(
    "text", ["ACGT\n", ">\nACGT\n", ">one\n", ">one\n>two\nACGT\n"]
)
def test_malformed_fasta_rejected(tmp_path, text):
    path = tmp_path / "bad.fasta"
    path.write_text(text)
    with pytest.raises(ValueError, match="FASTA"):
        list(FASTA()(path))


def test_gmt_preserves_empty_description_and_member_order(tmp_path):
    path = tmp_path / "sets.gmt"
    path.write_text("colors\t\tred\tblue\n\nshapes\tExample\tcircle\tcircle\n")
    chunks = list(GMT()(path))
    assert chunks[0].data == {
        "name": "colors",
        "description": "",
        "members": ["red", "blue"],
    }
    assert chunks[1].data["members"] == ["circle", "circle"]
    assert chunks[1].source == f"{path}:3"


@pytest.mark.parametrize(
    "text", ["name\tdescription\n", "\tdesc\tmember\n", "name\tdesc\t\n"]
)
def test_malformed_gmt_rejected(tmp_path, text):
    path = tmp_path / "bad.gmt"
    path.write_text(text)
    with pytest.raises(ValueError, match="GMT"):
        list(GMT()(path))


@pytest.mark.parametrize(
    "suffix,text",
    [
        ("CSV", "id,value\n1,ok\n"),
        ("tsv", "id\tvalue\n1\tok\n"),
        ("json", '{"value":"ok"}'),
        ("ndjson", '{"value":"ok"}\n'),
        ("yml", "value: ok\n"),
        ("toml", 'value="ok"\n'),
        ("obo", "[Term]\nid: X:1\nname: ok\n"),
        ("md", "Some text.\n"),
        ("log", "line one\nline two\n"),
        ("faa", ">a\nMKW\n"),
        ("gmt", "set\tdesc\tmember\n"),
    ],
)
def test_gzip_detection_for_all_builtins(tmp_path, suffix, text):
    plain = tmp_path / f"data.{suffix}"
    compressed = tmp_path / f"data.{suffix}.GZ"
    plain.write_text(text)
    compressed.write_bytes(gzip.compress(text.encode()))
    actual = list(for_path(compressed)(compressed))
    expected = list(for_path(plain)(plain))
    assert [c.data for c in actual] == [c.data for c in expected]
    assert [c.id for c in actual] == [c.id for c in expected]
    assert all(c.source.startswith(str(compressed)) for c in actual)


def test_csv_explicit_format_encoding_and_dialect(tmp_path):
    path = tmp_path / "export.data"
    path.write_bytes("id;name\n001;'Montréal; Québec'\n".encode("latin-1"))
    parser = for_path(
        path, "csv", {"encoding": "latin-1", "delimiter": ";", "quotechar": "'"}
    )
    (chunk,) = parser(path)
    assert chunk.data == {"id": "001", "name": "Montréal; Québec"}


@pytest.mark.parametrize(
    "options",
    [
        {"delimeter": ";"},
        {"delimiter": "xx"},
        {"quotechar": ""},
        {"delimiter": "\n"},
        {"delimiter": '"'},
        {"encoding": "unknown-encoding"},
        {"encoding": "base64_codec"},
    ],
)
def test_invalid_format_options_fail_before_reading(tmp_path, options):
    with pytest.raises(ValueError):
        for_path(tmp_path / "absent.csv", options=options)


def test_obo_stanza_filter(tmp_path):
    path = tmp_path / "terms.obo"
    path.write_text("[Typedef]\nid: part_of\n[Term]\nid: X:1\nname: example\n")
    chunks = list(for_path(path, options={"stanza": "Term"})(path))
    assert [c.id for c in chunks] == ["X:1"]
