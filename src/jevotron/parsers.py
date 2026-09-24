"""Built-in parsers. Any callable yielding Chunk objects is also a parser."""

import csv
import gzip
import io
import json
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml

from jevotron.databases import DuckDB, SQLite, detect_database
from jevotron.models import Chunk, pointer_key, resolve


def _open_text(path: Path, encoding: str):
    opener = gzip.open if Path(path).suffix.lower() == ".gz" else open
    return opener(path, "rt", encoding=encoding, newline="")


def _entries(data: Any, records: str) -> list:
    selected = resolve(data, records)
    return selected if isinstance(selected, list) else [selected]


def _id(data: Any, id_column: str | None, fallback: str) -> str:
    if id_column is None:
        return fallback
    if (
        not isinstance(data, dict)
        or id_column not in data
        or data[id_column] in (None, "")
    ):
        raise ValueError(f"Missing identifier field {id_column!r} at {fallback}")
    return str(data[id_column])


@dataclass
class CSV:
    id_column: str | None = None
    delimiter: str = ","
    fields: list[str] | None = None
    encoding: str = "utf-8-sig"
    quotechar: str = '"'

    def __post_init__(self):
        for name in ("delimiter", "quotechar"):
            value = getattr(self, name)
            if len(value) != 1 or value in "\r\n\0":
                raise ValueError(f"{name} must be one character (not a newline or NUL)")
        if self.delimiter == self.quotechar:
            raise ValueError("delimiter and quotechar must differ")

    def __call__(self, path: Path) -> Iterable[Chunk]:
        with _open_text(path, self.encoding) as stream:
            reader = csv.DictReader(
                stream, delimiter=self.delimiter, quotechar=self.quotechar, strict=True
            )
            names = reader.fieldnames
            if (
                not names
                or any(not name for name in names)
                or len(set(names)) != len(names)
            ):
                raise ValueError(
                    f"{path}: CSV requires distinct, nonempty column names"
                )
            if self.id_column is not None and self.id_column not in names:
                raise ValueError(
                    f"{path}: Missing identifier column {self.id_column!r}"
                )
            for index, row in enumerate(reader, 1):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(
                        f"{path}:{reader.line_num}: CSV row has wrong number of cells"
                    )
                yield Chunk(
                    _id(row, self.id_column, str(index)),
                    row,
                    self.fields,
                    f"{path}:row:{index}",
                )


class _YamlLoader(yaml.SafeLoader):
    def construct_document(self, node):
        self._validated_mappings = set()
        try:
            return super().construct_document(node)
        finally:
            self._validated_mappings.clear()

    def flatten_mapping(self, node):
        # PyYAML flattens merge sources recursively, including sources that never
        # pass through _mapping. Check their original pairs before it mutates them.
        if node not in self._validated_mappings:
            explicit = set()
            for key_node, _ in node.value:
                if key_node.tag == "tag:yaml.org,2002:merge":
                    continue
                key = self.construct_object(key_node)
                if not isinstance(key, str):
                    raise ValueError("YAML object keys must be strings")
                if key in explicit:
                    raise ValueError(
                        f"Duplicate YAML key {key!r} "
                        f"at line {key_node.start_mark.line + 1}"
                    )
                explicit.add(key)
            self._validated_mappings.add(node)
        super().flatten_mapping(node)


def _mapping(loader: _YamlLoader, node: yaml.MappingNode) -> dict:
    # Standard YAML merge overrides are intentional, not duplicate source keys.
    loader.flatten_mapping(node)
    result = loader.construct_mapping(node)
    if any(not isinstance(key, str) for key in result):
        raise ValueError("YAML object keys must be strings")
    return result


_YamlLoader.add_constructor("tag:yaml.org,2002:map", _mapping)
# Keep dates as text, so parsed entries are JSON-compatible without lossy coercion.
_YamlLoader.add_constructor(
    "tag:yaml.org,2002:timestamp", yaml.SafeLoader.construct_scalar
)


