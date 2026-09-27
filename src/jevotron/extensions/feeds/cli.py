"""Optional installed feed commands; importing the main CLI does not load this module."""

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import httpx

from jevotron.classification import classify, decode_choices
from jevotron.models import json_text

from .adapters import ADAPTERS, get_adapter
from .common import digest, timestamp
from .preset import INTERESTS, classification_input, make_preview, research_feed_spec
from .ranking import rank, render
from .storage import atomic, load_records, locked, read_jsonl


def bounded(low, high):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("Expected an integer") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"Expected {low}–{high}")
        return number

    return parse


def protect(destination: Path | None, sources: list[Path]):
    if destination is not None and any(
        destination.resolve() == source.resolve()
        or (destination.exists() and source.exists() and destination.samefile(source))
        for source in sources
    ):
        raise ValueError("Output/cache must not overwrite an input or another output")


def write_jsonl(rows, destination):
    content = "".join(json_text(row) + "\n" for row in rows)
    if destination:
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic(destination, content)
    else:
        sys.stdout.write(content)


def bound_assessments(records: list[dict], path: Path, interests: str) -> dict:
    """Validate serialized choices and bind them to current content/rubric hashes."""
    current = {r["id"]: r for r in records if r["status"] == "active"}
    decoded = {}
    for result in read_jsonl(path):
        uri = result["id"]
        if uri not in current or uri in decoded:
            raise ValueError(
                "Assessment has an unknown, unavailable or duplicate record ID"
            )
        if result.get("source") != current[uri]["source"]:
            raise ValueError("Assessment source does not match the current record")
        preview = make_preview(current[uri], interests)
        if result["request_hash"] != preview["request_hash"]:
            raise ValueError(
                "Stale assessment: record/context, interests, model or rubric changed"
            )
        response = {
            "model": result["model"],
            "answers": {
                name: {
                    "type": "choice",
                    "choice": answer["label"],
                    "probabilities": answer["probabilities"],
                    "confidence": answer["confidence"],
                }
                for name, answer in result["answers"].items()
            },
        }
        # Recompute review decisions locally; don't trust a serialized uncertain flag.
        decoded[uri] = decode_choices(response, preview["request"])
    return decoded


def parser():
    app = argparse.ArgumentParser(
        prog="jevotron-feeds",
        description="Optional experimental research feeds: bounded public collection and local review",
    )
    commands = app.add_subparsers(dest="command", required=True)
    commands.add_parser("presets", help="List shipped classification presets (offline)")
    commands.add_parser(
        "adapters", help="List source adapters and dependencies (offline)"
    )
    for name in ("collect", "recheck"):
        command = commands.add_parser(
            name, help="Bounded public Bluesky GETs; no inference"
        )
        command.add_argument(
            "--adapter",
            choices=[adapter.name for adapter in ADAPTERS if adapter.collects],
            default="bluesky",
            help="Collection adapter (default: bluesky); local files use preview/digest",
        )
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--actor", help="Selected public account handle or DID")
        source.add_argument(
            "--query", help="Public search (may be unavailable without auth)"
        )
        command.add_argument("--root", type=Path, default=Path(".jevotron/feeds"))
        command.add_argument("--interests", default=INTERESTS)
        if name == "collect":
            command.add_argument(
                "--limit",
                type=bounded(1, 25),
                default=10,
                help="Posts per page, 1–25 (default 10)",
            )
            command.add_argument(
                "--pages", type=bounded(1, 3), default=1, help="Pages, 1–3 (default 1)"
            )
            command.add_argument(
                "--resume", action="store_true", help="Continue saved historical cursor"
            )
    for name in ("preview", "classify", "digest"):
        command = commands.add_parser(
            name,
            help={
                "preview": "Print exact requests locally (offline)",
                "classify": "Classify normalized records; cache-only unless --execute is supplied",
                "digest": "Rank all active records with optional verified assessments (offline)",
            }[name],
        )
        command.add_argument(
            "file",
            type=Path,
            help="Normalized feed records JSONL, at most 200 records / 16 MB",
        )
        command.add_argument(
            "--preset", choices=["research-feed"], default="research-feed"
        )
        command.add_argument("--interests", default=INTERESTS)
        command.add_argument(
            "--output",
            "-o",
            type=Path,
            required=name == "digest",
            help="Output JSONL file; digest takes a directory",
        )
        if name != "digest":
            command.add_argument(
                "--limit",
                type=bounded(1, 200),
                default=3 if name == "preview" else 10,
                help="Maximum active records, 1–200",
            )
        if name == "classify":
            command.add_argument(
                "--execute",
                action="store_true",
                help="Allow paid TypeSafe calls on cache misses",
            )
            command.add_argument(
                "--cache",
                type=Path,
                default=Path(".jevotron/feeds/classification.sqlite3"),
            )
            command.add_argument(
                "--refresh",
                action="store_true",
                help="Replace cached answers; requires --execute on misses",
            )
        if name == "digest":
            command.add_argument(
                "--assessments",
                type=Path,
                help="Saved classification JSONL; stale hashes fail",
            )
            command.add_argument(
                "--as-of",
                help="Timezone-aware timestamp for reproducible recency ranking",
            )
    return app


