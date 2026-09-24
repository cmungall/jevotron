"""A minimal Jev HTTP adapter; no credentials are stored in requests or caches."""

import os
import time
from typing import Any

import httpx

ENDPOINT = "https://api.typesafe.ai/v1/systemone"


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
                    delay = 2**attempt
                    try:
                        delay = min(
                            30,
                            max(delay, float(response.headers.get("Retry-After", 0))),
                        )
                    except ValueError:
                        pass
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
