from jevotron import Chunk, Config
from jevotron.parsers import OBO


def units(path):
    """Wrap a built-in parser with a few lines of project-specific Python."""
    for chunk in OBO()(path):
        yield Chunk(
            id=chunk.id,
            data=chunk.data,
            fields=[
                p for p in chunk.field_paths() if p.startswith(("/def/", "/synonym/"))
            ],
            source=chunk.source,
        )


config = Config(
    parser=units,
    guidance="These stanzas describe measurement units. Definitions and synonyms should "
    "match the named unit. Identifiers are arbitrary local identifiers. "
    "Judge only the current stanza; no ontology graph is provided.",
)
