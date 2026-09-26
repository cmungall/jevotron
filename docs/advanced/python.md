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

`preview(chunks, config=None)` yields the exact requests `scan` would send, as
dictionaries with `id`, `source`, `fields`, `absent`, `request_hash`, and
`request`. Like the CLI command, it needs no credentials, opens no cache, and
makes no network calls:

```python
from jevotron import Config, preview

for item in preview(chunks, Config(guidance="Check geographic consistency.")):
    print(item["id"], item["request_hash"])
```
