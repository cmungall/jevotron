from jevotron import Config
from jevotron.parsers import YAML

config = Config(
    parser=YAML(id_column="id", fields=["/category", "/quantity", "/unit_price_usd"]),
    guidance="These are physical items currently in stock. Quantity must be a nonnegative "
    "integer and unit_price_usd must be nonnegative. The category must match the "
    "description. Categories are stationery, electronics, and kitchenware.",
    exemplars=[
        {
            "entry": {
                "description": "Ceramic dinner plate",
                "category": "kitchenware",
                "quantity": 2,
                "unit_price_usd": 10.00,
            },
            "assessment": {
                "/category": "NORMAL",
                "/quantity": "NORMAL",
                "/unit_price_usd": "NORMAL",
            },
        }
    ],
)