@dataclass
class YAML:
    id_column: str | None = None
    fields: list[str] | None = None
    encoding: str = "utf-8-sig"
    records: str = ""

    def __call__(self, path: Path) -> Iterable[Chunk]:
        with _open_text(path, self.encoding) as stream:
            for doc_index, data in enumerate(
                yaml.load_all(stream, Loader=_YamlLoader), 1
            ):
                if data is None:
                    continue
                entries = _entries(data, self.records)
                for index, entry in enumerate(entries, 1):
                    fallback = f"{doc_index}:{index}"
                    yield Chunk(
                        _id(entry, self.id_column, fallback),
                        entry,
                        self.fields,
                        f"{path}:document:{doc_index}:entry:{index}",
                    )


def _json_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


@dataclass
class JSON:
    id_column: str | None = None
    fields: list[str] | None = None
    encoding: str = "utf-8-sig"
    records: str = ""

    def __call__(self, path: Path) -> Iterable[Chunk]:
        with _open_text(path, self.encoding) as stream:
            data = json.load(stream, object_pairs_hook=_json_object)
        for index, entry in enumerate(_entries(data, self.records), 1):
            yield Chunk(
                _id(entry, self.id_column, str(index)),
                entry,
                self.fields,
                f"{path}:entry:{index}",
            )


@dataclass
class JSONL:
    id_column: str | None = None
    fields: list[str] | None = None
    encoding: str = "utf-8-sig"

    def __call__(self, path: Path) -> Iterable[Chunk]:
        with _open_text(path, self.encoding) as stream:
            for line, text in enumerate(stream, 1):
                if text.strip():
                    entry = json.loads(text, object_pairs_hook=_json_object)
                    yield Chunk(
                        _id(entry, self.id_column, str(line)),
                        entry,
                        self.fields,
                        f"{path}:{line}",
                    )


def _obo_lines(stream: Iterable[str]) -> Iterable[tuple[int, str]]:
    text = ""
    start = 1
    continued = False
    for line, raw in enumerate(stream, 1):
        if not continued:
            start = line
        raw = raw.rstrip("\r\n")
        trailing = len(raw) - len(raw.rstrip("\\"))
        if trailing % 2:
            text += raw[:-1]
            continued = True
        else:
            yield start, text + raw
            text = ""
            continued = False
    if continued:
        raise ValueError(f"Unfinished OBO line continuation at line {start}")


@dataclass
class OBO:
    """Stanza tokenizer; preserves raw tag values, including comments and escapes.

    This deliberately does not interpret OBO axioms or walk the ontology graph.
    Every tag maps to a list, even if it occurs only once.
    """

    encoding: str = "utf-8-sig"
    stanza: str = ""

    def __call__(self, path: Path) -> Iterable[Chunk]:
        data: dict[str, Any] | None = None
        start = 0

        def chunk() -> Chunk:
            assert data is not None
            identifier = data.get("id", [f"stanza:{start}"])[0]
            paths = [
                f"{pointer_key(tag)}/{i}"
                for tag, values in data.items()
                if tag != "_stanza"
                for i in range(len(values))
            ]
            return Chunk(identifier, data, paths, f"{path}:{start}")

        with _open_text(path, self.encoding) as stream:
            for line, text in _obo_lines(stream):
                text = text.strip()
                if not text or text.startswith("!"):
                    continue
                if text.startswith("["):
                    if not text.endswith("]") or len(text) < 3:
                        raise ValueError(f"{path}:{line}: Invalid OBO stanza header")
                    if data is not None and (
                        not self.stanza or data["_stanza"] == self.stanza
                    ):
                        yield chunk()
                    data = {"_stanza": text[1:-1]}
                    start = line
                elif data is not None:
                    tag, sep, value = text.partition(":")
                    tag = tag.strip()
                    if not sep or not tag or tag == "_stanza":
                        raise ValueError(f"{path}:{line}: Invalid OBO tag-value line")
                    data.setdefault(tag, []).append(value.strip())
            if data is not None and (not self.stanza or data["_stanza"] == self.stanza):
                yield chunk()


def _toml_values(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _toml_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_toml_values(item) for item in value]
    return value


@dataclass
class TOML:
    id_column: str | None = None
    fields: list[str] | None = None
    encoding: str = "utf-8-sig"
    records: str = ""

    def __call__(self, path: Path) -> Iterable[Chunk]:
        with _open_text(path, self.encoding) as stream:
            data = _toml_values(tomllib.loads(stream.read()))
        for index, entry in enumerate(_entries(data, self.records), 1):
            yield Chunk(
                _id(entry, self.id_column, str(index)),
                entry,
                self.fields,
                f"{path}:entry:{index}",
            )


