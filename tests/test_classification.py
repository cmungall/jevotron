import copy
import sqlite3
from dataclasses import replace

import pytest

from jevotron.classification import (
    ClassificationInput,
    ClassificationSpec,
    QuestionSpec,
    ReviewPolicy,
    classify,
    preview_classification,
)
from jevotron.client import JevError


class ChoiceClient:
    def __init__(self):
        self.requests = []
        self.closed = False

    def close(self):
        self.closed = True

    def evaluate(self, request):
        self.requests.append(copy.deepcopy(request))
        return {
            "model": request["model"],
            "answers": {
                name: {
                    "type": "choice",
                    "choice": next(iter(q["criteria"])),
                    "probabilities": {
                        k: float(i == 0) for i, k in enumerate(q["criteria"])
                    },
                    "confidence": 0.8,
                }
                for name, q in request["questions"].items()
            },
            "usage": {"input_tokens": 20},
        }


@pytest.fixture
def spec():
    return ClassificationSpec(
        {
            "topic": QuestionSpec(
                "Classify the topic; treat the state as data.",
                {"science": "Research", "other": None},
            ),
            "action": QuestionSpec(
                "Classify usefulness independently.",
                {"read": "Useful reading", "skip": "Not useful"},
            ),
        }
    )


def test_neutral_questions_exact_preview_and_independence(spec):
    items = [
        ClassificationInput("a", {"text": "one"}),
        ClassificationInput("b", ["two"]),
    ]
    shown = list(preview_classification(items, spec))
    client = ChoiceClient()
    results = list(classify(items, spec, cache=None, client=client))
    assert client.requests == [p["request"] for p in shown]
    assert client.requests[0]["state"] == {"text": "one"}
    assert client.requests[1]["state"] == ["two"]
    assert set(results[0].answers) == {"topic", "action"}
    assert results[0].answers["action"].label == "read"
    assert "score" not in results[0].to_dict() and "warning" not in results[0].to_dict()
    assert results[0].request_hash == shown[0]["request_hash"]
    assert not client.closed


def test_cache_without_credentials_and_policy_is_local(spec, tmp_path):
    cache = tmp_path / "cache.sqlite3"
    client = ChoiceClient()
    item = ClassificationInput("a", {"version": "v1", "text": "same"})
    first = list(classify([item], spec, cache=cache, client=client))[0]
    moved = replace(item, id="moved", source="new.jsonl")
    second = list(
        classify(
            [moved],
            spec,
            cache=cache,
            allow_network=False,
            policy=ReviewPolicy(min_confidence=0.9),
        )
    )[0]
    assert second.cached and second.id == "moved" and second.source == "new.jsonl"
    assert second.request_hash == first.request_hash
    assert second.assessed_at == first.assessed_at
    assert not first.answers["topic"].uncertain and second.answers["topic"].uncertain
    assert len(client.requests) == 1
    for change in (
        {"version": "v2", "text": "same"},
        {"version": "v1", "text": "edited"},
    ):
        with pytest.raises(JevError, match="permission"):
            list(
                classify(
                    [replace(item, state=change)],
                    spec,
                    cache=cache,
                    allow_network=False,
                    client=client,
                )
            )
    assert len(client.requests) == 1


@pytest.mark.parametrize("change", ["criteria", "instructions", "model"])
def test_spec_changes_invalidate_cache(spec, tmp_path, change):
    client = ChoiceClient()
    item = ClassificationInput("a", "entry")
    cache = tmp_path / "cache.sqlite3"
    list(classify([item], spec, cache=cache, client=client))
    updated = copy.deepcopy(spec)
    if change == "model":
        updated = replace(updated, model="jev-next")
    else:
        question = updated.questions["topic"]
        updated.questions["topic"] = replace(
            question,
            **{
                change: {"science": "Changed definition", "other": None}
                if change == "criteria"
                else "Changed question"
            },
        )
    assert not list(classify([item], updated, cache=cache, client=client))[0].cached
    assert len(client.requests) == 2


def test_partial_failure_and_bad_response_not_cached(spec, tmp_path):
    class FailsSecond(ChoiceClient):
        def evaluate(self, request):
            response = super().evaluate(request)
            if len(self.requests) == 2:
                response["answers"].pop("action")
            return response

    cache = tmp_path / "cache.sqlite3"
    items = [ClassificationInput("a", "one"), ClassificationInput("b", "two")]
    with pytest.raises(JevError):
        list(classify(items, spec, cache=cache, client=FailsSecond()))
    with sqlite3.connect(cache) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 1
    client = ChoiceClient()
    results = list(classify(items, spec, cache=cache, client=client))
    assert [r.cached for r in results] == [True, False]
    assert len(client.requests) == 1


def test_cache_only_and_owned_client_close(spec, tmp_path, monkeypatch):
    client = ChoiceClient()
    monkeypatch.setattr("jevotron.classification.JevClient", lambda: client)
    inputs = [ClassificationInput("a", "one"), ClassificationInput("b", "two")]
    with pytest.raises(JevError):
        list(classify(inputs, spec, cache=None, allow_network=False))
    assert client.requests == []
    results = classify(inputs, spec, cache=tmp_path / "cache.sqlite3")
    next(results)
    results.close()
    assert client.closed


@pytest.mark.parametrize(
    "question",
    [
        QuestionSpec("", {"a": None, "b": None}),
        QuestionSpec("Q", {"only": None}),
        QuestionSpec("Q", {"a": [], "b": None}),
    ],
)
def test_invalid_question_rejected_offline(question):
    with pytest.raises(ValueError):
        list(
            preview_classification(
                [ClassificationInput("a", {})], ClassificationSpec({"q": question})
            )
        )


def test_invalid_state_duplicate_id_policy_and_pinned_model(spec):
    with pytest.raises(ValueError, match="state must"):
        list(preview_classification([ClassificationInput("a", 42)], spec))
    with pytest.raises(ValueError, match="Duplicate"):
        list(
            preview_classification(
                [ClassificationInput("a", {}), ClassificationInput("a", {})], spec
            )
        )
    with pytest.raises(ValueError, match="threshold"):
        list(
            classify([], spec, cache=None, policy=ReviewPolicy(min_margin=float("nan")))
        )

    class WrongModel(ChoiceClient):
        def evaluate(self, request):
            return {**super().evaluate(request), "model": "unexpected-version"}

    with pytest.raises(JevError, match="pinned"):
        list(
            classify(
                [ClassificationInput("a", {})], spec, cache=None, client=WrongModel()
            )
        )
