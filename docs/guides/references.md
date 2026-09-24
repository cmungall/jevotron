# Reference joins and graph context

Attach explicit related records when the current entry does not contain enough
information to judge a field. A `ReferenceJoin` selects matching records by key;
`GraphContext` retrieves a bounded N-hop knowledge-graph neighborhood. Both work
through `--config`, with the usual preview, scan, and cache workflows.

The original entry, reporting ID, and selected fields stay intact. Related data
is supplied separately as `request.state.context`. Every request remains
independent and contains all the evidence available to that assessment.

## Check an inventory against its category catalog

The [example bundle](../downloads/examples.zip) includes `references/items.yaml`,
`categories.csv`, and `jev_config.py`. From a checkout:

```sh
jt preview examples/references/items.yaml --config examples/references/jev_config.py
jt scan examples/references/items.yaml --config examples/references/jev_config.py
```

Preview is offline; an uncached scan requires `TYPESAFE_API_KEY`. The example
checks stacking limits that exist only in the reference catalog: ceramic cups
have `stack_count: 12`, but their category allows at most 4. Towels have a count
of 6 and a limit of 10. Model scores may vary.

The config selects `/stack_count` for assessment and declares the join:

```python
from pathlib import Path
from jevotron import Config, ReferenceJoin
from jevotron.parsers import YAML

HERE = Path(__file__).parent
config = Config(
    parser=YAML(id_column="id", fields=["/stack_count"]),
    guidance="stack_count must not exceed the category's max_stack.",
    references=[
        ReferenceJoin(
            name="category_limits",
            path=HERE / "categories.csv",
            source_key="/category",
            reference_key="/id",
            fields=["/max_stack"],
        )
    ],
)
```

Keys and field selections use exact JSON Pointers. Keys must be nonempty JSON
scalars; `1`, `"1"`, and `true` are different keys. CSV values are strings, so
ensure both sides use compatible key types. `fields=None` supplies the entire
reference record. A field selection supplies a mapping of pointer to value,
for example `{"/max_stack": "4"}`; it does not change assessed field paths.

Reference files use the built-in parsers, including CSV, JSON, and YAML.
`records="/categories"` selects a nested collection in JSON/YAML/TOML. A config
can declare several references with distinct names.

### Missing and duplicate keys

By default, a missing match or duplicate reference key is an error. For an
intentional one-to-many join, set `many=True`; all matches are included in
canonical order. Duplicate matching records retain their multiplicity.

Set `missing="include"` to assess missing references: the context explicitly
contains `missing: true` and an empty record list. State how to judge a missing
match in guidance. An absent source-key field or an invalid key is always an
input error.

## Retrieve an N-hop knowledge-graph neighborhood

The same example includes node and edge files. A product points to its category,
which points to a handling rule. Two hops reach the rule needed for assessment:

```sh
jt preview examples/references/items.yaml --config examples/references/graph_config.py
jt scan examples/references/items.yaml --config examples/references/graph_config.py
```

Declare traversal explicitly:

```python
from pathlib import Path
from jevotron import Config, GraphContext
from jevotron.parsers import YAML

HERE = Path(__file__).parent
config = Config(
    parser=YAML(id_column="id", fields=["/stack_count"]),
    guidance="Follow in_category then handling_rule. The item's stack_count "
    "must not exceed the reached handling rule's max_stack.",
    references=[
        GraphContext(
            name="handling_rules",
            nodes=HERE / "nodes.json",
            edges=HERE / "edges.json",
            source_key="/id",
            relations=["in_category", "handling_rule"],
            hops=2,
            direction="outgoing",
            max_nodes=10,
            max_edges=10,
        )
    ],
)
```

The node file contains objects with `/id`; the edge file contains objects with
`/source`, `/target`, and `/relation`. These pointers are configurable through
`node_key`, `edge_source`, `edge_target`, and `edge_relation`. Use `node_records`
or `edge_records` for wrapped collections. This accepts explicit node/edge
exports; it does not perform RDF/OWL reasoning or automatically extract OBO
relationships. An OBO node file can use `node_key="/id/0"` with a separate edge
file.

`source_key` chooses the focal node from the entry. Omit it to use `Chunk.id`.
`direction` can be `outgoing`, `incoming`, or `both`; `relations` must explicitly
list permitted relationship names. `hops` must be a positive integer. Traversal
visits each node once, so cycles and alternate paths do not loop. Nodes include
their shortest hop distance. The result contains **all permitted edges between
reached nodes**, including edges between nodes at the hop boundary, with their
original directions. Identical duplicate edges are collapsed; distinct edge
attributes are preserved.

Node keys must be unique. All included relationship types are checked for
missing endpoints when the graph is indexed; a dangling edge is an error.
A missing focal node follows `missing="error"` or `missing="include"`, as for
joins. `fields` projects node attributes; edge data is retained in full.

## Bounds, provenance, and caching

Each join defaults to `max_records=50`, `max_bytes=32768`. Each graph defaults
to `max_nodes=100`, `max_edges=200`, `max_bytes=65536`. The focal graph node counts
toward `max_nodes`. Byte limits measure the UTF-8 encoding of the complete
canonical JSON context item, including its lookup settings. They are **not
token estimates**. Guidance, the focal entry, and other named contexts add to
the overall model input.

Exceeding a limit raises an error before the affected entry's API call. Context
is never silently truncated. Earlier completed entries may already be cached;
normal incomplete-run handling applies. Preview the complete file with a
sufficient `--limit` to inspect all entries before scanning.

Reference files are loaded and indexed in memory once when a preview/scan
iterator starts. A run uses that snapshot; start a new run to pick up file
edits. Limits bound the supplied context, not the in-memory source index.

Preview and JSONL results include `context.data` (the exact supplied reference
content) and `context.provenance` (source files, record keys/IDs, locations,
lookup settings, and limits). Join provenance uses `record_index` to identify the exact canonical context
record; graph provenance uses `edge_index` for edges. Wrapped-collection
selectors are retained in provenance settings. Graph edges retain endpoints and
relationships so a reviewer can follow evidence paths. CSV reports add a JSON `context` column
when references are configured. Cached reports use current source locations
while retaining the original assessment timestamp.

Cache identity includes the actual supplied context and traversal settings.
Changing a reached node, selected reference attribute, or relevant edge requires
reassessment. Unrelated reference edits and file/row reordering do not. Paths and
source line numbers are provenance only and do not trigger reassessment. Raising
a limit without changing the supplied evidence does not require a new call.

Without `references`, existing requests, reports, and cache keys remain
unchanged. Related records provide evidence; only the original entry's selected
fields receive scores.
