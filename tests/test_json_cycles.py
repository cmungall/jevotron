import pytest

from jevotron import Chunk, Config
from jevotron.cli import main
from jevotron.models import validate_json
from jevotron.parsers import YAML
from jevotron.runner import preview, scan


@pytest.fixture(params=["list", "dict", "indirect_list", "mixed"])
def cycle(request):
    if request.param == "list":
        value = []
        value.append(value)
    elif request.param == "dict":
        value = {}
        value["self"] = value
    elif request.param == "indirect_list":
        value = [[]]
        value[0].append(value)
    else:
        value = {"children": []}
        value["children"].append({"parent": value})
    return value


def test_cyclic_chunks_fail_before_requests(cycle, fake):
    chunk = Chunk("cyclic", cycle)
    with pytest.raises(ValueError, match="must not contain cycles"):
        list(preview([chunk]))
    with pytest.raises(ValueError, match="must not contain cycles"):
        list(scan([chunk], client=fake, cache=None))
    assert fake.requests == []


def test_cyclic_config_exemplars_are_rejected(cycle):
    with pytest.raises(ValueError, match="must not contain cycles"):
        Config(exemplars=[cycle]).validate()


def test_shared_acyclic_containers_are_valid():
    shared = {"values": [1, 2]}
    data = {"first": shared, "second": [shared, shared["values"]]}
    assert Chunk("shared", data).field_paths() == ["/first", "/second"]
    Config(exemplars=[shared, shared]).validate()


def test_deep_nesting_is_a_validation_error():
    data = []
    for _ in range(2000):
        data = [data]
    with pytest.raises(ValueError, match="nested too deeply"):
        validate_json(data)


def test_yaml_acyclic_aliases_produce_a_preview(tmp_path):
    path = tmp_path / "shared.yaml"
    path.write_text("first: &shared [1, 2]\nsecond: *shared\n")
    (row,) = preview(YAML()(path))
    assert row["request"]["state"]["entry"] == {
        "first": [1, 2],
        "second": [1, 2],
    }


@pytest.mark.parametrize("command", ["preview", "scan"])
@pytest.mark.parametrize("text", ["value: &loop [*loop]\n", "value: &loop [[*loop]]\n"])
def test_yaml_cycles_return_clean_cli_errors(
    tmp_path, capsys, monkeypatch, fake, command, text
):
    path = tmp_path / "recursive.yaml"
    path.write_text(text)
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    args = [command, str(path)] + (["--no-cache"] if command == "scan" else [])
    assert main(args) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Error: JSON-compatible data must not contain cycles" in captured.err
    assert "Traceback" not in captured.err
    assert fake.requests == []
