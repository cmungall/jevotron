"""A minimal Jev HTTP adapter; no credentials are stored in requests or caches."""

import math
import os
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _retry_delay(value: str | None, backoff: float) -> float:
    if value is None:
        return backoff
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            # The obsolete HTTP asctime format has no explicit timezone.
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = retry_at.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError, OSError):
            return backoff
    if not math.isfinite(seconds):
        return backoff
    return min(30, max(backoff, seconds))


class JevError(RuntimeError):
    pass


class JevClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ):
        self._key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self._key:
            raise JevError("Set TYPESAFE_API_KEY to assess uncached entries")
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(3):
            try:
                response = self._http.post(
                    ENDPOINT,
                    json=payload,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
            except httpx.TransportError:
                if attempt == 2:
                    raise JevError("Jev connection failed after 3 attempts") from None
                time.sleep(2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    delay = _retry_delay(
                        response.headers.get("Retry-After"), 2**attempt
                    )
                    time.sleep(delay)
                    continue
            if not response.is_success:
                # Do not echo API response bodies, request headers, or credentials.
                raise JevError(f"Jev HTTP {response.status_code}; scan stopped")
            try:
                return response.json()
            except ValueError:
                raise JevError("Jev returned invalid JSON") from None
        raise JevError("Jev request failed")

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
