"""Command-line preview and streaming assessment reports."""

import csv
import json
import sqlite3
import sys
import zlib
from contextlib import ExitStack, closing
from dataclasses import dataclass, replace
from enum import Enum
from itertools import islice
from pathlib import Path
from typing import Annotated

import typer
import yaml

from jevotron.client import JevError
from jevotron.compare_cli import compare_command
from jevotron.config import Config, load_config
from jevotron.databases import Database
from jevotron.models import json_text, pointer_key, resolve
from jevotron.parsers import FORMATS, for_path, format_spec
from jevotron.runner import preview, scan

app = typer.Typer(
    help="Find suspicious fields in files and databases. Preview, scan, and review warnings.",
    no_args_is_help=True,
    rich_markup_mode="markdown",
    pretty_exceptions_show_locals=False,
)
app.command("compare")(compare_command)

InputFile = Annotated[
    Path,
    typer.Argument(
        help="File to assess; detect databases by header, other formats by extension."
    ),
]
ConfigFile = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="Local Python configuration file.",
        rich_help_panel="Input & guidance",
    ),
]
Guidance = Annotated[
    str | None,
    typer.Option(
        help="Inline instructions describing what is correct.",
        rich_help_panel="Input & guidance",
    ),
]
GuidanceFile = Annotated[
    Path | None,
    typer.Option(
        help="Read guidance from a UTF-8 document (instead of --guidance).",
        rich_help_panel="Input & guidance",
    ),
]
Exemplars = Annotated[
    Path | None,
    typer.Option(
        help="JSON list of user-selected examples.", rich_help_panel="Input & guidance"
    ),
]
Model = Annotated[
    str | None,
    typer.Option(
        help="Jev model; defaults to jev-1.13.0 or the config value.",
        rich_help_panel="Input & guidance",
    ),
]
Fields = Annotated[
    list[str] | None,
    typer.Option(
        "--field",
        help="JSON Pointer to assess; repeat to select multiple fields.",
        rich_help_panel="Input & guidance",
    ),
]
IdColumn = Annotated[
    str | None,
    typer.Option(
        help="Use this top-level scalar field as the entry ID.",
        rich_help_panel="Input & guidance",
    ),
]
Output = Annotated[
    Path | None,
    typer.Option(
        "--output",
        "-o",
        help="Write results to this file; defaults to stdout.",
        rich_help_panel="Output",
    ),
]
InputFormat = Annotated[
    str | None,
    typer.Option(
        "--format",
        help="Override input format; see 'jevotron formats'.",
        rich_help_panel="Input format",
    ),
]
FormatOptions = Annotated[
    list[str] | None,
    typer.Option(
        "--format-option",
        help="Parser option KEY=VALUE; repeat as needed.",
        rich_help_panel="Input format",
    ),
]
Tables = Annotated[
    list[str] | None,
    typer.Option(
        "--table",
        help="Database table or view (optionally schema-qualified); repeat to select several. Default: all user tables.",
        rich_help_panel="Input format",
    ),
]


class OutputFormat(str, Enum):
    jsonl = "jsonl"
    csv = "csv"


@dataclass
class RunOptions:
    command: str
    file: Path
    config: Path | None = None
    format: str | None = None
    format_options: list[str] | None = None
    tables: list[str] | None = None
    guidance: str | None = None
    guidance_file: Path | None = None
    exemplars: Path | None = None
    model: str | None = None
    fields: list[str] | None = None
    id_column: str | None = None
    limit: int | None = None
    output: Path | None = None
    cache: Path = Path(".jevotron/cache.sqlite3")
    no_cache: bool = False
    refresh: bool = False
    threshold: float | None = None
    warnings_only: bool = False
    sort_score: bool = False
    output_format: str = "jsonl"


@app.command("preview")
def preview_command(
    file: InputFile,
    config: ConfigFile = None,
    format: InputFormat = None,
    format_option: FormatOptions = None,
    table: Tables = None,
    guidance: Guidance = None,
    guidance_file: GuidanceFile = None,
    exemplars: Exemplars = None,
    model: Model = None,
    field: Fields = None,
    id_column: IdColumn = None,
    limit: Annotated[int, typer.Option(min=1, help="Maximum entries to preview.")] = 3,
    output: Output = None,
) -> None:
    """Inspect exact model requests without API calls or credentials."""
    raise typer.Exit(
        _run(
            RunOptions(
                command="preview",
                file=file,
                config=config,
                format=format,
                format_options=format_option,
                tables=table,
                guidance=guidance,
                guidance_file=guidance_file,
                exemplars=exemplars,
                model=model,
                fields=field,
                id_column=id_column,
                limit=limit,
                output=output,
            )
        )
    )


