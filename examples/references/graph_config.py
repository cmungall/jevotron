from pathlib import Path

from jevotron import Config, GraphContext
from jevotron.parsers import YAML

HERE = Path(__file__).parent
config = Config(
    parser=YAML(id_column="id", fields=["/stack_count"]),
    guidance="Follow the product's in_category edge and that category's handling_rule "
    "edge. stack_count must not exceed the reached handling rule's max_stack. "
    "The supplied graph is authoritative for these rules.",
    references=[
        GraphContext(
            name="handling_rules",
            nodes=HERE / "nodes.json",
            edges=HERE / "edges.json",
            source_key="/id",
            relations=["in_category", "handling_rule"],
            hops=2,
            max_nodes=10,
            max_edges=10,
        )
    ],
)
