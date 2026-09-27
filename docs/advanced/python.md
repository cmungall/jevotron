# Python integration

The CLI is the primary interface. This API is available for embedding jevotron
in an existing application or ETL process.


```python
from pathlib import Path
from jevotron import Config, scan
from jevotron.parsers import CSV

chunks = CSV(id_column="ident")(Path("examples/airports/spiked.csv"))
for result in scan(chunks, Config(guidance="Check geographic consistency.")):
    if result.warning:
        print(result.id, result.score)
```

`scan(chunks, config, cache=..., refresh=False, client=None)` is a generator.
Pass `cache=None` to disable persistence. An injected client implements
`evaluate(request) -> response` using the Jev HTTP JSON contract. This makes
offline testing and external integrations straightforward. If stopping a scan
early, close its iterator (or use `contextlib.closing`) to release resources.
The caller owns an injected client's lifetime.

`compare(baseline_chunks, candidate_chunks, config, cache=..., client=...)`
is also available from `jevotron`. Supply stable chunk IDs on both sides. It
validates both complete inputs, assesses distinct requests once, and returns
a buffered `Comparison` with `entries`, `metadata`, `jsonl()`, `markdown()`, and
`violates("new-warning")`. One config and threshold apply to both inputs; see
[comparison semantics](../guides/compare.md).
