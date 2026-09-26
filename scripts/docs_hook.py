"""Generate command reference and example downloads from their actual sources."""

import json
import re
import shlex
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from mkdocs.structure.files import File
from typer.main import get_command

from jevotron.cli import app
from jevotron.models import resolve
from jevotron.parsers import FORMATS, OBO

ROOT = Path(__file__).resolve().parents[1]
FORMAT_PREVIEWS = {}


def on_files(files, config):
    # Run the documented commands against the downloadable inputs, offline.
    # A broken example fails the build instead of publishing invented output.
    FORMAT_PREVIEWS.clear()
    folder = ROOT / "examples/formats"
    manifest = json.loads((folder / "previews.json").read_text())
    for name, example in manifest.items():
        args = ["preview", example["file"], *example["args"]]
        result = subprocess.run(
            [sys.executable, "-m", "jevotron", *args],
            cwd=folder,
            capture_output=True,
            text=True,
            check=True,
        )
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        if len(rows) != 1:
            raise ValueError(f"Format example {name} must preview exactly one entry")
        FORMAT_PREVIEWS[name] = {
            **example,
            "command": shlex.join(["jevotron", *args]),
            "input": (folder / example["file"]).read_text(),
            "output": rows[0],
        }
        files.append(
            File.generated(
                config,
                f"downloads/formats/{name}-preview.jsonl",
                content=result.stdout,
            )
        )
    bundle = BytesIO()
    with ZipFile(bundle, "w") as archive:
        for path in sorted((ROOT / "examples").rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                name = path.relative_to(ROOT / "examples")
                content = path.read_bytes()
                archive.writestr(str(name), content)
                # Markdown inputs must download verbatim, not become HTML pages.
                target = f"downloads/{name}" + (".txt" if path.suffix == ".md" else "")
                files.append(File.generated(config, target, content=content))
    files.append(
        File.generated(config, "downloads/examples.zip", content=bundle.getvalue())
    )
    files.append(
        File.generated(
            config,
            "downloads/airports/airports.csv",
            content=(ROOT / "examples/airports/spiked.csv").read_bytes(),
        )
    )
    # Publish the canonical study report and its evidence, without a second
    # hand-maintained copy of the analysis in docs/.
    study = ROOT / "analysis/agent-traces"
    analysis_bundle = BytesIO()
    with ZipFile(analysis_bundle, "w") as archive:
        for path in sorted(study.iterdir()):
            if not path.is_file() or path.suffix not in {
                ".md",
                ".py",
                ".json",
                ".jsonl",
            }:
                continue
            content = path.read_bytes()
            archive.writestr(f"analysis/agent-traces/{path.name}", content)
            if path.name == "README.md":
                target = "index.md"
                content = content.decode().replace(
                    "](guidance.md)", "](guidance.md.txt)"
                )
            else:
                target = path.name + (".txt" if path.suffix == ".md" else "")
            files.append(
                File.generated(
                    config, f"analysis/agent-traces/{target}", content=content
                )
            )
    files.append(
        File.generated(
            config,
            "downloads/agent-trace-analysis.zip",
            content=analysis_bundle.getvalue(),
        )
    )
    return files


def _format_example(match):
    name = match.group(1)
    example = FORMAT_PREVIEWS[name]
    row = example["output"]
    excerpt = {
        "id": row["id"],
        "source": row["source"],
        "fields": row["fields"],
        "request": {"state": {"entry": row["request"]["state"]["entry"]}},
    }
    filename = example["file"]
    download = filename + (".txt" if filename.endswith(".md") else "")
    return "\n".join(
        [
            f"**1. Input — `{filename}`**",
            "",
            f"```{example['language']}",
            example["input"].rstrip("\n"),
            "```",
            "",
            f'[Download `{filename}`](../downloads/formats/{download}){{ download="{filename}" }}',
            "",
            "**2. Command**",
            "",
            "```sh",
            example["command"],
            "```",
            "",
            "**3. Preview output**",
            "",
            "Entry metadata and parsed data (excerpt):",
            "",
            "```json",
            json.dumps(excerpt, indent=2, ensure_ascii=False),
            "```",
            "",
            '??? example "Complete preview output (formatted JSON)"',
            "",
            "    ```json",
            *[
                f"    {line}"
                for line in json.dumps(row, indent=2, ensure_ascii=False).splitlines()
            ],
            "    ```",
            "",
            f"[Download exact JSONL output](../downloads/formats/{name}-preview.jsonl)",
        ]
    )


def _results(match):
    name = match.group(1)
    folder = ROOT / "docs/assets/results"
    rows = [
        json.loads(line) for line in (folder / f"{name}.jsonl").read_text().splitlines()
    ]
    summary = (folder / f"{name}-summary.txt").read_text().strip()
    models = ", ".join(sorted({row["model"] for row in rows}))
    dates = ", ".join(sorted({row["assessed_at"].split("T")[0] for row in rows}))

    def entry(row):
        ident = next((f["value"] for f in row["fields"] if f["path"] == "/ident"), None)
        return f"{row['id']} · {ident}" if ident else row["id"]

    lines = [
        f"Model: **{models}** · Assessment date: **{dates}**.",
        "",
        "**Terminal summary (stderr):**",
        "",
        "```text",
        summary,
        "```",
        "",
        "**All entries:**",
        "",
        "| Entry | Label | Score | Warning |",
        "| --- | --- | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {entry(row)} | {row['label']} | {row['score']:.2f} | {'Yes' if row['warning'] else 'No'} |"
        )
    lines.extend(
        [
            "",
            "**Flagged fields (score ≥ 0.5):**",
            "",
            "| Entry | Field | Value | Score |",
            "| --- | --- | --- | ---: |",
        ]
    )
    for row in rows:
        for field in row["fields"]:
            if field["score"] >= 0.5:
                value = json.dumps(field["value"], ensure_ascii=False).replace(
                    "|", "\\|"
                )
                lines.append(
                    f"| {entry(row)} | `{field['path']}` | `{value}` | {field['score']:.2f} |"
                )
    first = next(row for row in rows if row["warning"])
    lines.extend(
        [
            "",
            '??? example "One complete warning entry (actual JSON)"',
            "",
            "    ```json",
            *[
                f"    {line}"
                for line in json.dumps(first, indent=2, ensure_ascii=False).splitlines()
            ],
            "    ```",
            "",
            f"[Download complete JSONL output](../assets/results/{name}.jsonl) · "
            f"[Download terminal summary](../assets/results/{name}-summary.txt)",
        ]
    )
    return "\n".join(lines)


def _obo_results():
    """Show captured output beside the exact source stanza it assessed."""
    source = ROOT / "examples/units/units.obo"
    source_lines = source.read_text().splitlines()
    chunks = list(OBO()(source))
    stanzas = {}
    for index, chunk in enumerate(chunks):
        start = int(chunk.source.rsplit(":", 1)[1]) - 1
        end = (
            int(chunks[index + 1].source.rsplit(":", 1)[1]) - 1
            if index + 1 < len(chunks)
            else len(source_lines)
        )
        stanzas[chunk.id] = (chunk, start, "\n".join(source_lines[start:end]).rstrip())
    folder = ROOT / "docs/assets/results"
    rows = [
        json.loads(line) for line in (folder / "units.jsonl").read_text().splitlines()
    ]
    lines = [
        "**Terminal summary (stderr):**",
        "",
        "```text",
        (folder / "units-summary.txt").read_text().rstrip(),
        "```",
    ]
    for row in rows:
        chunk, start, stanza = stanzas[row["id"]]
        if row["source"] != f"{source.name}:{start + 1}" or any(
            resolve(chunk.data, field["path"]) != field["value"]
            for field in row["fields"]
        ):
            raise ValueError("Captured OBO results no longer match the example source")
        if not row["warning"]:
            continue
        lines.extend(
            [
                "",
                f"### Warning: {row['id']} — {chunk.data['name'][0]}",
                "",
                f"Original stanza at `{row['source']}`:",
                "",
                "```text",
                stanza,
                "```",
                "",
                "**Actual JSONL result for this stanza (formatted for readability):**",
                "",
                "```json",
                json.dumps(row, indent=2, ensure_ascii=False),
                "```",
            ]
        )
    return "\n".join(lines)


def on_page_markdown(markdown, page, config, files):
    if "{{ obo_results }}" in markdown:
        markdown = markdown.replace("{{ obo_results }}", _obo_results())
    markdown = re.sub(r"\{\{ format_example:(\w+) \}\}", _format_example, markdown)
    markdown = re.sub(
        r"\{\{ results:(airports|inventory|units) \}\}", _results, markdown
    )
    if "{{ format_reference }}" in markdown:
        lines = [
            "| Format | Detected extensions | Default chunking and fields |",
            "| --- | --- | --- |",
        ]
        for spec in FORMATS:
            extensions = ", ".join(f"`{ext}`" for ext in spec.extensions)
            lines.append(f"| `{spec.name}` | {extensions} | {spec.summary} |")
        lines.extend(["", "### Parser options", ""])
        lines.extend(
            [
                "Text formats accept `encoding=utf-8-sig` by default (UTF-8 with an optional BOM).",
                "Use, for example, `--format-option encoding=latin-1` for a legacy file.",
                "",
                "| Format | Option | Default | Meaning |",
                "| --- | --- | --- | --- |",
            ]
        )
        for spec in FORMATS:
            for option in spec.options:
                default = json.dumps(option.default)
                lines.append(
                    f"| `{spec.name}` | `{option.name}` | `{default}` | {option.help} |"
                )
        markdown = markdown.replace("{{ format_reference }}", "\n".join(lines))
    if "{{ cli_reference }}" not in markdown:
        return markdown
    root = get_command(app)
    sections = []
    for name, command in [
        ("jevotron", root),
        *[(f"jevotron {n}", c) for n, c in root.commands.items()],
    ]:
        with command.make_context(name, [], resilient_parsing=True) as context:
            sections.extend(
                [
                    f"## `{name}`",
                    "",
                    command.help or "",
                    "",
                    "```text",
                    command.get_usage(context),
                    "```",
                    "",
                    "| Option | Description |",
                    "| --- | --- |",
                ]
            )
            for parameter in command.get_params(context):
                record = parameter.get_help_record(context)
                if record:
                    option, help_text = record
                    sections.append(
                        f"| `{option.replace('|', '&#124;')}` | {help_text.replace('|', '&#124;')} |"
                    )
            sections.append("")
    return markdown.replace("{{ cli_reference }}", "\n".join(sections))
