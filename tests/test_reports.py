from contextlib import closing
from dataclasses import replace

import pytest
import yaml

from jevotron.models import FieldResult, Result
from jevotron.reports import ReportQuery, report_rows


@pytest.fixture
def assessment():
    fields = [
        FieldResult(
            "/context",
            {"text": "café", "flags": [True, None]},
            "NORMAL",
            {"NORMAL": 0.9, "ANOMALY": 0.1},
            0.98,
            0.1,
        ),
        FieldResult(
            "/value", "BAD", "ANOMALY", {"NORMAL": 0.2, "ANOMALY": 0.8}, 0.6, 0.8
        ),
    ]
    return Result(
        "1",
        None,
        "ANOMALY",
        0.8,
        True,
        fields,
        [],
        "test",
        "2026-01-01",
        "hash",
        False,
        {},
    )


def test_entry_and_field_confidence_and_parent_context(assessment):
    (entry,) = report_rows(assessment, "entries", 0.5)
    assert entry["confidence"] == 0.6
    normal, anomaly = report_rows(assessment, "fields", 0.5)
    assert normal["label"] == "NORMAL" and normal["confidence"] == 0.98
    assert not normal["warning"] and normal["entry_warning"]
    assert normal["entry_label"] == "ANOMALY"
    assert normal["entry_score"] == 0.8
    assert normal["entry_confidence"] == 0.6
    assert anomaly["warning"]
    assert "fields" not in normal


@pytest.mark.parametrize("view", ["entries", "fields"])
@pytest.mark.parametrize("order_by", [None, "confidence DESC"])
def test_query_round_trip_preserves_heterogeneous_values(assessment, view, order_by):
    rows = list(report_rows(assessment, view, 0.5))
    with closing(ReportQuery(view, "source IS NULL AND NOT cached", order_by)) as query:
        selected = list(query.select(rows))
        assert selected == rows
        assert [list(row) for row in selected] == [list(row) for row in rows]
        # Include nested mappings: dict equality alone cannot detect YAML churn.
        assert "".join(
            yaml.safe_dump([row], sort_keys=False) for row in selected
        ) == "".join(yaml.safe_dump([row], sort_keys=False) for row in rows)


def test_field_probability_and_nested_value_expressions(assessment):
    rows = list(report_rows(assessment, "fields", 0.5))
    with closing(
        ReportQuery(
            "fields", "CAST(probabilities ->> 'ANOMALY' AS DOUBLE) >= 0.8", None
        )
    ) as query:
        assert [row["path"] for row in query.select(rows)] == ["/value"]
    with closing(ReportQuery("fields", "value ->> 'text' = 'café'", None)) as query:
        assert [row["path"] for row in query.select(rows)] == ["/context"]


@pytest.mark.parametrize(
    "order_by", [None, "score DESC, confidence ASC", "score DESC -- trailing comment"]
)
def test_filter_batches_and_global_order(assessment, order_by):
    size = ReportQuery.batch_size * 2 + 3
    rows = [
        next(
            report_rows(
                replace(assessment, id=str(i), score=(i % 5) / 5), "entries", 0.5
            )
        )
        for i in range(size)
    ]
    expected = [row for row in rows if row["score"] >= 0.4]
    if order_by:
        expected.sort(key=lambda row: -row["score"])
    with closing(ReportQuery("entries", "score >= 0.4", order_by)) as query:
        assert list(query.select(rows)) == expected


def test_where_emits_before_consuming_all_input(assessment):
    row = next(report_rows(assessment, "entries", 0.5))
    consumed = 0

    def source():
        nonlocal consumed
        for _ in range(ReportQuery.batch_size + 1):
            consumed += 1
            yield row

    with closing(ReportQuery("entries", "warning", None)) as query:
        selected = query.select(source())
        assert next(selected) == row
        assert consumed == ReportQuery.batch_size
        selected.close()


def test_order_emits_nothing_on_incomplete_input(assessment):
    def source():
        yield next(report_rows(assessment, "entries", 0.5))
        raise ValueError("input failed")

    with closing(ReportQuery("entries", None, "confidence DESC")) as query:
        with pytest.raises(ValueError, match="input failed"):
            next(query.select(source()))


@pytest.mark.parametrize(
    "where,order_by", [("label = 'MISSING'", None), (None, "confidence DESC")]
)
def test_empty_input(where, order_by):
    with closing(ReportQuery("entries", where, order_by)) as query:
        assert list(query.select([])) == []
