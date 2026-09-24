import csv
import json
from dataclasses import replace
from pathlib import Path

import pytest

from jevotron import Chunk, Config, GraphContext, ReferenceJoin, preview, scan
from jevotron.cli import main
from jevotron.runner import make_request


def write_json(path, value):
    path.write_text(json.dumps(value))
    return path


def join_config(tmp_path, rows=None, **options):
    path = write_json(
        tmp_path / "categories.json",
        rows
        or [
            {"id": "fragile", "max_weight": 5},
            {"id": "heavy", "max_weight": 50},
        ],
    )
    return Config(
        references=[
            ReferenceJoin(
                "categories",
                path,
                "/category",
                "/id",
                **options,
            )
        ]
    )


def item(category="fragile", ident="item"):
    return Chunk(ident, {"category": category, "weight": 8}, fields=["/weight"])


def test_join_keeps_entry_fields_and_exposes_exact_request_and_provenance(
    tmp_path, fake
):
    config = join_config(tmp_path, fields=["/max_weight"])
    chunk = item()
    shown = next(preview([chunk], config))
    result = next(scan([chunk], config, cache=None, client=fake))
    assert shown["request"] == fake.requests[0] == make_request(chunk, config)[0]
    assert shown["request"]["state"]["entry"] == chunk.data
    assert shown["fields"] == ["/weight"]
    assert shown["id"] == "item"
    context = shown["request"]["state"]["context"][0]
    assert context["records"] == [{"key": "fragile", "data": {"/max_weight": 5}}]
    assert result.context == shown["context"] == result.to_dict()["context"]
    assert result.context["provenance"][0]["records"][0]["source"].endswith(":entry:1")
    assert result.request_hash == shown["request_hash"]


@pytest.mark.parametrize(
    "suffix,text",
    [
        ("csv", "id,max_weight\nfragile,5\n"),
        ("yaml", "- id: fragile\n  max_weight: 5\n"),
        ("json", '[{"id": "fragile", "max_weight": 5}]'),
    ],
)
def test_reference_file_formats(tmp_path, suffix, text):
    path = tmp_path / f"references.{suffix}"
    path.write_text(text)
    config = Config(references=[ReferenceJoin("limits", path, "/category", "/id")])
    row = next(preview([item()], config))["context"]["data"][0]["records"][0]
    assert str(row["data"]["max_weight"]) == "5"


def test_nested_reference_collection(tmp_path):
    path = write_json(tmp_path / "catalog.json", {"catalog": [{"id": "fragile"}]})
    config = Config(
        references=[
            ReferenceJoin("catalog", path, "/category", "/id", records="/catalog")
        ]
    )
    assert next(preview([item()], config))["context"]["data"][0]["records"]


def test_join_selective_cache_invalidation_reorder_and_move(tmp_path, fake, cache_path):
    config = join_config(tmp_path)
    chunks = [item("fragile", "a"), item("heavy", "b")]
    first = list(scan(chunks, config, cache=cache_path, client=fake))
    path = config.references[0].path
    rows = json.loads(path.read_text())
    write_json(path, list(reversed(rows)))
    moved = tmp_path / "moved.json"
    path.rename(moved)
    config.references[0].path = moved
    replay = list(scan(chunks, config, cache=cache_path))
    assert all(r.cached for r in replay)
    assert first[0].request_hash == replay[0].request_hash
    assert first[0].context["provenance"] != replay[0].context["provenance"]
    write_json(moved, [dict(rows[0], max_weight=10), rows[1], {"id": "unrelated"}])
    results = list(scan(chunks, config, cache=cache_path, client=fake))
    assert [r.cached for r in results] == [False, True]
    assert len(fake.requests) == 3


def test_projected_out_changes_preserve_cache(tmp_path, fake, cache_path):
    config = join_config(tmp_path, fields=["/max_weight"])
    list(scan([item()], config, cache=cache_path, client=fake))
    write_json(
        config.references[0].path,
        [{"id": "fragile", "max_weight": 5, "irrelevant": "new"}],
    )
    assert next(scan([item()], config, cache=cache_path)).cached


def test_join_missing_and_duplicates_are_explicit(tmp_path):
    config = join_config(tmp_path)
    with pytest.raises(ValueError, match="no match"):
        list(preview([item("absent")], config))
    config.references[0].missing = "include"
    data = next(preview([item("absent")], config))["context"]["data"][0]
    assert data["missing"] and data["records"] == []
    write_json(config.references[0].path, [{"id": "fragile"}, {"id": "fragile"}])
    with pytest.raises(ValueError, match="duplicate key"):
        list(preview([item()], config))


