"""Bounded local feed state. No inference, credentials or mail access."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from jevotron.models import json_text

from .common import digest
from .preset import make_preview
from .ranking import rank, render
from .records import validate_record

MAX_RECORDS = 200
MAX_FILE_BYTES = 16_000_000


def atomic(path: Path, content: str):
    temp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temp = Path(stream.name)
            stream.write(content)
        # Close before replacement, including on platforms that lock open files.
        temp.replace(path)
    finally:
        if temp:
            temp.unlink(missing_ok=True)


@contextmanager
def locked(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ".lock"
    with path.open("x") as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        path.unlink()


def read_jsonl(path: Path) -> list[dict]:
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("Feed input exceeds the 16 MB local-file bound")
    lines = [line for line in data.decode("utf-8").splitlines() if line.strip()]
    if len(lines) > MAX_RECORDS:
        raise ValueError("Feed input exceeds the 200-record bound")
    return [json.loads(line) for line in lines]


def load_records(path: Path) -> list[dict]:
    records = read_jsonl(path)
    seen = set()
    for record in records:
        validate_record(record)
        if record["id"] in seen:
            raise ValueError(
                "Duplicate record ID; merge source versions before classifying"
            )
        seen.add(record["id"])
    return records


def write_snapshot(
    folder: Path, records: list[dict], interests: str, now: str, metadata: dict
):
    for record in records:
        validate_record(record)
    active = [r for r in records if r["status"] == "active"]
    queue = rank(active, now)
    outputs = {
        "records.jsonl": records,
        "preview.jsonl": [make_preview(r, interests) for r in active],
        "queue.jsonl": queue,
    }
    for name, rows in outputs.items():
        atomic(folder / name, "".join(json_text(row) + "\n" for row in rows))
    atomic(folder / "digest.html", render(queue))
    manifest = {
        **metadata,
        "observed_at": now,
        "records": len(records),
        "active": len(active),
        "preset": "research-feed",
        "interests": interests,
        "inference": "not_run",
        "output_hashes": {name: digest(rows) for name, rows in outputs.items()},
    }
    atomic(folder / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


def collect_source(**kwargs):
    """Compatibility entry point for the original Bluesky example."""
    from .adapters import get_adapter

    return get_adapter("bluesky").load().collect_source(**kwargs)