def run(args):
    if args.command == "adapters":
        for adapter in ADAPTERS:
            print(
                f"{adapter.name}: {adapter.description}; sources={','.join(adapter.sources)}"
            )
        return
    if args.command == "presets":
        print(
            "research-feed (experimental): topic, relevance, content_kind, actionability"
        )
        return
    if args.command in {"collect", "recheck"}:
        result = (
            get_adapter(args.adapter)
            .load()
            .collect_source(
                root=args.root,
                actor=args.actor,
                query=args.query,
                interests=args.interests,
                limit=getattr(args, "limit", 10),
                pages=getattr(args, "pages", 1),
                resume=getattr(args, "resume", False),
                recheck=args.command == "recheck",
            )
        )
        print(json.dumps(result, indent=2))
        return
    records = load_records(args.file)
    active = [r for r in records if r["status"] == "active"]
    if args.command in {"preview", "classify"}:
        protect(args.output, [args.file])
        if args.output:
            if args.output.exists() and not args.output.is_file():
                raise ValueError("Preview/classification output must be a file")
            args.output.parent.mkdir(parents=True, exist_ok=True)
        active = active[: args.limit]
        if args.command == "preview":
            write_jsonl([make_preview(r, args.interests) for r in active], args.output)
            return
        protect(args.cache, [args.file])
        protect(args.output, [args.cache])
        with closing(
            classify(
                [classification_input(r, args.interests) for r in active],
                research_feed_spec(),
                cache=args.cache,
                refresh=args.refresh,
                allow_network=args.execute,
            )
        ) as results:
            # At most 200 results. Publish complete output only after success;
            # partial successes remain in the cache for a later invocation.
            rows = [result.to_dict() for result in results]
        write_jsonl(rows, args.output)
        print(
            f"Classified {len(rows)} records ({sum(r['cached'] for r in rows)} cached).",
            file=sys.stderr,
        )
        return
    now = args.as_of or datetime.now(timezone.utc).isoformat()
    timestamp(now)
    assessments = (
        bound_assessments(records, args.assessments, args.interests)
        if args.assessments
        else {}
    )
    queue = rank(records, now, assessments)
    sources = [args.file] + ([args.assessments] if args.assessments else [])
    for name in ("queue.jsonl", "digest.html", "manifest.json", ".lock"):
        protect(args.output / name, sources)
    with locked(args.output):
        write_jsonl(queue, args.output / "queue.jsonl")
        atomic(args.output / "digest.html", render(queue))
        manifest = {
            "preset": args.preset,
            "interests": args.interests,
            "as_of": now,
            "records": len(records),
            "active": len(queue),
            "classified": len(assessments),
            "unassessed": len(queue) - len(assessments),
            "queue_hash": digest(queue),
        }
        atomic(args.output / "manifest.json", json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"folder": str(args.output), **manifest}, indent=2))


def main(argv: list[str] | None = None) -> int:
    app = parser()
    args = app.parse_args(argv)
    try:
        run(args)
        return 0
    except KeyboardInterrupt:
        print("Interrupted; successful classifications remain cached.", file=sys.stderr)
        return 130
    except (
        ValueError,
        OSError,
        RuntimeError,
        KeyError,
        TypeError,
        sqlite3.Error,
        httpx.HTTPError,
    ) as exc:
        print(f"Feed command failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
