# Airports / CSV

Eight real airport records, two deliberately changed country codes, one short command.

!!! important "API key required"
    Set `export TYPESAFE_API_KEY="your-api-key"` using a key from the
    [TypeSafe dashboard](https://console.typesafe.ai/).
    [Full setup instructions](../quickstart.md#set-your-api-key).

## Get the data

[Download airports.csv](../downloads/airports/airports.csv){ .md-button .md-button--primary }

Or, from a checkout:

```sh
cp examples/airports/spiked.csv airports.csv
```

This is an eight-record public-domain subset of OurAirports. We intentionally
changed Heathrow's country to Japan and JFK's to France. The
[source record](../downloads/airports/SOURCE.md.txt) and
[exact injected changes](../downloads/airports/changes.json) document the changes;
those labels are never sent to Jev.

## Scan

```sh
jevotron scan airports.csv --guidance "Check airport locations."
```

All columns are assessed by default. No config file, field selection, or separate
guidance document is needed.

## Actual output

This is a captured Jev run of the command above, not invented example scores.
The tables display the JSONL output in a readable form. Future probabilities
may differ; this small example is not a benchmark of general accuracy.

{{ results:airports }}

The country mismatches were both flagged. Other fields can also receive elevated
scores when a record contradicts itself; the field results show exactly which
parts Jev considered suspicious.

## Export only warnings

```sh
jevotron scan airports.csv --guidance "Check airport locations." \
  --warnings-only --output-format csv -o warnings.csv
```

[Download the actual warnings.csv](../assets/results/airports-warnings.csv).
It contains all assessed fields of the two warning entries. This second run
reuses all eight cached assessments.

## Compare with the baseline

Download [baseline.csv](../downloads/airports/baseline.csv), then run:

```sh
jevotron scan baseline.csv --guidance "Check airport locations."
```

Six records are unchanged and reuse their assessments. Only two require new
calls. Keep the guidance and other inference settings identical across versions.

## More control when you need it

Use `--field /iso_country` to score only the country while keeping the whole row
visible. Use `--id-column ident` to report airport identifiers instead of row
numbers. These are optional refinements, not prerequisites.

The [example bundle](../downloads/examples.zip) also includes a longer guidance
file and reusable config. Longer guidance is supplied with `--guidance-file`.
