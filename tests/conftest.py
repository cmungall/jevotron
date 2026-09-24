import copy

import pytest


class FakeJev:
    """Deterministic evaluator: the literal value BAD is anomalous."""

    def __init__(self):
        self.requests = []
        self.closed = False

    def evaluate(self, request):
        self.requests.append(copy.deepcopy(request))
        answers = {}
        for key, question in request["questions"].items():
            labels = list(question["criteria"])
            bad = question["instructions"]["field_value"] == "BAD"
            probabilities = {
                labels[0]: 0.1 if bad else 0.9,
                labels[1]: 0.9 if bad else 0.1,
            }
            answers[key] = {
                "type": "choice",
                "choice": max(probabilities, key=probabilities.get),
                "probabilities": probabilities,
                "confidence": 0.8,
            }
        return {"model": "jev-test", "answers": answers, "usage": {"input_tokens": 50}}

    def close(self):
        self.closed = True


@pytest.fixture
def fake():
    return FakeJev()


@pytest.fixture
def cache_path(tmp_path):
    return tmp_path / "cache.sqlite3"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """The unit suite must never use the developer's real credentials."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
