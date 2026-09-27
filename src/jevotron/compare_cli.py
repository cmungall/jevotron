"""CLI for complete, stable-ID dataset comparisons."""

import csv
import json
import os
import re
import sqlite3
import sys
import tempfile
import zlib
from contextlib import ExitStack, closing
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
import yaml

from jevotron.client import JevError
from jevotron.comparison import compare
from jevotron.config import Config, load_config
from jevotron.databases import Database
from jevotron.models import pointer_key, resolve
from jevotron.parsers import (
    CSV,
    FASTA,
    GMT,
    JSON,
    JSONL,
    OBO,
    TOML,
    YAML,
    Text,
    for_path,
)


class ComparisonFormat(str, Enum):
    jsonl = "jsonl"
    markdown = "markdown"


class ComparisonPolicy(str, Enum):
    none = "none"
    new_warning = "new-warning"
    any_warning = "any-warning"


def _stable_chunks(chunks, parser, id_column, fields, parser_ids):
    column = id_column if id_column is not None else getattr(parser, "id_column", None)
    positional = isinstance(parser, (CSV, JSON, JSONL, YAML, TOML, Text))
    if column is None and (
        positional
        or (not isinstance(parser, (Database, OBO, FASTA, GMT)) and not parser_ids)
    ):
        raise ValueError(
            "Comparison requires stable IDs: use --id-column or a configured parser "
            "with id_column. Custom parsers may assert stable IDs with --parser-ids."
        )
    for chunk in chunks:
        changes = {"fields": fields} if fields else {}
        if isinstance(parser, Database):
            # Database IDs include the table namespace. Never strip it or accept
            # a row-position fallback (including nullable SQLite primary keys).
            if re.search(r":row:[0-9]+\Z", chunk.id):
                raise ValueError(
                    f"Database entry {chunk.id!r} has no stable key; use --id-column "
                    "or non-null primary keys."
                )
        elif column is not None:
            value = resolve(chunk.data, pointer_key(column))
            if value is None or isinstance(value, (list, dict)) or str(value) == "":
                raise ValueError("Stable ID must be a nonempty scalar field")
            changes["id"] = str(value)
        elif isinstance(parser, OBO):
            identifiers = chunk.data.get("id", [])
            if len(identifiers) != 1 or not identifiers[0]:
                raise ValueError(
                    "Each compared OBO stanza requires exactly one nonempty id"
                )
        yield replace(chunk, **changes) if changes else chunk