@app.command("scan")
def scan_command(
    file: InputFile,
    config: ConfigFile = None,
    format: InputFormat = None,
    format_option: FormatOptions = None,
    table: Tables = None,
    guidance: Guidance = None,
    guidance_file: GuidanceFile = None,
    exemplars: Exemplars = None,
    model: Model = None,
    field: Fields = None,
    id_column: IdColumn = None,
    limit: Annotated[
        int | None, typer.Option(min=1, help="Maximum entries to assess; default all.")
    ] = None,
    output: Output = None,
    cache: Annotated[
        Path, typer.Option(help="SQLite assessment cache.", rich_help_panel="Cache")
    ] = Path(".jevotron/cache.sqlite3"),
    no_cache: Annotated[
        bool,
        typer.Option(
            "--no-cache",
            help="Disable cache reads and writes.",
            rich_help_panel="Cache",
        ),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            help="Reassess and replace matching cache entries.",
            rich_help_panel="Cache",
        ),
    ] = False,
    threshold: Annotated[
        float | None,
        typer.Option(
            help="Warning threshold from 0 to 1; default 0.5 or config value.",
            rich_help_panel="Output",
        ),
    ] = None,
    warnings_only: Annotated[
        bool,
        typer.Option(
            "--warnings-only",
            help="Emit only entries at or above the threshold.",
            rich_help_panel="Output",
        ),
    ] = False,
    sort_score: Annotated[
        bool,
        typer.Option(
            "--sort-score",
            help="Buffer results and emit highest scores first.",
            rich_help_panel="Output",
        ),
    ] = False,
    output_format: Annotated[
        OutputFormat, typer.Option(help="Report format.", rich_help_panel="Output")
    ] = OutputFormat.jsonl,
) -> None:
    """Assess fields with Jev, reusing cached results for unchanged entries."""
    raise typer.Exit(
        _run(
            RunOptions(
                command="scan",
                file=file,
                config=config,
                format=format,
                format_options=format_option,
                tables=table,
                guidance=guidance,
                guidance_file=guidance_file,
                exemplars=exemplars,
                model=model,
                fields=field,
                id_column=id_column,
                limit=limit,
                output=output,
                cache=cache,
                no_cache=no_cache,
                refresh=refresh,
                threshold=threshold,
                warnings_only=warnings_only,
                sort_score=sort_score,
                output_format=output_format.value,
            )
        )
    )


@app.command("formats")
def formats_command(
    name: Annotated[
        str | None, typer.Argument(help="Show options for this format.")
    ] = None,
) -> None:
    """List supported input formats, defaults, and parser options. No API key needed."""
    try:
        specs = (format_spec(name),) if name else FORMATS
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for spec in specs:
        typer.echo(f"{spec.name} ({', '.join(spec.extensions)}): {spec.summary}")
        if name:
            for option in spec.all_options:
                typer.echo(f"  {option.name}={option.default!r} — {option.help}")
            if not spec.text:
                typer.echo(
                    "  Use --table NAME to select tables or views; 'jt tables FILE' lists them."
                )
    typer.echo(
        "\nText formats accept .gz compression and --format-option encoding=NAME."
    )
    if not name:
        typer.echo("Use 'jevotron formats NAME' to see format-specific options.")


@app.command("tables")
def tables_command(file: InputFile, format: InputFormat = None) -> None:
    """List database tables, views, columns, and primary keys as JSONL. No API key needed."""
    try:
        parser = for_path(file, format)
        if not isinstance(parser, Database):
            raise ValueError("tables requires a SQLite or DuckDB database")
        for table in parser.catalog(file):
            typer.echo(json_text(table.to_dict()))
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _format_options(items):
    values = {}
    for item in items or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError("--format-option requires KEY=VALUE")
        if key in values:
            raise ValueError(f"Repeated format option: {key!r}")
        values[key] = value
    return values


def _selected(chunks, args):
    for chunk in chunks:
        changes = {}
        if args.fields:
            changes["fields"] = args.fields
        if args.id_column is not None:
            value = resolve(chunk.data, pointer_key(args.id_column))
            if value is None or isinstance(value, (list, dict)) or str(value) == "":
                raise ValueError("--id-column must select a nonempty scalar field")
            changes["id"] = str(value)
        yield replace(chunk, **changes) if changes else chunk


def _write_csv(writer, result):
    for field in result.fields:
        writer.writerow(
            {
                "id": result.id,
                "source": result.source,
                "label": result.label,
                "score": result.score,
                "warning": result.warning,
                "field": field.path,
                "value": json_text(field.value),
                "field_label": field.label,
                "field_score": field.score,
                "probabilities": json_text(field.probabilities),
                "confidence": field.confidence,
                "model": result.model,
                "assessed_at": result.assessed_at,
                "request_hash": result.request_hash,
                "cached": result.cached,
            }
        )


