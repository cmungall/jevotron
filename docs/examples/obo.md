# Measurement units / OBO

The [units.obo source on GitHub](https://github.com/cmungall/jevotron/blob/main/examples/units/units.obo)
contains three synthetic terms: metre, kilogram, and second. `DEMO` identifiers
are local examples, not identifiers from the Unit Ontology. The kilogram stanza
deliberately contains an incorrect definition.

## Scan definitions and synonyms

The [example config on GitHub](https://github.com/cmungall/jevotron/blob/main/examples/units/jev_config.py)
selects every definition and synonym in each stanza. The entire stanza, including
its name, is supplied as context. Only the selected fields receive scores.

OBO tags become lists, even when a tag occurs only once: `/def/0` is the first
definition, and `/synonym/0` and `/synonym/1` are separate synonyms. The config
selects the fields that actually exist, so the metre stanza's two synonyms and
the other stanzas' single synonyms are all assessed.

From a repository checkout, inspect the requests without making an API call:

```sh
cd examples/units
jevotron preview units.obo --config jev_config.py
```

Then scan with the same config. An uncached scan needs a
[configured API key](../quickstart.md#set-your-api-key):

```sh
jevotron scan units.obo --config jev_config.py
```

The guidance asks whether definitions and synonyms match the named measurement
unit. This example assesses each stanza independently; its config supplies no
related stanzas or ontology graph.

## Actual output

The following output was captured from that command while replaying three cached
Jev assessments. `cached: true` and the original assessment dates are preserved.
An uncached run can produce different probabilities.

{{ obo_results }}

The default warning threshold is **0.5**. In this result, `/def/0` crosses that
threshold; `/synonym/0` does not. The stanza is marked as a warning because at
least one selected field crosses the threshold. The result contains labels and
probabilities; the original stanza above provides the context for reviewing them.

The complete [three-entry JSONL output](https://github.com/cmungall/jevotron/blob/main/docs/assets/results/units.jsonl)
and [terminal summary](https://github.com/cmungall/jevotron/blob/main/docs/assets/results/units-summary.txt)
are available on GitHub.

## Export warning entries

```sh
jevotron scan units.obo --config jev_config.py --warnings-only --output-format csv -o warnings.csv
```

The [actual warning CSV](https://github.com/cmungall/jevotron/blob/main/docs/assets/results/units-warnings.csv)
contains both assessed fields of the kilogram stanza, including the normal `kg`
synonym. `--warnings-only` filters entries, so it preserves the other assessed
fields of each warning entry for review. With the same inputs and config, this
export reuses the cached assessments.

## Select just the definition without a config

```sh
jevotron scan units.obo --field /def/0 --guidance "The definition must match the named unit."
```

This requests a different assessment from the captured config-based run above.
