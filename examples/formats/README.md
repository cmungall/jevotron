# File format examples

Small, synthetic examples of realistic inventory, operations, transaction, and
sequence data. These demonstrate parsing, not measured anomaly-detection accuracy.
Some records intentionally contain suspicious values. EX identifiers and sequence
fragments are illustrative; the gene sets are not curated pathway definitions.

The format reference shows each input, command, and CLI-generated preview.
`previews.json` lists the commands used by the documentation build. Every command
runs from this directory, needs no API key, and previews one entry.

```sh
jevotron preview products.csv --limit 1
jevotron preview catalog.json --format-option records=/products --limit 1
```
