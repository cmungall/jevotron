"""Explicit built-in adapters. Discovery never imports a provider or reads credentials."""

from dataclasses import dataclass
from importlib import import_module
from types import ModuleType


@dataclass(frozen=True)
class Adapter:
    name: str
    sources: tuple[str, ...]
    module: str
    description: str
    collects: bool = False

    def load(self) -> ModuleType:
        # Only trusted, explicitly registered module paths; never from source data.
        return import_module(self.module)


ADAPTERS = (
    Adapter(
        "bluesky",
        ("bluesky-public", "synthetic-bluesky"),
        "jevotron.extensions.feeds.adapters.bluesky",
        "Bounded unauthenticated public collection; existing HTTPX dependency",
        collects=True,
    ),
    Adapter(
        "local",
        ("public-import", "synthetic-feed"),
        "jevotron.extensions.feeds.adapters.local",
        "Normalized public/synthetic JSONL only; no retrieval or extra dependencies",
    ),
)


def get_adapter(name: str) -> Adapter:
    for adapter in ADAPTERS:
        if adapter.name == name:
            return adapter
    raise ValueError(f"Unsupported feed adapter: {name}")


def adapter_for_source(source: str) -> Adapter:
    for adapter in ADAPTERS:
        if source in adapter.sources:
            return adapter
    raise ValueError(f"Unsupported feed source: {source}")
