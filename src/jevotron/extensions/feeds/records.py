"""Provider-independent normalized envelope with explicit adapter validation."""

from jevotron.models import validate_json

from .adapters import adapter_for_source
from .common import timestamp


def require_text(record: dict, name: str, *, empty: bool = False):
    value = record.get(name)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(
            f"Record {name} must be {'possibly empty' if empty else 'nonempty'} text"
        )


def validate_record(record: dict):
    if not isinstance(record, dict):
        raise ValueError("Expected a normalized record object")
    validate_json(record)
    for name in ("id", "source", "status"):
        require_text(record, name)
    adapter = adapter_for_source(record["source"])
    if "observed_at" in record:
        require_text(record, "observed_at")
        timestamp(record["observed_at"])
    if record["status"] == "unavailable":
        if set(record) - {"id", "source", "status", "observed_at"}:
            raise ValueError("Unavailable records must be content-free tombstones")
    elif record["status"] == "active":
        for name in ("version", "author", "text", "created_at"):
            require_text(record, name, empty=name == "text")
        timestamp(record["created_at"])
        if "context" in record and not isinstance(record["context"], dict):
            raise ValueError("Record context must be an object")
    else:
        raise ValueError("Record status must be active or unavailable")
    adapter.load().validate_record(record)