@dataclass
class Text:
    """Plain text, including Markdown; no heading or code-fence interpretation."""

    split: str = "paragraphs"
    encoding: str = "utf-8-sig"

    def __post_init__(self):
        if self.split not in ("paragraphs", "lines", "file"):
            raise ValueError("split must be paragraphs, lines, or file")

    def __call__(self, path: Path) -> Iterable[Chunk]:
        with _open_text(path, self.encoding) as stream:
            if self.split == "file":
                content = stream.read()
                if content.strip():
                    yield Chunk("1", {"text": content}, source=f"{path}:1")
                return
            pending = []
            start = 1
            for line, raw in enumerate(stream, 1):
                if self.split == "lines":
                    if raw.strip():
                        yield Chunk(
                            str(line),
                            {"text": raw.rstrip("\r\n")},
                            source=f"{path}:{line}",
                        )
                elif raw.strip():
                    if not pending:
                        start = line
                    pending.append(raw)
                elif pending:
                    yield Chunk(
                        str(start),
                        {"text": "".join(pending).rstrip("\r\n")},
                        source=f"{path}:{start}",
                    )
                    pending = []
            if pending:
                yield Chunk(
                    str(start),
                    {"text": "".join(pending).rstrip("\r\n")},
                    source=f"{path}:{start}",
                )


@dataclass
class FASTA:
    encoding: str = "utf-8-sig"

    def __call__(self, path: Path) -> Iterable[Chunk]:
        header = None
        sequence = []
        start = 0

        def chunk():
            parts = header.split(maxsplit=1)
            if not sequence:
                raise ValueError(f"{path}:{start}: FASTA record has no sequence")
            return Chunk(
                parts[0],
                {
                    "id": parts[0],
                    "description": parts[1] if len(parts) > 1 else "",
                    "sequence": "".join(sequence),
                },
                source=f"{path}:{start}",
            )

        with _open_text(path, self.encoding) as stream:
            for line, raw in enumerate(stream, 1):
                text = raw.strip()
                if not text:
                    continue
                if text.startswith(">"):
                    if header is not None:
                        yield chunk()
                    header = text[1:].strip()
                    if not header:
                        raise ValueError(f"{path}:{line}: Empty FASTA header")
                    sequence = []
                    start = line
                elif header is None:
                    raise ValueError(f"{path}:{line}: FASTA sequence before header")
                else:
                    sequence.append("".join(text.split()))
            if header is not None:
                yield chunk()


@dataclass
class GMT:
    encoding: str = "utf-8-sig"

    def __call__(self, path: Path) -> Iterable[Chunk]:
        with _open_text(path, self.encoding) as stream:
            for line, raw in enumerate(stream, 1):
                if not raw.strip():
                    continue
                parts = raw.rstrip("\r\n").split("\t")
                if len(parts) < 3 or not parts[0] or any(not p for p in parts[2:]):
                    raise ValueError(
                        f"{path}:{line}: GMT needs a name, description, and nonempty members"
                    )
                yield Chunk(
                    parts[0],
                    {"name": parts[0], "description": parts[1], "members": parts[2:]},
                    source=f"{path}:{line}",
                )


@dataclass(frozen=True)
class FormatOption:
    name: str
    default: str
    help: str


@dataclass(frozen=True)
class FormatSpec:
    name: str
    extensions: tuple[str, ...]
    factory: Callable
    summary: str
    options: tuple[FormatOption, ...] = ()
    text: bool = True

    @property
    def all_options(self) -> tuple[FormatOption, ...]:
        return (ENCODING, *self.options) if self.text else self.options


ENCODING = FormatOption(
    "encoding", "utf-8-sig", "Text encoding; UTF-8 with optional BOM by default."
)
RECORDS = FormatOption(
    "records",
    "",
    "JSON Pointer to records before chunking; empty selects the document root.",
)
QUOTECHAR = FormatOption("quotechar", '"', "Single quoting character for cells.")

