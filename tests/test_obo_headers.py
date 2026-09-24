import json

import pytest

from jevotron.cli import main
from jevotron.parsers import OBO


@pytest.mark.parametrize(
    "header",
    [
        "[Term]",
        "[Term] ! comment",
        "[Term] ! example [note]",
        "[Term]!comment with [ and ] brackets",
        "[Term]\t! [Typedef]",
    ],
)
def test_header_comments_preserve_stanza_fields_and_locations(tmp_path, header):
    path = tmp_path / "terms.obo"
    path.write_text(
        "format-version: 1.2\n\n"
        f"{header}\nid: X:1\nname: example ! raw comment\n"
        'def: "A \\! literal." [] ! trailing [note]\n'
        "[Typedef] ! other stanza\nid: part_of\n"
    )
    (chunk,) = OBO(stanza="Term")(path)
    assert chunk.id == "X:1"
    assert chunk.source == f"{path}:3"
    assert chunk.data == {
        "_stanza": "Term",
        "id": ["X:1"],
        "name": ["example ! raw comment"],
        "def": ['"A \\! literal." [] ! trailing [note]'],
    }
    assert chunk.field_paths() == ["/id/0", "/name/0", "/def/0"]
    assert [c.data["_stanza"] for c in OBO()(path)] == ["Term", "Typedef"]


@pytest.mark.parametrize(
    "header",
    ["[Term", "[]", "[ ]", "[Term]]", "[[Term]]", "[Term] trailing text"],
)
def test_malformed_stanza_headers_remain_errors(tmp_path, header):
    path = tmp_path / "terms.obo"
    path.write_text(f"{header}\nid: X:1\n")
    with pytest.raises(ValueError, match="Invalid OBO stanza header"):
        list(OBO()(path))


@pytest.mark.parametrize("comment", ["comment", "example [note]"])
def test_cli_filter_includes_commented_terms(
    tmp_path, capsys, monkeypatch, fake, comment
):
    path = tmp_path / "terms.obo"
    path.write_text(f"[Term] ! {comment}\nid: X:1\nname: example\n")
    args = [str(path), "--format-option", "stanza=Term"]
    assert main(["preview", *args]) == 0
    (row,) = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert row["id"] == "X:1"
    assert row["request"]["state"]["entry"]["_stanza"] == "Term"
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    assert main(["scan", *args, "--no-cache"]) == 0
    assert fake.requests == [row["request"]]
