"""Canonical local identities and timestamps for feed adapters."""

import hashlib
from datetime import datetime
from typing import Any

from jevotron.models import json_text


def digest(value: Any) -> str:
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def timestamp(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Timestamps must have a timezone")
    return result
