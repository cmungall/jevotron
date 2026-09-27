"""Shared choice-response validation and request identity; no task-specific scoring."""

import hashlib
import math
from typing import Any, Protocol

from jevotron.client import ENDPOINT, JevError
from jevotron.models import json_text


class Evaluator(Protocol):
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def request_hash(request: dict) -> str:
    return hashlib.sha256(
        json_text({"endpoint": ENDPOINT, "request": request}).encode()
    ).hexdigest()


def _probability(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def validate_response(response: Any, request: dict) -> None:
    """Never persist failed, partial, or malformed assessments as successes."""
    try:
        if (
            not isinstance(response, dict)
            or not isinstance(response["model"], str)
            or not response["model"]
        ):
            raise ValueError
        answers = response["answers"]
        if not isinstance(answers, dict) or set(answers) != set(request["questions"]):
            raise ValueError
        for key, question in request["questions"].items():
            answer = answers[key]
            probabilities = answer["probabilities"]
            if (
                answer["type"] != "choice"
                or not isinstance(probabilities, dict)
                or set(probabilities) != set(question["criteria"])
                or not all(_probability(p) for p in probabilities.values())
                or not _probability(answer["confidence"])
            ):
                raise ValueError
            # Jev may round each option probability to two decimal places.
            rounded = all(
                math.isclose(p, round(p, 2), abs_tol=1e-12, rel_tol=0)
                for p in probabilities.values()
            )
            tolerance = 0.005 * len(probabilities) + 1e-12 if rounded else 0.001
            if not math.isclose(
                sum(probabilities.values()), 1, rel_tol=0, abs_tol=tolerance
            ):
                raise ValueError
            if answer["choice"] not in probabilities or probabilities[
                answer["choice"]
            ] < max(probabilities.values()):
                raise ValueError
        if not isinstance(response.get("usage", {}), dict):
            raise ValueError
        json_text(response)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise JevError(
            "Jev returned an invalid or incomplete assessment; not cached"
        ) from None
