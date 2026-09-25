"""Run with `uv run python -m examples.classified_feeds --help`."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from jevotron.extensions.feeds import storage
from jevotron.extensions.feeds.storage import atomic
from jevotron.models import json_text

from .bluesky import normalize
from .mail import normalize_thread, proposed_labels
from .pipeline import digest, make_preview, rank, render

ROOT = Path(".jevotron/classified-feeds")
FIXTURES = Path(__file__).parent / "fixtures"
INTERESTS = (
    "Open science, bioinformatics, ontologies, and open-source research software"
)


def write_outputs(
    folder: Path, records: list[dict], interests: str, now: str, manifest: dict
):
    active = [record for record in records if record["status"] == "active"]
    previews = [make_preview(record, interests) for record in active]
    queue = rank(active, now)
    outputs = {
        "records.jsonl": records,
        "preview.jsonl": previews,
        "queue.jsonl": queue,
        "mail-proposals.jsonl": [
            proposed_labels(record, {})
            for record in active
            if record["source"] == "synthetic-mail"
        ],
    }
    hashes = {}
    for name, rows in outputs.items():
        atomic(folder / name, "".join(json_text(row) + "\n" for row in rows))
        hashes[name] = digest(rows)
    atomic(folder / "digest.html", render(queue))
    manifest.update(
        observed_at=now,
        records=len(records),
        active=len(active),
        inference="not_run",
        inference_quality="untested",
        output_hashes=hashes,
    )
    atomic(folder / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    return manifest


def run(args):
    now = datetime.now(timezone.utc).isoformat()
    if args.command == "synthetic":
        folder = ROOT / "synthetic"
    else:
        result = storage.collect_source(
            root=ROOT,
            actor=args.actor,
            query=args.query,
            interests=args.interests,
            limit=getattr(args, "limit", 10),
            pages=getattr(args, "pages", 1),
            resume=getattr(args, "resume", False),
            recheck=args.command == "recheck",
        )
        print(json.dumps(result, indent=2))
        return
    folder.mkdir(parents=True, exist_ok=True)
    # Fail on concurrent writers; a stale lock after process death needs manual removal.
    lock = folder / ".lock"
    with lock.open("x") as stream:
        stream.write(str(os.getpid()))
    try:
        if args.command == "synthetic":
            fixture = json.loads((FIXTURES / "bluesky.json").read_text())
            records = [normalize(item, now, {}) for item in fixture["feed"]]
            for record in records:
                record["source"] = "synthetic-bluesky"
            records += [
                normalize_thread(thread)
                for thread in json.loads((FIXTURES / "mail.json").read_text())
            ]
            result = write_outputs(
                folder, records, args.interests, now, {"provenance": "synthetic"}
            )
        print(json.dumps({"folder": str(folder), **result}, indent=2))
    finally:
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(
        description="Bounded local previews; no inference or Gmail access"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("collect", "recheck", "synthetic"):
        command = commands.add_parser(name)
        command.add_argument("--interests", default=INTERESTS)
        if name != "synthetic":
            source = command.add_mutually_exclusive_group(required=True)
            source.add_argument("--actor")
            source.add_argument("--query")
        if name == "collect":
            command.add_argument("--limit", type=int, choices=range(1, 26), default=10)
            command.add_argument("--pages", type=int, choices=range(1, 4), default=1)
            command.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
    except (ValueError, RuntimeError, OSError, KeyError, httpx.HTTPError) as exc:
        parser.exit(1, f"Preview failed: {exc}\n")


if __name__ == "__main__":
    main()
