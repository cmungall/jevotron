"""Terminal and scriptable commands for offline human review."""

import csv
import json
import sqlite3
from enum import Enum
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
import yaml

from jevotron.models import json_text
from jevotron.review import ReviewStore, protect_store, read_report

app = typer.Typer(
    help="Review fields offline, retain decisions, and select exemplars.",
    no_args_is_help=True,
)
Store = Annotated[
    Path,
    typer.Option(
        help="Separate SQLite store for human reviews; use one per dataset/project."
    ),
]
DEFAULT_STORE = Path(".jevotron/reviews.sqlite3")


class Decision(str, Enum):
    confirmed_error = "confirmed-error"
    valid_exception = "valid-exception"
    deferred = "deferred"


def checked(function):
    @wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (
            ValueError,
            OSError,
            sqlite3.Error,
            yaml.YAMLError,
            csv.Error,
            KeyError,
            TypeError,
        ) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(1) from exc
        except (KeyboardInterrupt, typer.Abort):
            typer.echo("Review interrupted; recorded decisions remain saved.", err=True)
            raise typer.Exit(130) from None

    return call


@app.command("import")
@checked
def import_command(
    report: Annotated[Path, typer.Argument(help="JSONL scan report to import.")],
    store: Store = DEFAULT_STORE,
    source: Annotated[
        Path | None,
        typer.Option(
            help="Original data for legacy reports missing full entry context."
        ),
    ] = None,
    config: Annotated[
        Path | None,
        typer.Option(
            help="Original scan config, used with --source for matching requests."
        ),
    ] = None,
    cache: Annotated[
        Path | None,
        typer.Option(
            help="Read original requests from this model cache without changing it."
        ),
    ] = None,
):
    """Import assessed fields; unchanged request hashes retain their reviews."""
    protect_store(store, report, source, config, cache, Path(".jevotron/cache.sqlite3"))
    rows = read_report(report, source=source, config=config, cache=cache)
    with ReviewStore(store) as queue:
        count = queue.import_results(rows)
    typer.echo(f"Imported {count} assessed fields into {store}.", err=True)


@app.command("list")
@checked
def list_command(
    store: Store = DEFAULT_STORE,
    unreviewed: Annotated[
        bool,
        typer.Option(
            help="Only unreviewed, deferred, or changed fields needing reconsideration."
        ),
    ] = False,
    warnings_only: Annotated[
        bool, typer.Option(help="Only fields belonging to warning entries.")
    ] = False,
    history: Annotated[
        bool, typer.Option(help="Also include superseded assessments.")
    ] = False,
):
    """Print queue items as JSONL, including full entries and assessment provenance."""
    with ReviewStore(store) as queue:
        for item in queue.items(
            unreviewed=unreviewed, warnings_only=warnings_only, history=history
        ):
            typer.echo(json_text(item))


@app.command("decide")
@checked
def decide_command(
    entry_id: str,
    field: Annotated[
        str, typer.Argument(help="Exact JSON Pointer; use '' for the whole entry.")
    ],
    decision: Decision,
    store: Store = DEFAULT_STORE,
    note: str = "",
    request_hash: Annotated[
        str | None,
        typer.Option(
            help="Require this current request hash, preventing a decision on a changed queue item."
        ),
    ] = None,
):
    """Record a decision on the current entry/field and print its durable decision ID."""
    with ReviewStore(store) as queue:
        result = queue.decide(
            entry_id, field, decision.value, note=note, expected_hash=request_hash
        )
    typer.echo(json_text(result))


@app.command("start")
@checked
def start_command(
    store: Store = DEFAULT_STORE,
    unreviewed: Annotated[
        bool,
        typer.Option(
            help="Only unreviewed, deferred, or changed fields needing reconsideration."
        ),
    ] = False,
    warnings_only: Annotated[
        bool, typer.Option(help="Only fields belonging to warning entries.")
    ] = False,
):
    """Review each field interactively: confirm error, accept exception, defer, skip, or quit."""
    choices = {"c": "confirmed-error", "v": "valid-exception", "d": "deferred"}
    with ReviewStore(store) as queue:
        items = queue.items(unreviewed=unreviewed, warnings_only=warnings_only)
        if not items:
            typer.echo("No matching fields need review.", err=True)
        for index, item in enumerate(items, 1):
            typer.echo(
                f"\n[{index}/{len(items)}] {item['entry_id']} {item['field_path']!r} — {item['status']}",
                err=True,
            )
            typer.echo(
                f"Model: {item['model']} | Assessed: {item['assessed_at']}\nRequest: {item['request_hash']}\nSource: {item['source']}",
                err=True,
            )
            typer.echo(
                "Full entry:\n"
                + json.dumps(item["entry"], ensure_ascii=False, indent=2),
                err=True,
            )
            if "context" in item:
                typer.echo(
                    "Assessment context:\n"
                    + json.dumps(item["context"], ensure_ascii=False, indent=2),
                    err=True,
                )
            typer.echo(
                "Field assessment:\n"
                + json.dumps(item["assessment"], ensure_ascii=False, indent=2),
                err=True,
            )
            if item["review"]:
                typer.echo("Previous decision: " + json_text(item["review"]), err=True)
            while True:
                answer = (
                    typer.prompt(
                        "[c] confirmed error, [v] valid exception, [d] defer, [s] skip, [q] quit",
                        err=True,
                    )
                    .strip()
                    .lower()
                )
                if answer in {*choices, "s", "q"}:
                    break
                typer.echo("Choose c, v, d, s, or q.", err=True)
            if answer == "q":
                return
            if answer == "s":
                continue
            note = typer.prompt(
                "Reviewer note (optional)", default="", show_default=False, err=True
            )
            result = queue.decide(
                item["entry_id"],
                item["field_path"],
                choices[answer],
                note=note,
                expected_hash=item["request_hash"],
            )
            typer.echo(json_text(result))


@app.command("export")
@checked
def export_command(store: Store = DEFAULT_STORE):
    """Export append-only human decision history as JSONL to stdout."""
    with ReviewStore(store) as queue:
        for decision in queue.decisions():
            typer.echo(json_text(decision))


@app.command("exemplars")
@checked
def exemplars_command(
    decision: Annotated[
        list[int],
        typer.Option(
            "--decision",
            help="Explicit decision ID to include; repeat for multiple selections.",
        ),
    ],
    store: Store = DEFAULT_STORE,
    normal_label: str = "NORMAL",
    anomaly_label: str = "ANOMALY",
):
    """Export selected, resolved decisions as a --exemplars JSON list."""
    with ReviewStore(store) as queue:
        result = queue.exemplars(
            decision, normal_label=normal_label, anomaly_label=anomaly_label
        )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