# This small catalog drives detection, CLI help, and the documentation reference.
# Local extensions still use ordinary callables through Config(parser=...).
FORMATS = (
    FormatSpec(
        "sqlite",
        (".sqlite", ".sqlite3", ".db", ".db3"),
        SQLite,
        "Each row of every user table; columns are fields. Detected by file header.",
        text=False,
    ),
    FormatSpec(
        "duckdb",
        (".duckdb", ".ddb"),
        DuckDB,
        "Each row of every user table; columns are fields. Detected by file header.",
        text=False,
    ),
    FormatSpec(
        "csv",
        (".csv",),
        CSV,
        "One row; each column is a field.",
        (
            FormatOption(
                "delimiter", ",", "Single cell separator; use tab for a tab character."
            ),
            QUOTECHAR,
        ),
    ),
    FormatSpec(
        "tsv",
        (".tsv",),
        CSV,
        "One row; each column is a field.",
        (
            FormatOption(
                "delimiter", "\t", "Single cell separator; use tab for a tab character."
            ),
            QUOTECHAR,
        ),
    ),
    FormatSpec(
        "yaml",
        (".yaml", ".yml", ".yamll"),
        YAML,
        "Each list item, or one document; top-level keys are fields.",
        (RECORDS,),
    ),
    FormatSpec(
        "json",
        (".json",),
        JSON,
        "Each array item, or one document; top-level keys are fields.",
        (RECORDS,),
    ),
    FormatSpec(
        "jsonl",
        (".jsonl", ".ndjson"),
        JSONL,
        "Each nonblank line; top-level keys are fields.",
    ),
    FormatSpec(
        "toml",
        (".toml",),
        TOML,
        "One document by default; top-level keys are fields.",
        (RECORDS,),
    ),
    FormatSpec(
        "obo",
        (".obo",),
        OBO,
        "One stanza; each tag occurrence is a field.",
        (
            FormatOption(
                "stanza",
                "",
                "Only this stanza type, e.g. Term; empty includes all types.",
            ),
        ),
    ),
    FormatSpec(
        "text",
        (".txt", ".text", ".md", ".markdown"),
        Text,
        "One paragraph; /text is the field.",
        (FormatOption("split", "paragraphs", "Chunk by paragraphs, lines, or file."),),
    ),
    FormatSpec(
        "textlines",
        (".textlines", ".log"),
        Text,
        "One nonblank line; /text is the field.",
        (FormatOption("split", "lines", "Chunk by paragraphs, lines, or file."),),
    ),
    FormatSpec(
        "fasta",
        (".fasta", ".fa", ".fna", ".faa"),
        FASTA,
        "One sequence record; id, description, and sequence are fields.",
    ),
    FormatSpec(
        "gmt",
        (".gmt",),
        GMT,
        "One named set per line; name, description, and members are fields.",
    ),
)


def format_spec(name: str) -> FormatSpec:
    for spec in FORMATS:
        if spec.name == name.lower():
            return spec
    raise ValueError(
        f"Unknown format {name!r}; run 'jevotron formats' for supported formats"
    )


def for_path(
    path: Path, format: str | None = None, options: dict[str, str] | None = None
):
    """Infer a built-in parser, or select one explicitly, with validated options."""
    path = Path(path)
    if format is None:
        format = detect_database(path)
    if format is None:
        suffix = (
            path.with_suffix("") if path.suffix.lower() == ".gz" else path
        ).suffix.lower()
        format = next((s.name for s in FORMATS if suffix in s.extensions), None)
    if format is None:
        raise ValueError(
            "Unknown file format; use --format, run 'jevotron formats', "
            "or supply a parser in --config"
        )
    spec = format_spec(format)
    if not spec.text and path.suffix.lower() == ".gz" and detect_database(path) is None:
        raise ValueError(
            "Compressed databases are not supported; decompress the file first"
        )
    values = {o.name: o.default for o in spec.all_options}
    for key, value in (options or {}).items():
        if key not in values:
            raise ValueError(
                f"Unknown option {key!r} for {spec.name}; supported: {', '.join(values)}"
            )
        if not isinstance(value, str):
            raise ValueError(f"Format option {key!r} must be a string")
        values[key] = value
    if values.get("delimiter") in ("tab", "\\t"):
        values["delimiter"] = "\t"
    if spec.text:
        try:
            io.TextIOWrapper(io.BytesIO(), encoding=values["encoding"]).close()
        except LookupError as exc:
            raise ValueError(f"Unknown text encoding: {values['encoding']!r}") from exc
    return spec.factory(**values)
