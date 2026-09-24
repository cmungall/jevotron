# Your first scan

Go from a CSV file to a review queue in three commands. You need Python 3.12+
and [uv](https://docs.astral.sh/uv/).

## Install the CLI

From your local checkout:

```sh
uv tool install .
jevotron --help
```

This installs the `jevotron` command in an isolated environment. If needed, run
`uv tool update-shell` and restart your shell to put it on your path.
For development, `uv sync` followed by `uv run jevotron ...` uses the checkout
directly. This project does not yet assume a published PyPI release.

## Set your API key

!!! important "Required for live scans"
    Get an API key from the [TypeSafe dashboard](https://console.typesafe.ai/),
    then run this in the same shell you'll use for jevotron:

    ```sh
    export TYPESAFE_API_KEY="your-api-key"
    ```

    Replace the placeholder with your key. If it's already in your environment,
    no further setup is needed. `preview` and fully cached runs need no key.

Check that it is set without displaying the key:

```sh
test -n "$TYPESAFE_API_KEY" && echo "API key is set"
```

The CLI reads the environment variable automatically; it does not load `.env`
files. Keep credentials out of input data, project configs, and reports.

## Create a small input

```sh
cat > stock.csv <<'EOF'
id,item,quantity
notebook,A5 notebook,12
cable,USB-C cable,-30
mug,Ceramic mug,6
EOF
```

## Preview the input

```sh
jevotron preview stock.csv --guidance "Quantity must be nonnegative." --limit 1
```

Preview returns JSONL containing the entry ID, source, selected field paths,
request hash, and exact request. It needs no API key, makes no network requests,
and does not open the cache. By default, it shows three entries.

## Scan the file

```sh
jevotron scan stock.csv --guidance "Quantity must be nonnegative."
```

Scan evaluates all entries by default. Each entry is independent and sees the
same guidance. Results stream to your terminal as JSONL. Successful assessments are immediately saved to
`.jevotron/cache.sqlite3`.

## Review the warnings

```sh
jevotron scan stock.csv --guidance "Quantity must be nonnegative." \
  --warnings-only --output-format csv -o warnings.csv
```

This reuses the previous assessments. The negative quantity should be flagged;
actual model probabilities may vary. Results go to the output file, and the
scan summary goes to stderr.

!!! tip "Make the CLI yours"
    Run `jevotron --install-completion` to enable completion in your shell.
    Explore `jevotron preview --help` and `jevotron scan --help` for grouped options.

Next: [select fields in other formats](guides/files.md),
[compare file versions](guides/cache.md), or [try the airport example](examples/airports.md).