def test_many_is_deterministic_and_checks_record_limit(tmp_path):
    rows = [{"id": "fragile", "rule": "b"}, {"id": "fragile", "rule": "a"}]
    config = join_config(tmp_path, rows, many=True)
    first = next(preview([item()], config))
    write_json(config.references[0].path, list(reversed(rows)))
    assert next(preview([item()], config))["request_hash"] == first["request_hash"]
    config.references[0].max_records = 1
    with pytest.raises(ValueError, match="max_records"):
        list(preview([item()], config))


def test_scalar_keys_do_not_coerce_types(tmp_path):
    config = join_config(tmp_path, [{"id": 1}, {"id": "1"}, {"id": True}])
    for key in (1, "1", True):
        data = next(preview([item(key)], config))["context"]["data"][0]["records"][0][
            "data"
        ]
        assert type(data["id"]) is type(key)
    write_json(config.references[0].path, [{"id": ["invalid"]}])
    with pytest.raises(ValueError, match="nonempty JSON scalars"):
        list(preview([item()], config))


def graph_config(tmp_path, **options):
    nodes = write_json(tmp_path / "nodes.json", [{"id": n, "name": n} for n in "abcdx"])
    edges = write_json(
        tmp_path / "edges.json",
        [
            {"source": "a", "target": "b", "relation": "parent"},
            {"source": "b", "target": "c", "relation": "parent"},
            {"source": "c", "target": "a", "relation": "parent"},
            {"source": "c", "target": "d", "relation": "parent"},
            {"source": "a", "target": "x", "relation": "unrelated"},
        ],
    )
    return Config(
        references=[GraphContext("ancestors", nodes, edges, ["parent"], **options)]
    )


def graph_data(config, anchor="a"):
    return next(preview([Chunk(anchor, {"name": anchor})], config))["context"]["data"][
        0
    ]


def test_n_hop_context_and_cycles(tmp_path):
    config = graph_config(tmp_path, hops=2)
    data = graph_data(config)
    assert [(n["id"], n["distance"]) for n in data["nodes"]] == [
        ("a", 0),
        ("b", 1),
        ("c", 2),
    ]
    # The complete subgraph includes c -> a on the traversal boundary.
    assert len(data["edges"]) == 3
    assert all(e["relation"] == "parent" for e in data["edges"])
    config.references[0].hops = 100
    assert {n["id"] for n in graph_data(config)["nodes"]} == set("abcd")


@pytest.mark.parametrize(
    "direction,expected", [("outgoing", "ab"), ("incoming", "ac"), ("both", "abc")]
)
def test_graph_direction(tmp_path, direction, expected):
    config = graph_config(tmp_path, direction=direction)
    assert {n["id"] for n in graph_data(config)["nodes"]} == set(expected)


def test_graph_source_pointer_and_missing_anchor(tmp_path):
    config = graph_config(tmp_path, source_key="/node")
    row = next(preview([Chunk("report-id", {"node": "b"})], config))
    assert row["id"] == "report-id" and row["context"]["data"][0]["anchor"] == "b"
    config.references[0].source_key = None
    with pytest.raises(ValueError, match="no node"):
        graph_data(config, "absent")
    config.references[0].missing = "include"
    data = graph_data(config, "absent")
    assert data["missing"] and data["nodes"] == [] and data["edges"] == []


def test_graph_duplicate_nodes_and_dangling_endpoints(tmp_path):
    config = graph_config(tmp_path)
    path = config.references[0].nodes
    original = json.loads(path.read_text())
    write_json(path, original + [original[0]])
    with pytest.raises(ValueError, match="duplicate node"):
        graph_data(config)
    write_json(path, original[1:])
    with pytest.raises(ValueError, match="missing endpoint"):
        graph_data(config)


@pytest.mark.parametrize(
    "options,error",
    [
        ({"max_nodes": 1}, "max_nodes"),
        ({"hops": 2, "max_edges": 2}, "max_edges"),
        ({"max_bytes": 10}, "max_bytes"),
    ],
)
def test_graph_budgets_fail_before_inference(tmp_path, fake, options, error):
    config = graph_config(tmp_path, **options)
    with pytest.raises(ValueError, match=error):
        list(scan([Chunk("a", {"name": "a"})], config, cache=None, client=fake))
    assert fake.requests == []


def test_join_byte_budget_before_inference(tmp_path, fake):
    config = join_config(tmp_path, max_bytes=10)
    with pytest.raises(ValueError, match="max_bytes"):
        list(scan([item()], config, cache=None, client=fake))
    assert fake.requests == []


