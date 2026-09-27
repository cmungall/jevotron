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

`scan(chunks, config, cache=..., refresh=False, client=None, batch_size=1,
batch_tokens=48000, stats=None)` is a generator. `batch_size="auto"` chooses an
economical layout; an integer caps entries per request, and `1` selects the
original per-entry layout. `preview` accepts the same batching options and shows
planned requests assuming cache misses. See [batching](../guides/guidance.md#batching-and-large-guidance-files).

Pass a `jevotron.runner.ScanStats()` instance as `stats` to collect new API
submissions, actual input/output tokens, and estimated input tokens. These
counters are per request, so batched entries do not multiply token totals.
Pass `cache=None` to disable persistence. An injected client implements
`evaluate(request) -> response` using the Jev HTTP JSON contract. This makes
offline testing and external integrations straightforward. If stopping a scan
early, close its iterator (or use `contextlib.closing`) to release resources.
The caller owns an injected client's lifetime.
