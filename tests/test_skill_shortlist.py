"""Exercise the installed skill helper without credentials or inference."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[1] / "skills/jt-triage/scripts/shortlist.py"


def run_helper(report, *args):
    return subprocess.run(
        [sys.executable, str(HELPER), str(report), *args],
        capture_output=True,
        text=True,
    )


def test_shortlist_ranks_bounds_counts_and_preserves_evidence(tmp_path):
    report = tmp_path / "results.jsonl"
    rows = [
        {
            "id": str(i),
            "score": score,
            "source": f"data.csv:row:{i}",
            "fields": [{"path": "/value", "value": f"value-{i}"}],
            "warning": False,
        }
        for i, score in enumerate([0.1, 0.8, 0.9, 0.8, 0.5])
    ]
    report.write_text("\n".join(json.dumps(row) for row in rows) + "\n\n")
    result = run_helper(report, "--top", "2", "--threshold", "0.5")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["report_rows"] == 5
    assert data["matching_rows"] == 4
    assert data["candidates"] == [rows[2], rows[1]]


def test_empty_report(tmp_path):
    report = tmp_path / "empty.jsonl"
    report.write_text("")
    result = run_helper(report)
    assert result.returncode == 0
    assert json.loads(result.stdout)["candidates"] == []
    assert json.loads(result.stdout)["report_rows"] == 0


@pytest.mark.parametrize(
    "bad_row",
    ['{"id":"x",', '{"id":"x","score":NaN}', '{"id":"x","score":2}', "[]"],
)
def test_invalid_report_has_no_partial_shortlist(tmp_path, bad_row):
    report = tmp_path / "broken.jsonl"
    report.write_text('{"id":"valid","score":0.9}\n' + bad_row + "\n")
    result = run_helper(report)
    assert result.returncode == 1
    assert not result.stdout
    assert f"{report}:2:" in result.stderr


@pytest.mark.parametrize("args", [("--top", "0"), ("--threshold", "nan")])
def test_invalid_selection(tmp_path, args):
    result = run_helper(tmp_path / "unused.jsonl", *args)
    assert result.returncode == 2
    assert not result.stdout
