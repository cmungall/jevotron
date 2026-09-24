# Guidance & examples

Start with a single sentence on the command line:

```sh
jevotron scan data.csv --guidance "Quantity must be nonnegative."
```

For longer instructions, use a plain UTF-8 document:

```sh
jevotron scan data.csv --guidance-file curation-guide.md
```

`--guidance` always means literal text. `--guidance-file` always means a path.
Use one or the other; both override guidance in a project config. Identical
text produces the same cached request regardless of which option you use.

Use concrete rules, units, permitted exceptions, and the meaning of “anomaly.”
For example:

```text
Entries describe physical stock. Quantity is a count and cannot be negative.
Unit prices are in US dollars and must be nonnegative. A zero price is valid
for a free sample. Category should agree with the item description.
```

Every entry receives the same document. By default, each chunk is assessed
independently using only its entry and shared guidance/exemplars.
[Reference joins and graph context](references.md) can explicitly attach related
records or an N-hop neighborhood to each independent request.

## Supply a few exemplars

Create a JSON list of selected examples. You may include expected field labels:

```json
[
  {
    "entry": {"item": "Free sample", "quantity": 2, "unit_price_usd": 0},
    "assessment": {"/quantity": "NORMAL", "/unit_price_usd": "NORMAL"}
  }
]
```

Save it as `exemplars.json`, then run:

```sh
jevotron scan data.csv --guidance-file curation-guide.md \
  --exemplars exemplars.json
```

These are in-context examples, not extra entries to scan. The CLI still scans
all input entries unless you set `--limit`. It never selects exemplars on your
behalf. Changing the document or exemplars changes the request and requires
fresh assessments.

## Inspect the exact input

With [jq](https://jqlang.org/) installed:

```sh
jevotron preview data.csv --guidance-file curation-guide.md \
  --exemplars exemplars.json --limit 1 | jq '.request'
```

Use [project configuration](config.md) to keep these settings together when
you reuse them across many runs.
