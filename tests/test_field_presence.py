"""Field selection when entries do not all carry the same fields.

Heterogeneous entries are normal in OBO, JSONL, YAML, and sparse CSV. Selecting
a field that only some entries have must not abort the run, but a pointer that
matches nothing anywhere is still a typo and must fail before any inference.
"""

import csv
import io
import json

import pytest

from jevotron.cli import main
from jevotron.models import present

# a: both fields, b: /def only, c: neither.
ENTRIES = [
    {"id": "a", "def": "ok", "comment": "ok"},
    {"id": "b", "def": "BAD"},
    {"id": "c", "name": "no definition here"},
]


@pytest.fixture
def entries(tmp_path):
    path = tmp_path / "entries.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in ENTRIES) + "\n")
    return path


@pytest.fixture
def run(entries, fake, monkeypatch, capsys):
    """Scan the heterogeneous fixture offline; return (code, results, stderr)."""
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)

    def go(*options):
        code = main(["scan", str(entries), "--id-column", "id", "--no-cache", *options])
        captured = capsys.readouterr()
        results = [json.loads(line) for line in captured.out.splitlines()]
        return code, results, captured.err

    return go


def test_present_reports_membership_without_raising():
    assert present({"def": "x"}, "/def")
    assert not present({"def": "x"}, "/comment")
    assert present({"def": ["x"]}, "/def/0")
    assert not present({"def": ["x"]}, "/def/1")


def test_present_still_rejects_malformed_pointers():
    # An unusable pointer is an error, never a silently absent field.
    with pytest.raises(ValueError, match="Invalid JSON Pointer"):
        present({"def": "x"}, "def")


def test_missing_field_skips_the_entry_instead_of_aborting(run):
    code, results, err = run("--field", "/def")
    assert code == 0
    assert [r["id"] for r in results] == ["a", "b"]
    assert "Skipped 1 entries lacking selected fields" in err
    assert "/def absent in 1" in err


def test_skip_report_names_only_the_field_that_caused_the_skip(run):
    # c carries neither field, but /comment is optional and never skips an entry.
    _, _, err = run("--field", "/def", "--optional-field", "/comment")
    assert "/def absent in 1" in err
    assert "/comment" not in err


def test_relaxed_skip_report_names_every_absent_field(run):
    # Nothing is required, so c was skipped for carrying none of the selection.
    _, _, err = run("--field", "/def", "--field", "/comment", "--relaxed")
    assert "/def absent in 1" in err and "/comment absent in 1" in err


def test_strict_default_requires_every_selected_field(run):
    code, results, _ = run("--field", "/def", "--field", "/comment")
    assert code == 0
    assert [r["id"] for r in results] == ["a"]


def test_optional_field_is_assessed_only_where_present(run):
    code, results, _ = run("--field", "/def", "--optional-field", "/comment")
    assert code == 0
    assert [r["id"] for r in results] == ["a", "b"]
    assert [f["path"] for f in results[0]["fields"]] == ["/comment", "/def"]
    assert [f["path"] for f in results[1]["fields"]] == ["/def"]


def test_relaxed_assesses_whatever_subset_is_present(run):
    code, results, _ = run("--field", "/def", "--field", "/comment", "--relaxed")
    assert code == 0
    # c is skipped: assessing an entry with none of the selected fields is empty.
    assert [r["id"] for r in results] == ["a", "b"]
    assert [f["path"] for f in results[1]["fields"]] == ["/def"]


def test_absent_fields_are_reported_so_scores_are_interpretable(run):
    code, results, _ = run("--field", "/def", "--optional-field", "/comment")
    assert code == 0
    assert results[0]["absent"] == []
    assert results[1]["absent"] == ["/comment"]
    # b scores on /def alone; the output says so rather than implying /comment passed.
    assert [f["path"] for f in results[1]["fields"]] == ["/def"]


def test_absent_is_empty_when_no_fields_were_selected(run):
    code, results, _ = run()
    assert code == 0
    assert [r["absent"] for r in results] == [[], [], []]


def test_typo_fails_before_inference_and_names_the_pointer(run, fake):
    code, results, err = run("--field", "/defn")
    assert code == 1
    assert results == []
    assert "Field does not exist: '/defn'" in err
    assert not fake.requests


def test_relaxed_typo_also_fails_before_inference(run, fake):
    code, _, err = run("--field", "/defn", "--relaxed")
    assert code == 1
    assert "Field does not exist: '/defn'" in err
    assert not fake.requests


def test_required_fields_that_never_co_occur_fail(tmp_path, fake, monkeypatch, capsys):
    path = tmp_path / "split.jsonl"
    path.write_text('{"id": "a", "x": 1}\n{"id": "b", "y": 2}\n')
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    code = main(
        [
            "scan",
            str(path),
            "--id-column",
            "id",
            "--no-cache",
            "--field",
            "/x",
            "--field",
            "/y",
        ]
    )
    # Both pointers exist somewhere, so this is not a typo, but nothing is assessable.
    err = capsys.readouterr().err
    assert code == 1
    assert "No entry has every required field: '/x', '/y'" in err
    assert not fake.requests


def test_empty_input_is_not_blamed_on_the_field(tmp_path, fake, monkeypatch, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text("")
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    assert main(["scan", str(path), "--no-cache", "--field", "/def"]) == 0
    assert "Field does not exist" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "options, message",
    [
        (["--field", "/def", "--optional-field", "/def"], "both required and optional"),
        (["--field", "/def", "--field", "/def"], "Repeated field"),
        (["--relaxed"], "--relaxed requires"),
    ],
)
def test_contradictory_selections_are_rejected(run, options, message):
    code, _, err = run(*options)
    assert code == 1
    assert message in err


def test_preview_skips_the_same_entries_as_scan(entries, capsys):
    assert main(["preview", str(entries), "--id-column", "id", "--field", "/def"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in rows] == ["a", "b"]
    assert rows[1]["absent"] == []


def test_preview_reports_what_a_scan_would_skip(entries, capsys):
    # Preview is the free dry run, so it must show the cost of a selection.
    assert main(["preview", str(entries), "--id-column", "id", "--field", "/def"]) == 0
    err = capsys.readouterr().err
    assert "Skipped 1 entries lacking selected fields" in err
    assert "/def absent in 1" in err


def test_preview_stays_quiet_when_nothing_is_skipped(entries, capsys):
    assert main(["preview", str(entries), "--id-column", "id"]) == 0
    assert capsys.readouterr().err == ""


def test_limit_counts_assessed_entries_not_skipped_ones(run):
    code, results, _ = run("--field", "/def", "--limit", "2")
    assert code == 0
    assert [r["id"] for r in results] == ["a", "b"]


def test_csv_output_carries_the_absent_column(entries, fake, monkeypatch, capsys):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    assert (
        main(
            [
                "scan",
                str(entries),
                "--id-column",
                "id",
                "--no-cache",
                "--field",
                "/def",
                "--optional-field",
                "/comment",
                "--output-format",
                "csv",
            ]
        )
        == 0
    )
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out)))
    absent = {row["id"]: row["absent"] for row in rows}
    assert absent == {"a": "[]", "b": '["/comment"]'}
