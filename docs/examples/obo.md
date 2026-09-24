# Measurement units / OBO

This synthetic ontology has three stanzas. One incorrectly defines a kilogram
as a unit of time. Each stanza is assessed independently.

!!! important "API key required"
    Set `export TYPESAFE_API_KEY="your-api-key"` with a key from the
    [TypeSafe dashboard](https://console.typesafe.ai/).
    [Full setup instructions](../quickstart.md#set-your-api-key).

## Get the files

Download [units.obo](../downloads/units/units.obo), or use
`examples/units/units.obo` in a checkout. `DEMO` identifiers are local examples,
not identifiers from the Unit Ontology.

## Scan definitions and synonyms

Synonyms may occur a different number of times in each stanza. The small
[example config](../downloads/units/jev_config.py) selects all existing definitions
and synonyms. Put it beside `units.obo`:

```sh
jevotron preview units.obo --config jev_config.py
jevotron scan units.obo --config jev_config.py
```

Repeated synonyms remain separate fields, such as `/synonym/0` and `/synonym/1`.
This config assesses each stanza on its own. For explicit related evidence,
see [reference joins and graph context](../guides/references.md).

## Actual output

Captured from the config-based command above, using saved Jev assessments with
original dates and probabilities. These are actual results, not guaranteed
scores for future runs.

{{ results:units }}

[Download the actual warning CSV](../assets/results/units-warnings.csv), or export it:

```sh
jevotron scan units.obo --config jev_config.py --warnings-only --output-format csv -o warnings.csv
```

## Select just the definition without a config

```sh
jevotron scan units.obo --field /def/0 --guidance "The definition must match the named unit."
```

This requests a different assessment from the captured config-based run above.