def _publish(path: Path | None, content: str) -> None:
    if path is None:
        sys.stdout.write(content)
        sys.stdout.flush()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def compare_command(
    baseline: Annotated[Path, typer.Argument(help="Baseline input file or database.")],
    candidate: Annotated[
        Path, typer.Argument(help="Candidate input file or database.")
    ],
    id_column: Annotated[
        str | None, typer.Option(help="Stable top-level ID field for both inputs.")
    ] = None,
    parser_ids: Annotated[
        bool,
        typer.Option(
            "--parser-ids", help="Assert that a custom parser yields stable IDs."
        ),
    ] = False,
    config: Annotated[
        Path | None, typer.Option(help="One Python config applied to both inputs.")
    ] = None,
    guidance: Annotated[
        str | None, typer.Option(help="Shared assessment guidance.")
    ] = None,
    guidance_file: Annotated[
        Path | None, typer.Option(help="Read shared UTF-8 guidance.")
    ] = None,
    exemplars: Annotated[
        Path | None, typer.Option(help="Shared JSON list of examples.")
    ] = None,
    model: Annotated[
        str | None, typer.Option(help="Shared Jev model; pin a version for CI.")
    ] = None,
    field: Annotated[
        list[str] | None,
        typer.Option(help="JSON Pointer to assess on both sides; repeat."),
    ] = None,
    format: Annotated[
        str | None, typer.Option(help="Override the format of both inputs.")
    ] = None,
    format_option: Annotated[
        list[str] | None, typer.Option(help="Shared parser KEY=VALUE; repeat.")
    ] = None,
    table: Annotated[
        list[str] | None, typer.Option(help="Database table/view selection; repeat.")
    ] = None,
    threshold: Annotated[
        float | None, typer.Option(help="Apply this warning threshold to both inputs.")
    ] = None,
    cache: Annotated[Path, typer.Option(help="Shared SQLite assessment cache.")] = Path(
        ".jevotron/cache.sqlite3"
    ),
    no_cache: Annotated[
        bool,
        typer.Option("--no-cache", help="Disable persistent cache reads and writes."),
    ] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Reassess each distinct request once.")
    ] = False,
    fail_on: Annotated[
        ComparisonPolicy, typer.Option(help="Completed policy violations exit 3.")
    ] = ComparisonPolicy.none,
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Completed report; default stdout."),
    ] = None,
    output_format: Annotated[
        ComparisonFormat, typer.Option(help="Primary report format.")
    ] = ComparisonFormat.jsonl,
    markdown: Annotated[
        Path | None, typer.Option(help="Also write a concise Markdown report.")
    ] = None,
) -> None:
    """Compare datasets by stable ID; report new, resolved, and persistent warnings."""
    # Shared helpers are imported after the main app has registered its commands.
    from jevotron.cli import _format_options, _same_file

    try:
        protected = [baseline, candidate, config, guidance_file, exemplars, cache]
        outputs = [path for path in (output, markdown) if path is not None]
        for path in outputs:
            if path.is_dir():
                raise ValueError(f"Output must be a file, not a directory: {path}")
            if any(
                _same_file(path, source) for source in protected if source is not None
            ):
                raise ValueError(
                    "Output must not overwrite either input, config, guidance, exemplars, or cache"
                )
        if len(outputs) == 2 and _same_file(*outputs):
            raise ValueError("JSONL and Markdown output paths must be different")
        if not no_cache and any(
            _same_file(cache, source) for source in protected[:-1] if source is not None
        ):
            raise ValueError(
                "Cache must not overwrite either input, config, guidance, or exemplars"
            )
        if guidance is not None and guidance_file is not None:
            raise ValueError(
                "Use either --guidance TEXT or --guidance-file PATH, not both"
            )
        settings = load_config(config) if config is not None else Config()
        if guidance is not None:
            settings.guidance = guidance
        if guidance_file is not None:
            settings.guidance = guidance_file.read_text(encoding="utf-8")
        if exemplars is not None:
            settings.exemplars = json.loads(exemplars.read_text(encoding="utf-8"))
        if model is not None:
            settings.model = model
        if threshold is not None:
            settings.threshold = threshold
        settings.validate()
        reference_paths = [
            path
            for reference in getattr(settings, "references", [])
            for path in reference.source_paths()
        ]
        if any(
            _same_file(destination, source)
            for destination in outputs
            for source in reference_paths
        ):
            raise ValueError("Output must not overwrite reference inputs")
        if not no_cache and any(
            _same_file(cache, source) for source in reference_paths
        ):
            raise ValueError("Cache must not overwrite reference inputs")
        if settings.parser is not None and (
            format is not None or format_option or table
        ):
            raise ValueError(
                "Set parser options in the config; --format, --format-option, and --table cannot override a configured parser"
            )
        options = _format_options(format_option)
        with ExitStack() as stack:
            inputs = []
            identities = []
            for path in (baseline, candidate):
                parser = settings.parser or for_path(path, format, options)
                if isinstance(parser, Database):
                    parser = replace(
                        parser,
                        tables=table if table is not None else parser.tables,
                        id_column=id_column
                        if id_column is not None
                        else parser.id_column,
                    )
                elif table:
                    raise ValueError("--table requires a SQLite or DuckDB database")
                elif id_column is not None and isinstance(
                    parser, (CSV, JSON, JSONL, YAML, TOML)
                ):
                    parser = replace(parser, id_column=id_column)
                chunks = iter(parser(path))
                if hasattr(chunks, "close"):
                    stack.enter_context(closing(chunks))
                inputs.append(
                    _stable_chunks(chunks, parser, id_column, field, parser_ids)
                )
                identities.append(
                    {
                        "source": str(path),
                        "parser": type(parser).__name__,
                        "id_column": id_column
                        if id_column is not None
                        else getattr(parser, "id_column", None),
                        "parser_ids_asserted": parser_ids,
                    }
                )
            result = compare(
                *inputs, settings, cache=None if no_cache else cache, refresh=refresh
            )
        result.metadata["inputs"] = dict(
            zip(("baseline", "candidate"), identities, strict=True)
        )
        result.metadata["selected_fields"] = (
            field if field is not None else getattr(settings.parser, "fields", None)
        )
        policy = fail_on.value
        content = (
            result.jsonl(policy)
            if output_format == ComparisonFormat.jsonl
            else result.markdown(policy)
        )
        _publish(output, content)
        if markdown is not None:
            _publish(markdown, result.markdown(policy))
        summary = result.metadata["summary"]
        print(
            "Compared "
            + ", ".join(f"{count} {name}" for name, count in summary["entries"].items())
            + "; field warnings: "
            + ", ".join(
                f"{count} {name}" for name, count in summary["field_warnings"].items()
            )
            + f"; {summary['cached_requests']}/{summary['unique_requests']} unique requests cached.",
            file=sys.stderr,
        )
        status = 3 if result.violates(policy) else 0
    except KeyboardInterrupt:
        print(
            "INCOMPLETE: comparison interrupted; successful assessments remain cached.",
            file=sys.stderr,
        )
        status = 130
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
        print(
            "INCOMPLETE: comparison failed; successful assessments remain cached.",
            file=sys.stderr,
        )
        status = 1
    raise typer.Exit(status)
