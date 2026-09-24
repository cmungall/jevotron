"""Configuration is ordinary, explicitly loaded Python."""

import importlib.util
import math
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jevotron.models import Chunk, validate_json

Parser = Callable[[Path], Iterable[Chunk]]


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


def load_config(path: Path) -> Config:
    """Execute a trusted local file exporting `config = Config(...)`."""
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Config file does not exist: {path}")
    spec = importlib.util.spec_from_file_location("_jevotron_local_config", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Cannot load Python config: {path}")
    module = importlib.util.module_from_spec(spec)
    # Permit `from helpers import ...` next to the config, without changing cwd.
    previous_path = sys.path[:]
    sys.path.insert(0, str(path.parent))
    previous_module = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        # Read the current source instead of a same-mtime/same-size stale .pyc
        # after an agent or editor rapidly rewrites a local config.
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    finally:
        sys.path[:] = previous_path
        if previous_module is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous_module
    config = getattr(module, "config", None)
    if not isinstance(config, Config):
        raise ValueError("The config file must export `config = Config(...)`")
    config.validate()
    return config
