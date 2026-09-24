import pytest

from jevotron.parsers import YAML
from jevotron.runner import preview


@pytest.mark.parametrize(
    "text",
    [
        "<<: {value: first, value: second}\n",
        "<<: {<<: {value: first, value: second}}\n",
        "<<: [{other: ok}, {value: first, value: second}]\n",
        "<<: {value: first, value: second}\nvalue: override\n",
    ],
)
def test_duplicate_keys_in_merge_sources_rejected_before_request(tmp_path, text):
    path = tmp_path / "records.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match="Duplicate YAML key 'value' at line 1"):
        next(preview(YAML()(path)))


@pytest.mark.parametrize(
    "text,expected",
    [
        (
            "first: {<<: &base {<<: {x: 1}, x: 2}}\nsecond: *base\n",
            {"first": {"x": 2}, "second": {"x": 2}},
        ),
        (
            "second: &base {<<: {x: 1}, x: 2}\nfirst: {<<: *base}\n",
            {"first": {"x": 2}, "second": {"x": 2}},
        ),
        (
            "<<: [{x: first, a: 1}, {x: second, b: 2}]\n",
            {"x": "first", "a": 1, "b": 2},
        ),
        (
            "x: explicit\n<<: [{x: first, a: 1}, {x: second, b: 2}]\n",
            {"x": "explicit", "a": 1, "b": 2},
        ),
        (
            "<<: [{x: first, a: 1}, {x: second, b: 2}]\nx: explicit\n",
            {"x": "explicit", "a": 1, "b": 2},
        ),
    ],
)
def test_merge_overrides_and_aliases_preserve_values(tmp_path, text, expected):
    path = tmp_path / "records.yaml"
    path.write_text(text)
    (row,) = preview(YAML()(path))
    assert row["request"]["state"]["entry"] == expected


def test_merge_validation_is_independent_for_each_document(tmp_path):
    path = tmp_path / "records.yaml"
    path.write_text("<<: {x: 1}\nx: 2\n---\n<<: {x: 3, x: 4}\n")
    chunks = iter(YAML()(path))
    assert next(chunks).data == {"x": 2}
    with pytest.raises(ValueError, match="Duplicate YAML key 'x' at line 4"):
        next(chunks)
