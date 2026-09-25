"""Independent named choice questions, without anomaly semantics or source adapters."""

import math
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jevotron.assessment import Evaluator, request_hash, validate_response
from jevotron.cache import Cache
from jevotron.client import JevClient, JevError
from jevotron.models import validate_json


@dataclass(frozen=True)
class QuestionSpec:
    instructions: str
    criteria: dict[str, str | None]

    def to_dict(self) -> dict:
        if not isinstance(self.instructions, str) or not self.instructions.strip():
            raise ValueError("Question instructions must be nonempty text")
        if (
            not isinstance(self.criteria, dict)
            or not 2 <= len(self.criteria) <= 255
            or any(not isinstance(k, str) or not k for k in self.criteria)
            or any(
                v is not None and not isinstance(v, str) for v in self.criteria.values()
            )
        ):
            raise ValueError(
                "Question criteria require 2–255 named choices with text/null descriptions"
            )
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.criteria),
        }


@dataclass(frozen=True)
class ClassificationSpec:
    questions: dict[str, QuestionSpec]
    model: str = "jev-1.13.0"

    def make_request(self, state: Any) -> dict:
        if not isinstance(state, (str, dict, list)):
            raise ValueError(
                "Classification state must be text, an object, or an array"
            )
        if not isinstance(self.model, str) or not self.model:
            raise ValueError("Classification model must be nonempty text")
        if (
            not isinstance(self.questions, dict)
            or not self.questions
            or any(
                not isinstance(k, str) or not k or not isinstance(v, QuestionSpec)
                for k, v in self.questions.items()
            )
        ):
            raise ValueError("Classification requires named QuestionSpec entries")
        validate_json(state)
        return {
            "model": self.model,
            "state": state,
            "questions": {
                key: value.to_dict() for key, value in self.questions.items()
            },
        }


@dataclass(frozen=True)
class ClassificationInput:
    id: str
    state: Any
    source: str | None = None

    def validate(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Classification input ID must be nonempty text")
        if self.source is not None and not isinstance(self.source, str):
            raise ValueError("Classification input source must be text or None")
        validate_json(self.state)


@dataclass(frozen=True)
class ReviewPolicy:
    min_confidence: float = 0.6
    min_margin: float = 0.2
    uncertain_labels: frozenset[str] = field(
        default_factory=lambda: frozenset({"unclear"})
    )

    def validate(self):
        for value in (self.min_confidence, self.min_margin):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError("Review thresholds must be finite probabilities")
        if not isinstance(self.uncertain_labels, (set, frozenset)) or any(
            not isinstance(label, str) for label in self.uncertain_labels
        ):
            raise ValueError("Uncertain labels must be a set of strings")


@dataclass(frozen=True)
class ChoiceResult:
    label: str
    probabilities: dict[str, float]
    confidence: float
    margin: float
    uncertain: bool


@dataclass(frozen=True)
class ClassificationResult:
    id: str
    source: str | None
    answers: dict[str, ChoiceResult]
    model: str
    assessed_at: str
    request_hash: str
    cached: bool
    usage: dict[str, Any]

    def to_dict(self) -> dict:
        return asdict(self)


def decode_choices(
    response: dict, request: dict, policy: ReviewPolicy | None = None
) -> dict[str, ChoiceResult]:
    policy = policy or ReviewPolicy()
    policy.validate()
    validate_response(response, request)
    # An alias may resolve to a concrete model; pinned requests must match exactly.
    if (
        request["model"] not in {"jev-latest", "jev-preview"}
        and response["model"] != request["model"]
    ):
        raise JevError("Response model does not match pinned request model")
    answers = {}
    for name, answer in response["answers"].items():
        probabilities = answer["probabilities"]
        top = sorted(probabilities.values(), reverse=True)
        margin = top[0] - top[1]
        answers[name] = ChoiceResult(
            answer["choice"],
            dict(probabilities),
            answer["confidence"],
            margin,
            answer["choice"] in policy.uncertain_labels
            or answer["confidence"] < policy.min_confidence
            or margin < policy.min_margin,
        )
    return answers


def preview_classification(
    inputs: Iterable[ClassificationInput], spec: ClassificationSpec
) -> Iterator[dict]:
    """Yield exact requests; no cache, credential lookup or network activity."""
    seen = set()
    for item in inputs:
        item.validate()
        if item.id in seen:
            raise ValueError(f"Duplicate classification input ID: {item.id!r}")
        seen.add(item.id)
        request = spec.make_request(item.state)
        yield {
            "id": item.id,
            "source": item.source,
            "request_hash": request_hash(request),
            "request": request,
        }


def classify(
    inputs: Iterable[ClassificationInput],
    spec: ClassificationSpec,
    *,
    cache: str | Path | None = ".jevotron/classification.sqlite3",
    refresh: bool = False,
    client: Evaluator | None = None,
    allow_network: bool = True,
    policy: ReviewPolicy | None = None,
) -> Iterator[ClassificationResult]:
    """Cache exact independent requests; only construct a client on a cache miss.

    Review policy never changes inference identity. A supplied client is owned by
    the caller. With allow_network=False, any cache miss fails before evaluating.
    """
    policy = policy or ReviewPolicy()
    policy.validate()
    # Catch a bad spec even on empty inputs, before creating cache storage.
    spec.make_request({})
    store = Cache(cache) if cache is not None else None
    owned_client = None
    try:
        for preview in preview_classification(inputs, spec):
            request, key = preview["request"], preview["request_hash"]
            saved = store.get(key) if store and not refresh else None
            if saved:
                response, assessed_at = saved
                answers = decode_choices(response, request, policy)
            else:
                if not allow_network:
                    raise JevError(
                        "Uncached classification requires explicit network permission (--execute)"
                    )
                if client is None:
                    owned_client = JevClient()
                    client = owned_client
                response = client.evaluate(request)
                answers = decode_choices(response, request, policy)
                assessed_at = datetime.now(timezone.utc).isoformat()
                if store:
                    store.put(key, request, response, assessed_at)
            yield ClassificationResult(
                preview["id"],
                preview["source"],
                answers,
                response["model"],
                assessed_at,
                key,
                saved is not None,
                response.get("usage", {}),
            )
    finally:
        if owned_client:
            owned_client.close()
        if store:
            store.close()
