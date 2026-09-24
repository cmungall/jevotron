# Shell pipelines

Results go to stdout by default. Summaries and errors go to stderr, so JSONL
can flow directly into other tools. The examples below use
[jq](https://jqlang.org/).

## List suspicious entries

```sh
set -o pipefail
jevotron scan data.csv --warnings-only \
  | jq -r '[.id, .score, .source] | @tsv'
```

For a per-field view:

```sh
jevotron scan data.csv \
  | jq -r '. as $entry | .fields[] | select(.score >= 0.5) |
      [$entry.id, .path, .label, .score] | @tsv'
```

## Publish a report only after a successful scan

```sh
if jevotron scan data.csv --warnings-only --sort-score \
    --output-format csv -o warnings.pending.csv; then
  mv warnings.pending.csv warnings.csv
else
  printf '%s\n' 'Scan incomplete; previous warnings.csv retained.' >&2
  exit 1
fi
```

## Make warnings fail a shell check

Warnings do not cause a scan failure by default. Decide that policy explicitly:

```sh
jevotron scan data.csv --warnings-only -o warnings.jsonl || exit 1
if test -s warnings.jsonl; then
  printf '%s\n' 'Entries need review; see warnings.jsonl.' >&2
  exit 1
fi
```

| Exit code | Meaning |
| --- | --- |
| `0` | Command completed successfully; warnings may exist |
| `1` | Input, configuration, cache, or API error |
| `2` | Invalid CLI syntax or option value |
| `130` | Interrupted during processing |

## Get help and completion

```sh
jevotron --help
jevotron preview --help
jevotron scan --help
jevotron --install-completion
```

The CLI is built with Typer. Option groups separate input and guidance, output,
and cache settings. No progress bars or decorative output are mixed into reports.
