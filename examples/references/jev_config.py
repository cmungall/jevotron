from pathlib import Path

from jevotron import Config, ReferenceJoin
from jevotron.parsers import YAML

HERE = Path(__file__).parent
config = Config(
    parser=YAML(id_column="id", fields=["/stack_count"]),
    guidance="The category catalog is authoritative. stack_count must not exceed "
    "the matching category's max_stack. Numeric CSV values are strings.",
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
