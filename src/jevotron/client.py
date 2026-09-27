"""A minimal Jev HTTP adapter; no credentials are stored in requests or caches."""

import math
import os
import re
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


class ContextLimitError(JevError):
    """The service explicitly rejected the input's token/context size."""


def _context_limit(response: httpx.Response) -> bool:
    if response.status_code not in (400, 413, 422):
        return False
    # Inspect but never echo service bodies (which may contain user data).
    # Generic validation and HTTP body-size errors are not token-limit errors.
    try:
        body = response.json()
    except ValueError:
        return False
    if not isinstance(body, dict):
        return False
    error = body.get("error", body)
    detail = body.get("detail", error)
    for container in (body, error, detail):
        if not isinstance(container, dict):
            continue
        code = container.get("error_type", container.get("code"))
        if isinstance(code, str) and code in {
            "context_length_exceeded",
            "context_window_exceeded",
            "input_tokens_exceeded",
            # Observed Jev response: HTTP 400, detail.error_type.
            "max_tokens_exceeded",
        }:
            return True
    message = detail.get("message", "") if isinstance(detail, dict) else detail
    if not isinstance(message, str):
        return False
    return bool(
        re.search(
            r"(?:context (?:length|window)|input tokens?|token (?:count|limit))"
            r".{0,80}(?:exceed|too (?:long|large)|maximum)|"
            r"(?:exceed|too (?:long|large)).{0,80}(?:context (?:length|window)|token limit)",
            message,
            re.IGNORECASE,
        )
    )


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
            except (httpx.TransportError, httpx.DecodingError):
                # Decoding happens inside post(), before we can inspect the
                # status. Retry unreadable responses like connection failures.
                if attempt == 2:
                    raise JevError("Jev request failed after 3 attempts") from None
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
                if _context_limit(response):
                    raise ContextLimitError("Jev rejected the input token/context size")
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