def test_graph_cache_dependency_and_file_order(tmp_path, fake, cache_path):
    config = graph_config(tmp_path, hops=1)
    chunks = [Chunk("a", {"name": "a"}), Chunk("d", {"name": "d"})]
    list(scan(chunks, config, cache=cache_path, client=fake))
    spec = config.references[0]
    nodes = json.loads(spec.nodes.read_text())
    edges = json.loads(spec.edges.read_text())
    write_json(spec.nodes, list(reversed(nodes)))
    write_json(spec.edges, list(reversed(edges)))
    assert all(r.cached for r in scan(chunks, config, cache=cache_path))
    nodes[1]["name"] = "changed b"
    write_json(spec.nodes, nodes)
    assert [r.cached for r in scan(chunks, config, cache=cache_path, client=fake)] == [
        False,
        True,
    ]
    edges.append({"source": "a", "target": "x", "relation": "parent"})
    write_json(spec.edges, edges)
    assert [r.cached for r in scan(chunks, config, cache=cache_path, client=fake)] == [
        False,
        True,
    ]


def test_old_request_and_report_shapes_unchanged(fake):
    row = next(preview([item()]))
    assert set(row["request"]["state"]) == {"entry", "guidance", "exemplars"}
    assert "context" not in row
    assert "context" not in next(scan([item()], cache=None, client=fake)).to_dict()


def test_multiple_named_contexts_are_order_independent(tmp_path):
    config = join_config(tmp_path)
    config.references.append(replace(config.references[0], name="second"))
    first = next(preview([item()], config))
    config.references.reverse()
    assert next(preview([item()], config))["request_hash"] == first["request_hash"]
    config.references[0].name = config.references[1].name
    with pytest.raises(ValueError, match="distinct names"):
        list(preview([item()], config))


def test_context_indexes_are_snapshotted_per_iterator(tmp_path):
    config = join_config(tmp_path)
    rows = preview([item(ident="a"), item(ident="b")], config)
    first = next(rows)
    write_json(config.references[0].path, [{"id": "fragile", "max_weight": 99}])
    assert next(rows)["request_hash"] == first["request_hash"]
    assert next(preview([item()], config))["request_hash"] != first["request_hash"]


def cli_files(tmp_path):
    data = tmp_path / "items.csv"
    data.write_text("id,category,weight\nitem,fragile,8\n")
    reference = write_json(
        tmp_path / "catalog.json", [{"id": "fragile", "max_weight": 5}]
    )
    config = tmp_path / "config.py"
    config.write_text(
        "from pathlib import Path\nfrom jevotron import Config, ReferenceJoin\n"
        'config=Config(references=[ReferenceJoin("catalog", '
        'Path(__file__).parent / "catalog.json", "/category", "/id")])\n'
    )
    return data, reference, config


def test_cli_context_preview_and_csv(tmp_path, fake, monkeypatch, capsys):
    data, reference, config = cli_files(tmp_path)
    assert main(["preview", str(data), "--config", str(config)]) == 0
    row = json.loads(capsys.readouterr().out)
    assert row["context"]["provenance"][0]["files"] == [str(reference)]
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    output = tmp_path / "out.csv"
    assert (
        main(
            [
                "scan",
                str(data),
                "--config",
                str(config),
                "--no-cache",
                "--output-format",
                "csv",
                "-o",
                str(output),
            ]
        )
        == 0
    )
    report = next(csv.DictReader(output.open()))
    assert json.loads(report["context"]) == row["context"]


@pytest.mark.parametrize("alias", [False, True])
def test_reference_files_are_protected_from_output(tmp_path, capsys, alias):
    data, reference, config = cli_files(tmp_path)
    original = reference.read_bytes()
    output = reference
    if alias:
        output = tmp_path / "alias.json"
        output.hardlink_to(reference)
    assert main(["preview", str(data), "--config", str(config), "-o", str(output)]) == 1
    assert reference.read_bytes() == original
    assert "must not overwrite" in capsys.readouterr().err


def test_reference_file_protected_from_cache(tmp_path, capsys):
    data, reference, config = cli_files(tmp_path)
    original = reference.read_bytes()
    assert (
        main(["scan", str(data), "--config", str(config), "--cache", str(reference)])
        == 1
    )
    assert reference.read_bytes() == original
    assert "Cache must not overwrite" in capsys.readouterr().err


