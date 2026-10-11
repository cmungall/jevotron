"""Minimal Jev and OpenAI Decisions HTTP adapters.

No credentials are stored in requests or caches.
"""

import json
import math
import os
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
OPENAI_ENDPOINT = "https://api.openai.com/v1/decisions"


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


class RefusalError(JevError):
    """The service declined to answer the question named `question`."""

    def __init__(self, message: str, question: str):
        super().__init__(message)
        self.question = question


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
    service = "Jev"
    endpoint = ENDPOINT
    key_env = "TYPESAFE_API_KEY"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 60,
        transport: httpx.BaseTransport | None = None,
    ):
        self._key = api_key or os.environ.get(self.key_env)
        if not self._key:
            raise JevError(f"Set {self.key_env} to assess uncached entries")
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def encode(self, payload: dict[str, Any]) -> dict[str, Any]:
        """The wire request for a canonical (Jev-shaped) request."""
        return payload

    def decode(self, data: Any) -> Any:
        """A canonical (Jev-shaped) response; the runner validates it."""
        return data

    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]:
        wire = self.encode(payload)
        name = self.service
        for attempt in range(3):
            try:
                response = self._http.post(
                    self.endpoint,
                    json=wire,
                    headers={"Authorization": f"Bearer {self._key}"},
                )
            except (httpx.TransportError, httpx.DecodingError):
                # Decoding happens inside post(), before we can inspect the
                # status. Retry unreadable responses like connection failures.
                if attempt == 2:
                    raise JevError(f"{name} request failed after 3 attempts") from None
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
                    raise ContextLimitError(
                        f"{name} rejected the input token/context size"
                    )
                # Do not echo API response bodies, request headers, or credentials.
                raise JevError(f"{name} HTTP {response.status_code}; scan stopped")
            try:
                data = response.json()
            except ValueError:
                raise JevError(f"{name} returned invalid JSON") from None
            return self.decode(data)
        raise JevError(f"{name} request failed")

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def is_openai_model(model: str) -> bool:
    """OpenAI Decisions models (e.g. gpt-6-luna); everything else is Jev."""
    return model.startswith("gpt-")


def service_name(model: Any) -> str:
    """The service named in errors about `model`'s assessments."""
    if isinstance(model, str) and is_openai_model(model):
        return OpenAIDecisionsClient.service
    return JevClient.service


def _openai_instructions(instructions: dict[str, Any]) -> str:
    # Decisions takes text instructions. Keep the field selection (and, for
    # shared layouts, the entry) as JSON data after the question.
    details = {k: v for k, v in instructions.items() if k != "question"}
    return (
        f"{instructions['question']}\n\n"
        "The selected field, as JSON data:\n"
        f"{json.dumps(details, sort_keys=True, ensure_ascii=False, allow_nan=False)}"
    )


def to_openai(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate a canonical Jev-shaped request into a Decisions request.

    Jev's `state` becomes the single-turn text input; each choice question
    keeps its key as the question name, so answers route back unchanged.

    >>> to_openai({
    ...     "model": "gpt-6-luna",
    ...     "state": {"entry": {"a": 1}},
    ...     "questions": {"field_0": {
    ...         "type": "choice",
    ...         "instructions": {"question": "Q?", "field_path": "/a"},
    ...         "criteria": {"NORMAL": "Fine", "ANOMALY": None},
    ...     }},
    ... })["questions"][0]["choices"]
    [{'value': 'NORMAL', 'description': 'Fine'}, {'value': 'ANOMALY'}]
    """
    return {
        "model": payload["model"],
        "input": "The JSON data to assess (`state`):\n"
        + json.dumps(
            payload["state"], sort_keys=True, ensure_ascii=False, allow_nan=False
        ),
        "questions": [
            {
                "name": key,
                "type": question["type"],
                "instructions": _openai_instructions(question["instructions"]),
                "choices": [
                    {"value": label}
                    | ({"description": text} if text is not None else {})
                    for label, text in question["criteria"].items()
                ],
            }
            for key, question in payload["questions"].items()
        ],
    }


def from_openai(data: Any) -> dict[str, Any]:
    """Translate a Decisions response into the canonical Jev shape.

    Refusals and malformed answers are errors, so they are never cached.
    """
    try:
        answers = {}
        for answer in data["answers"]:
            name = answer["name"]
            if answer["type"] == "refusal":
                raise RefusalError(
                    "OpenAI Decisions declined to answer a question; not cached",
                    name,
                )
            probabilities = {}
            for item in answer["probabilities"]:
                if item["value"] in probabilities:
                    raise ValueError
                probabilities[item["value"]] = item["probability"]
            # Dicts would silently drop duplicates; the runner checks the set.
            if name in answers:
                raise ValueError
            answers[name] = {
                "type": answer["type"],
                "choice": answer["choice"],
                "probabilities": probabilities,
                "confidence": answer["confidence"],
            }
        return {
            "model": data["model"],
            "answers": answers,
            "usage": data.get("usage", {}),
        }
    except (KeyError, TypeError, ValueError):
        raise JevError(
            "OpenAI Decisions returned an invalid or incomplete assessment; not cached"
        ) from None


class OpenAIDecisionsClient(JevClient):
    """The same evaluate() contract, spoken to the OpenAI Decisions API."""

    service = "OpenAI Decisions"
    endpoint = OPENAI_ENDPOINT
    key_env = "OPENAI_API_KEY"

    def encode(self, payload: dict[str, Any]) -> dict[str, Any]:
        return to_openai(payload)

    def decode(self, data: Any) -> Any:
        return from_openai(data)
