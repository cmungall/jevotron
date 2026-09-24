"""The small public contract shared by parsers and the runner."""

import json
import re
from dataclasses import asdict, dataclass
from typing import Any


def json_text(value: Any) -> str:
    """Canonical JSON for both request identity and portable output."""
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def pointer_key(key: str) -> str:
    return "/" + key.replace("~", "~0").replace("/", "~1")


def resolve(data: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer; the empty pointer selects the root."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError(f"Invalid JSON Pointer: {pointer!r}")
    value = data
    for part in pointer.split("/")[1:] if pointer else []:
        if re.search(r"~(?![01])", part):
            raise ValueError(f"Invalid JSON Pointer escape: {pointer!r}")
        key = part.replace("~1", "/").replace("~0", "~")
        try:
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", key):
                    raise ValueError
                value = value[int(key)]
            elif isinstance(value, dict):
                value = value[key]
            else:
                raise ValueError
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError(f"Field does not exist: {pointer!r}") from exc
    return value


def validate_json(value: Any) -> None:
    """Reject lossy conversions, non-string keys, NaN, and custom objects."""
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Chunk data must have string object keys")
        for item in value.values():
            validate_json(item)
    elif isinstance(value, list):
        for item in value:
            validate_json(item)
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"Expected JSON-compatible data, got {type(value).__name__}")
    json_text(value)


@dataclass
class Chunk:
    id: str
    data: Any
    fields: list[str] | None = None
    source: str | None = None

    def field_paths(self) -> list[str]:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Chunk.id must be a nonempty string")
        if self.source is not None and not isinstance(self.source, str):
            raise ValueError("Chunk.source must be a string or None")
        if self.fields is not None and not isinstance(self.fields, list):
            raise ValueError("Chunk.fields must be a list of JSON Pointer strings")
        validate_json(self.data)
        if self.fields is None:
            paths = (
                [pointer_key(key) for key in self.data]
                if isinstance(self.data, dict)
                else [""]
            )
        else:
            paths = list(self.fields)
        if not paths or any(not isinstance(p, str) for p in paths):
            raise ValueError(f"Chunk {self.id!r} must have at least one field path")
        if len(set(paths)) != len(paths):
            raise ValueError(f"Chunk {self.id!r} contains duplicate field paths")
        for path in paths:
            resolve(self.data, path)
        return paths


@dataclass
class FieldResult:
    path: str
    value: Any
    label: str
    probabilities: dict[str, float]
    confidence: float
    score: float


@dataclass
class Result:
    id: str
    source: str | None
    label: str
    score: float
    warning: bool
    fields: list[FieldResult]
    model: str
    assessed_at: str
    request_hash: str
    cached: bool
    usage: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