@pytest.mark.parametrize(
    "options",
    [
        {"hops": 0},
        {"max_nodes": True},
        {"max_edges": -1},
        {"max_bytes": 0},
        {"direction": "sideways"},
        {"relations": []},
        {"relations": ["x", "x"]},
    ],
)
def test_graph_configuration_validation(tmp_path, options):
    config = graph_config(tmp_path)
    for key, value in options.items():
        setattr(config.references[0], key, value)
    with pytest.raises(ValueError):
        config.validate()


def test_join_provenance_identifies_canonically_sorted_records(tmp_path):
    config = join_config(
        tmp_path,
        [
            {"id": "fragile", "rule": "z"},
            {"id": "fragile", "rule": "a"},
        ],
        many=True,
    )
    context = next(preview([item()], config))["context"]
    records = context["data"][0]["records"]
    origins = context["provenance"][0]["records"]
    assert records[0]["data"]["rule"] == "a"
    assert origins[0]["record_index"] == 0
    assert origins[0]["source"].endswith(":entry:2")
    assert origins[1]["record_index"] == 1
    assert origins[1]["source"].endswith(":entry:1")


def test_parallel_graph_edges_have_unambiguous_provenance(tmp_path):
    config = graph_config(tmp_path)
    path = config.references[0].edges
    write_json(
        path,
        [
            {"source": "a", "target": "b", "relation": "parent", "evidence": "z"},
            {"source": "a", "target": "b", "relation": "parent", "evidence": "a"},
        ],
    )
    context = next(preview([Chunk("a", {"name": "a"})], config))["context"]
    edges = context["data"][0]["edges"]
    origins = context["provenance"][0]["edges"]
    assert edges[0]["data"]["evidence"] == "a"
    assert origins[0]["edge_index"] == 0
    assert origins[0]["locations"][0].endswith(":entry:2")
    assert origins[1]["edge_index"] == 1
    assert origins[1]["locations"][0].endswith(":entry:1")


def test_wrapped_collection_provenance(tmp_path):
    config = join_config(tmp_path)
    spec = config.references[0]
    write_json(spec.path, {"wrapped": [{"id": "fragile"}]})
    spec.records = "/wrapped"
    context = next(preview([item()], config))["context"]
    assert context["provenance"][0]["settings"]["records"] == "/wrapped"
    config = graph_config(tmp_path)
    graph = config.references[0]
    for path in (graph.nodes, graph.edges):
        write_json(path, {"wrapped": json.loads(path.read_text())})
    graph.node_records = graph.edge_records = "/wrapped"
    context = next(preview([Chunk("a", {"name": "a"})], config))["context"]
    assert context["provenance"][0]["settings"]["node_records"] == "/wrapped"
    assert context["provenance"][0]["settings"]["edge_records"] == "/wrapped"


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("join", "path", None),
        ("join", "source_key", None),
        ("join", "reference_key", "id"),
        ("join", "records", "/bad~2"),
        ("join", "fields", ["/bad~"]),
        ("join", "many", 1),
        ("graph", "nodes", None),
        ("graph", "edges", 5),
        ("graph", "source_key", []),
        ("graph", "node_key", None),
        ("graph", "edge_relation", "relation"),
        ("graph", "edge_source", "/bad~"),
        ("graph", "edge_target", 1),
        ("graph", "node_records", None),
        ("graph", "edge_records", "/bad~3"),
        ("graph", "fields", ["bad"]),
    ],
)
def test_reference_configuration_rejects_invalid_paths_and_pointers(
    tmp_path, kind, field, value
):
    config = join_config(tmp_path) if kind == "join" else graph_config(tmp_path)
    setattr(config.references[0], field, value)
    with pytest.raises(ValueError):
        config.validate()


@pytest.mark.parametrize("filename", ["jev_config.py", "graph_config.py"])
def test_bundled_reference_example_supplies_the_required_limits(filename):
    from jevotron.config import load_config

    folder = Path(__file__).resolve().parents[1] / "examples/references"
    config = load_config(folder / filename)
    previews = list(preview(config.parser(folder / "items.yaml"), config))
    assert [row["id"] for row in previews] == ["cups", "towels"]
    violations = []
    for row in previews:
        state = row["request"]["state"]
        context = state["context"][0]
        if context["kind"] == "join":
            limit = int(context["records"][0]["data"]["/max_stack"])
        else:
            rules = [
                n for n in context["nodes"] if n["data"]["kind"] == "handling_rule"
            ]
            assert len(rules) == 1 and rules[0]["distance"] == 2
            limit = rules[0]["data"]["max_stack"]
        violations.append(state["entry"]["stack_count"] > limit)
        assert row["fields"] == ["/stack_count"]
    assert violations == [True, False]
