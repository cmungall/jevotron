from pathlib import Path

from jevotron import Config
from jevotron.parsers import CSV

HERE = Path(__file__).parent

config = Config(
    parser=CSV(
        id_column="ident", fields=["/iso_country", "/latitude_deg", "/longitude_deg"]
    ),
    guidance=(HERE / "guidance.md").read_text(encoding="utf-8"),
)