def _same_file(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve() or (
        left.exists() and right.exists() and left.samefile(right)
    )


def _run(args: RunOptions) -> int:
    count = cached = warnings = emitted = 0
    try:
        if args.guidance is not None and args.guidance_file is not None:
            raise ValueError(
                "Use either --guidance TEXT or --guidance-file PATH, not both"
            )
        config = load_config(args.config) if args.config else Config()
        if args.guidance is not None:
            config.guidance = args.guidance
        elif args.guidance_file is not None:
            config.guidance = args.guidance_file.read_text(encoding="utf-8")
        if args.exemplars:
            config.exemplars = json.loads(args.exemplars.read_text(encoding="utf-8"))
        if args.model:
            config.model = args.model
        if getattr(args, "threshold", None) is not None:
            config.threshold = args.threshold
        config.validate()
        if args.output:
            protected = [
                args.file,
                args.config,
                args.guidance_file,
                args.exemplars,
                getattr(args, "cache", None),
            ]
            if any(_same_file(args.output, p) for p in protected if p):
                raise ValueError(
                    "Output must not overwrite input, config, guidance, exemplars, or cache"
                )
        if config.parser is not None and (
            args.format is not None or args.format_options or args.tables
        ):
            raise ValueError(
                "--format, --format-option, and --table cannot be combined with a custom config parser; "
                "set its options in the Python config"
            )
        parser = config.parser or for_path(
            args.file, args.format, _format_options(args.format_options)
        )
        selection_args = args
        if isinstance(parser, Database):
            parser = replace(
                parser,
                tables=args.tables if args.tables is not None else parser.tables,
                id_column=args.id_column
                if args.id_column is not None
                else parser.id_column,
            )
            # Database IDs retain the table namespace even with --id-column.
            selection_args = replace(args, id_column=None)
            if (
                args.command == "scan"
                and not args.no_cache
                and _same_file(args.cache, args.file)
            ):
                raise ValueError("Cache must not overwrite the input database")
        elif args.tables:
            raise ValueError("--table requires a SQLite or DuckDB database")
        chunks = iter(parser(args.file))
        with ExitStack() as stack:
            if hasattr(chunks, "close"):
                stack.enter_context(closing(chunks))
            selected = _selected(chunks, selection_args)
            limited = (
                islice(selected, args.limit) if args.limit is not None else selected
            )
            output = sys.stdout
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                output = stack.enter_context(
                    args.output.open("w", encoding="utf-8", newline="")
                )
            if args.command == "preview":
                for item in preview(limited, config):
                    print(json_text(item), file=output)
                return 0
            results = stack.enter_context(
                closing(
                    scan(
                        limited,
                        config,
                        cache=None if args.no_cache else args.cache,
                        refresh=args.refresh,
                    )
                )
            )
            writer = None
            if args.output_format == "csv":
                writer = csv.DictWriter(
                    output,
                    fieldnames=[
                        "id",
                        "source",
                        "label",
                        "score",
                        "warning",
                        "field",
                        "value",
                        "field_label",
                        "field_score",
                        "probabilities",
                        "confidence",
                        "model",
                        "assessed_at",
                        "request_hash",
                        "cached",
                    ],
                )
                writer.writeheader()

            def write(result):
                nonlocal emitted
                if args.warnings_only and not result.warning:
                    return
                if writer:
                    _write_csv(writer, result)
                else:
                    print(json_text(result.to_dict()), file=output)
                output.flush()
                emitted += 1

            pending = []
            for result in results:
                count += 1
                cached += result.cached
                warnings += result.warning
                if args.sort_score:
                    pending.append(result)
                else:
                    write(result)
            for result in sorted(pending, key=lambda r: r.score, reverse=True):
                write(result)
        print(
            f"Assessed {count} entries ({cached} cached); {warnings} warnings; "
            f"emitted {emitted} entries.",
            file=sys.stderr,
        )
        return 0
    except KeyboardInterrupt:
        print(
            f"Interrupted after {count} entries; successful assessments remain cached.",
            file=sys.stderr,
        )
        return 130
    except (
        JevError,
        ValueError,
        OSError,
        sqlite3.Error,
        yaml.YAMLError,
        csv.Error,
        EOFError,
        zlib.error,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        if args.command == "scan":
            print(
                f"INCOMPLETE: {count} entries assessed; successful assessments remain cached.",
                file=sys.stderr,
            )
        return 1


def main(argv: list[str] | None = None) -> int:
    """Console entry point, also usable by offline command tests."""
    try:
        app(args=argv, prog_name="jevotron")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
