"""Configuration is ordinary, explicitly loaded Python."""

import importlib.util
import math
import sys
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any

from jevotron.models import Chunk, validate_json

Parser = Callable[[Path], Iterable[Chunk]]
_CONFIG_IMPORT_LOCK = RLock()


@dataclass
class Config:
    parser: Parser | None = None
    guidance: str = ""
    exemplars: list[Any] = field(default_factory=list)
    labels: list[str] = field(default_factory=lambda: ["NORMAL", "ANOMALY"])
    criteria: dict[str, str | None] | None = None
    anomaly_label: str = "ANOMALY"
    model: str = "jev-1.13.0"
    threshold: float = 0.5

    def validate(self) -> None:
        if self.parser is not None and not callable(self.parser):
            raise ValueError("Config.parser must be callable")
        if (
            not isinstance(self.labels, list)
            or not 2 <= len(self.labels) <= 255
            or any(not isinstance(label, str) or not label for label in self.labels)
            or len(set(self.labels)) != len(self.labels)
        ):
            raise ValueError(
                "Config.labels must contain 2–255 distinct nonempty strings"
            )
        if self.anomaly_label not in self.labels:
            raise ValueError("Config.anomaly_label must be in Config.labels")
        if self.criteria is not None:
            if not isinstance(self.criteria, dict) or set(self.criteria) != set(
                self.labels
            ):
                raise ValueError("Config.criteria must describe exactly Config.labels")
            if any(
                v is not None and not isinstance(v, str) for v in self.criteria.values()
            ):
                raise ValueError("Criteria descriptions must be strings or None")
        if not isinstance(self.guidance, str):
            raise ValueError("Config.guidance must be text")
        if not isinstance(self.exemplars, list):
            raise ValueError("Config.exemplars must be a list")
        validate_json(self.exemplars)
        if not self.model or not isinstance(self.model, str):
            raise ValueError("Config.model must be a nonempty string")
        if (
            isinstance(self.threshold, bool)
            or not isinstance(self.threshold, (int, float))
            or not math.isfinite(self.threshold)
            or not 0 <= self.threshold <= 1
        ):
            raise ValueError("Config.threshold must be between 0 and 1")

    def choice_criteria(self) -> dict[str, str | None]:
        if self.criteria is not None:
            return dict(self.criteria)
        defaults = {
            "NORMAL": "The field appears correct and consistent with the entry and guidance. "
            "Unusual but valid values are normal.",
            "ANOMALY": "The field appears incorrect, internally inconsistent, or contrary "
            "to the supplied guidance, and merits human review.",
        }
        return {label: defaults.get(label) for label in self.labels}


def _sibling_roots(directory: Path) -> set[str]:
    roots = {"_jevotron_local_config"}
    for child in directory.iterdir():
        if child.is_file() and child.suffix == ".py":
            name = child.stem
        elif child.is_dir() and child.name.isidentifier():
            name = child.name
        else:
            continue
        # Ask the normal finders without consulting sys.modules. A namespace
        # directory does not win over a regular installed package, and local
        # files do not win over built-in/frozen modules.
        for finder in sys.meta_path:
            spec = finder.find_spec(name, None)
            if spec is not None:
                break
        else:
            continue
        if spec.origin not in (None, "built-in", "frozen"):
            origin = Path(spec.origin)
            if origin == child or origin.is_relative_to(child):
                roots.add(name)
        elif spec.origin is None and spec.submodule_search_locations is not None:
            if str(child) in spec.submodule_search_locations:
                roots.add(name)
    return roots


@contextmanager
def _local_imports(directory: Path):
    """Scope sibling imports to this load without changing the caller's modules."""
    # Module caching is process-wide, as is sys.path. Serialize our loaders,
    # including nested loads, while preserving imports owned by the caller.
    with _CONFIG_IMPORT_LOCK:
        previous_path = sys.path[:]
        try:
            sys.path.insert(0, str(directory))
            importlib.invalidate_caches()
            roots = _sibling_roots(directory)

            def local(name):
                return name.partition(".")[0] in roots

            previous_modules = {
                name: module
                for name, module in sys.modules.copy().items()
                if local(name)
            }
            try:
                for name in previous_modules:
                    sys.modules.pop(name, None)
                yield
            finally:
                for name in list(sys.modules):
                    if local(name):
                        del sys.modules[name]
                sys.modules.update(previous_modules)
        finally:
            sys.path[:] = previous_path


def load_config(path: Path) -> Config:
    """Execute a trusted local file exporting `config = Config(...)`."""
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Config file does not exist: {path}")
    spec = importlib.util.spec_from_file_location("_jevotron_local_config", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load Python config: {path}")
    module = importlib.util.module_from_spec(spec)
    with _local_imports(path.parent):
        # Register during execution so dataclass decorators can resolve globals.
        sys.modules[spec.name] = module
        # Read the current source instead of a same-mtime/same-size stale .pyc
        # after an agent or editor rapidly rewrites a local config.
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    config = getattr(module, "config", None)
    if not isinstance(config, Config):
        raise ValueError("The config file must export `config = Config(...)`")
    config.validate()
    return config
