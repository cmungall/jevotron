# Inventory / YAML

Apply written rules to a synthetic stock list containing a negative quantity
and a ceramic mug assigned to stationery.

!!! important "API key required"
    Set `export TYPESAFE_API_KEY="your-api-key"` with a key from the
    [TypeSafe dashboard](https://console.typesafe.ai/).
    [Full setup instructions](../quickstart.md#set-your-api-key).

## Get the files

Download [items.yaml](../downloads/inventory/items.yaml) and
[jev_config.py](../downloads/inventory/jev_config.py) into the same directory,
or use `examples/inventory/` in a checkout.

The config contains category rules and a selected correct example. Use it
entirely through the CLI:

```sh
jevotron preview items.yaml --config jev_config.py
jevotron scan items.yaml --config jev_config.py
```

Quantity, category, and unit price receive separate scores. The entire item,
including its description, remains visible. Review the `cable` quantity and
`mug` category; exact model scores may vary.

## Actual output

Captured from the command above. This run reused saved Jev assessments; the
original assessment dates and probabilities are preserved. The tables render
actual JSONL results, and future model results may differ.

{{ results:inventory }}

[Download the actual warning CSV](../assets/results/inventory-warnings.csv), or create it yourself:

```sh
jevotron scan items.yaml --config jev_config.py --warnings-only --output-format csv -o warnings.csv
```

## Use flags instead

To check only quantity with a one-line instruction:

```sh
jevotron scan items.yaml --id-column id --field /quantity \
  --guidance "Quantity is physical stock and must be nonnegative." --warnings-only
```

This is a different assessment request and creates separate cache records.
