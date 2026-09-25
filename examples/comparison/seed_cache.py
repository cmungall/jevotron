"""Seed synthetic airport assessments for an offline comparison demonstration.

Usage: uv run python examples/comparison/seed_cache.py /tmp/airport-fixture.sqlite3
The dedicated jev-offline-fixture model keeps these separate from real scans.
"""

import json
import sys
from pathlib import Path

from jevotron.cache import Cache
from jevotron.runner import request_hash, validate_response


def seed(path: Path) -> None:
    fixture = json.loads(Path(__file__).with_name("airports-offline.json").read_text())
    with Cache(path) as cache:
        for assessment in fixture["assessments"]:
            request = assessment["request"]
            response = assessment["response"]
            key = request_hash(request)
            if key != assessment["request_hash"]:
                raise ValueError("Fixture request hash does not match its request")
            if request["model"] != "jev-offline-fixture":
                raise ValueError("Fixture must use its dedicated synthetic model")
            validate_response(response, request)
            cache.put(key, request, response, assessment["assessed_at"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: seed_cache.py CACHE_PATH")
    seed(Path(sys.argv[1]))
