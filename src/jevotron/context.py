"""Deterministic, bounded reference records and graph neighborhoods."""

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jevotron.models import Chunk, json_text, resolve, validate_json
from jevotron.parsers import for_path


def _key(value: Any) -> str:
    if value is None or isinstance(value, (dict, list)) or value == "":
        raise ValueError("Reference keys must be nonempty JSON scalars")
    validate_json(value)
    return json_text(value)


def _positive(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _pointer(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or (value and not value.startswith("/"))
        or re.search(r"~(?![01])", value)
    ):
        raise ValueError(f"{name} must be a valid JSON Pointer")


def _path(value: Path, name: str) -> None:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError(f"{name} must be a file path")


def _common(name: str, fields: list[str] | None, missing: str, max_bytes: int):
    if not isinstance(name, str) or not name:
        raise ValueError("Reference names must be nonempty strings")
    if missing not in ("error", "include"):
        raise ValueError("missing must be 'error' or 'include'")
    if fields is not None and (
        not isinstance(fields, list)
        or not fields
        or any(not isinstance(p, str) for p in fields)
        or len(set(fields)) != len(fields)
    ):
        raise ValueError("Reference fields must be distinct JSON Pointer strings")
    for pointer in fields or []:
        _pointer(pointer, "fields")
    _positive(max_bytes, "max_bytes")


def _rows(path: Path, records: str):
    options = {"records": records} if records else None
    parser = for_path(Path(path), options=options)
    for chunk in parser(Path(path)):
        validate_json(chunk.data)
        yield chunk


def _project(data: Any, fields: list[str] | None) -> Any:
    return data if fields is None else {p: resolve(data, p) for p in sorted(fields)}


@dataclass
class ReferenceJoin:
    """Attach exact-key matches from a reference file to each assessed entry."""

    name: str
    path: Path
    source_key: str
    reference_key: str
    fields: list[str] | None = None
    many: bool = False
    missing: str = "error"
    max_records: int = 50
    max_bytes: int = 32768
    records: str = ""

    def validate(self):
        _common(self.name, self.fields, self.missing, self.max_bytes)
        _path(self.path, "path")
        for name in ("source_key", "reference_key", "records"):
            _pointer(getattr(self, name), name)
        _positive(self.max_records, "max_records")
        if not isinstance(self.many, bool):
            raise ValueError("many must be a boolean")

    def source_paths(self) -> list[Path]:
        return [Path(self.path)]


@dataclass
class GraphContext:
    """Retrieve a relation-filtered N-hop neighborhood from node/edge files."""

    name: str
    nodes: Path
    edges: Path
    relations: list[str]
    source_key: str | None = None
    node_key: str = "/id"
    edge_source: str = "/source"
    edge_target: str = "/target"
    edge_relation: str = "/relation"
    direction: str = "outgoing"
    hops: int = 1
    fields: list[str] | None = None
    missing: str = "error"
    max_nodes: int = 100
    max_edges: int = 200
    max_bytes: int = 65536
    node_records: str = ""
    edge_records: str = ""

    def validate(self):
        _common(self.name, self.fields, self.missing, self.max_bytes)
        _path(self.nodes, "nodes")
        _path(self.edges, "edges")
        if self.source_key is not None:
            _pointer(self.source_key, "source_key")
        for name in (
            "node_key",
            "edge_source",
            "edge_target",
            "edge_relation",
            "node_records",
            "edge_records",
        ):
            _pointer(getattr(self, name), name)
        if (
            not isinstance(self.relations, list)
            or not self.relations
            or any(not isinstance(r, str) or not r for r in self.relations)
            or len(set(self.relations)) != len(self.relations)
        ):
            raise ValueError("Graph relations must be distinct nonempty strings")
        if self.direction not in ("outgoing", "incoming", "both"):
            raise ValueError("Graph direction must be outgoing, incoming, or both")
        for name in ("hops", "max_nodes", "max_edges"):
            _positive(getattr(self, name), name)

    def source_paths(self) -> list[Path]:
        return [Path(self.nodes), Path(self.edges)]


class _JoinIndex:
    def __init__(self, spec: ReferenceJoin):
        self.spec = spec
        self.index: dict[str, list[Chunk]] = defaultdict(list)
        for chunk in _rows(spec.path, spec.records):
            key = _key(resolve(chunk.data, spec.reference_key))
            if self.index[key] and not spec.many:
                raise ValueError(f"Reference {spec.name!r}: duplicate key {key}")
            self.index[key].append(chunk)

    def context(self, chunk: Chunk):
        spec = self.spec
        value = resolve(chunk.data, spec.source_key)
        matches = self.index.get(_key(value), [])
        if not matches and spec.missing == "error":
            raise ValueError(f"Reference {spec.name!r}: no match for {_key(value)}")
        if len(matches) > spec.max_records:
            raise ValueError(f"Reference {spec.name!r}: max_records exceeded")
        records = sorted(
            [
                ({"key": value, "data": _project(row.data, spec.fields)}, row)
                for row in matches
            ],
            key=lambda pair: (
                json_text(pair[0]),
                json_text([pair[1].id, pair[1].source]),
            ),
        )
        settings = {
            "source_key": spec.source_key,
            "reference_key": spec.reference_key,
            "fields": sorted(spec.fields) if spec.fields is not None else None,
            "many": spec.many,
            "missing": spec.missing,
        }
        data = {
            "name": spec.name,
            "kind": "join",
            "lookup": settings,
            "key": value,
            "missing": not matches,
            "records": [record for record, row in records],
        }
        provenance = {
            "name": spec.name,
            "files": [str(spec.path)],
            "settings": {**settings, "records": spec.records},
            "limits": {"max_records": spec.max_records, "max_bytes": spec.max_bytes},
            "records": [
                {
                    "record_index": index,
                    "id": row.id,
                    "key": value,
                    "source": row.source,
                }
                for index, (record, row) in enumerate(records)
            ],
        }
        return data, provenance


class _GraphIndex:
    def __init__(self, spec: GraphContext):
        self.spec = spec
        self.nodes: dict[str, tuple[Any, Chunk]] = {}
        self.edges: dict[str, dict] = {}
        self.sources: dict[str, list[str | None]] = defaultdict(list)
        self.adjacency: dict[str, set[str]] = defaultdict(set)
        self.incident: dict[str, set[str]] = defaultdict(set)
        for chunk in _rows(spec.nodes, spec.node_records):
            value = resolve(chunk.data, spec.node_key)
            key = _key(value)
            if key in self.nodes:
                raise ValueError(f"Graph {spec.name!r}: duplicate node key {key}")
            self.nodes[key] = (value, chunk)
        for chunk in _rows(spec.edges, spec.edge_records):
            relation = resolve(chunk.data, spec.edge_relation)
            if not isinstance(relation, str) or not relation:
                raise ValueError("Graph edge relations must be nonempty strings")
            if relation not in spec.relations:
                continue
            source, target = (
                resolve(chunk.data, spec.edge_source),
                resolve(chunk.data, spec.edge_target),
            )
            left, right = _key(source), _key(target)
            if left not in self.nodes or right not in self.nodes:
                raise ValueError(f"Graph {spec.name!r}: edge has a missing endpoint")
            edge = {
                "source": source,
                "target": target,
                "relation": relation,
                "data": chunk.data,
            }
            edge_key = json_text(edge)
            self.edges[edge_key] = edge
            self.sources[edge_key].append(chunk.source)
            self.incident[left].add(edge_key)
            self.incident[right].add(edge_key)
            if spec.direction in ("outgoing", "both"):
                self.adjacency[left].add(edge_key)
            if spec.direction in ("incoming", "both"):
                self.adjacency[right].add(edge_key)

    def context(self, chunk: Chunk):
        spec = self.spec
        anchor = (
            chunk.id
            if spec.source_key is None
            else resolve(chunk.data, spec.source_key)
        )
        root = _key(anchor)
        exists = root in self.nodes
        if not exists and spec.missing == "error":
            raise ValueError(f"Graph {spec.name!r}: no node for {root}")
        visited = {root} if exists else set()
        frontier = set(visited)
        edge_keys: set[str] = set()
        distances = {root: 0} if exists else {}
        # Expand each reached node once. Cycles and alternate paths cannot loop.
        for depth in range(1, spec.hops + 1):
            following = set()
            for node in sorted(frontier):
                for edge_key in sorted(self.adjacency.get(node, ())):
                    edge_keys.add(edge_key)
                    if len(edge_keys) > spec.max_edges:
                        raise ValueError(f"Graph {spec.name!r}: max_edges exceeded")
                    edge = self.edges[edge_key]
                    for endpoint in (edge["source"], edge["target"]):
                        key = _key(endpoint)
                        if key not in visited:
                            visited.add(key)
                            following.add(key)
                            distances[key] = depth
                            if len(visited) > spec.max_nodes:
                                raise ValueError(
                                    f"Graph {spec.name!r}: max_nodes exceeded"
                                )
            frontier = following
            if not frontier:
                break
        # Return the complete allowed subgraph on the reached nodes, including
        # edges between boundary nodes. Direction controls reachability only.
        for node in visited:
            for key in self.incident[node]:
                edge = self.edges[key]
                if _key(edge["source"]) in visited and _key(edge["target"]) in visited:
                    edge_keys.add(key)
                    if len(edge_keys) > spec.max_edges:
                        raise ValueError(f"Graph {spec.name!r}: max_edges exceeded")
        settings = {
            "source_key": spec.source_key,
            "node_key": spec.node_key,
            "edge_source": spec.edge_source,
            "edge_target": spec.edge_target,
            "edge_relation": spec.edge_relation,
            "relations": sorted(spec.relations),
            "direction": spec.direction,
            "hops": spec.hops,
            "fields": sorted(spec.fields) if spec.fields is not None else None,
            "missing": spec.missing,
        }
        data = {
            "name": spec.name,
            "kind": "graph",
            "lookup": settings,
            "anchor": anchor,
            "missing": not exists,
            "nodes": [
                {
                    "id": self.nodes[key][0],
                    "distance": distances[key],
                    "data": _project(self.nodes[key][1].data, spec.fields),
                }
                for key in sorted(visited)
            ],
            "edges": [self.edges[key] for key in sorted(edge_keys)],
        }
        provenance = {
            "name": spec.name,
            "files": [str(spec.nodes), str(spec.edges)],
            "settings": {
                **settings,
                "node_records": spec.node_records,
                "edge_records": spec.edge_records,
            },
            "limits": {
                "max_nodes": spec.max_nodes,
                "max_edges": spec.max_edges,
                "max_bytes": spec.max_bytes,
            },
            "nodes": [
                {"id": self.nodes[key][0], "source": self.nodes[key][1].source}
                for key in sorted(visited)
            ],
            "edges": [
                {
                    "edge_index": index,
                    "source": self.edges[key]["source"],
                    "target": self.edges[key]["target"],
                    "relation": self.edges[key]["relation"],
                    "locations": sorted(self.sources[key], key=json_text),
                }
                for index, key in enumerate(sorted(edge_keys))
            ],
        }
        return data, provenance


class ContextResolver:
    """Snapshot and index reference inputs once per preview/scan iterator."""

    def __init__(self, references: list[ReferenceJoin | GraphContext]):
        self.indexes = []
        for spec in sorted(references, key=lambda s: s.name):
            spec.validate()
            self.indexes.append(
                _JoinIndex(spec)
                if isinstance(spec, ReferenceJoin)
                else _GraphIndex(spec)
            )

    def resolve(self, chunk: Chunk) -> dict | None:
        if not self.indexes:
            return None
        data, provenance = [], []
        for index in self.indexes:
            item, origin = index.context(chunk)
            size = len(json_text(item).encode("utf-8"))
            if size > index.spec.max_bytes:
                raise ValueError(
                    f"Reference {index.spec.name!r}: max_bytes exceeded ({size})"
                )
            data.append(item)
            provenance.append(origin)
        return {"data": data, "provenance": provenance}
