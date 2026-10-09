import json

import httpx
import pytest

from jevotron.client import (
    OPENAI_ENDPOINT,
    JevClient,
    JevError,
    OpenAIDecisionsClient,
)
from jevotron.config import Config
from jevotron.models import Chunk
from jevotron.runner import preview, scan


def decisions_handler(requests):
    """Answer like OpenAI Decisions: the literal value BAD is anomalous."""

    def handler(request):
        assert str(request.url) == OPENAI_ENDPOINT
        assert request.headers["Authorization"] == "Bearer test-only-key"
        body = json.loads(request.content)
        requests.append(body)
        answers = []
        for question in body["questions"]:
            labels = [c["value"] for c in question["choices"]]
            bad = '"BAD"' in question["instructions"]
            p = [0.1, 0.9] if bad else [0.9, 0.1]
            answers.append(
                {
                    "type": "choice",
                    "name": question["name"],
                    "choice": labels[p.index(max(p))],
                    "probabilities": [
                        {"value": label, "probability": prob}
                        for label, prob in zip(labels, p)
                    ],
                    "confidence": 0.8,
                }
            )
        return httpx.Response(
            200,
            json={
                "model": "gpt-6-luna-2026-10-01",
                "answers": answers,
                "usage": {"input_tokens": 40, "output_tokens": 0},
            },
        )

    return handler


def client(handler):
    return OpenAIDecisionsClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    )


@pytest.mark.parametrize(
    "model, expected",
    [("gpt-6-luna", OpenAIDecisionsClient), ("jev-1.13.0", JevClient)],
)
def test_model_selects_backend(monkeypatch, model, expected):
    seen = []
    monkeypatch.setattr(
        expected, "evaluate", lambda self, payload: seen.append(type(self)) or {}
    )
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    with pytest.raises(JevError, match="invalid or incomplete"):
        list(scan([Chunk("a", {"v": 1})], Config(model=model), cache=None))
    assert seen == [expected]


def test_missing_openai_key_names_the_variable(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "only-jev")
    with pytest.raises(JevError, match="OPENAI_API_KEY"):
        list(scan([Chunk("a", {"v": 1})], Config(model="gpt-6-luna"), cache=None))


def test_scan_translates_request_and_response(cache_path):
    requests = []
    config = Config(model="gpt-6-luna", guidance="Check codes.")
    chunks = [Chunk("a", {"code": "BAD", "name": "x"}, source="t:1")]
    with client(decisions_handler(requests)) as c:
        [result] = scan(chunks, config, cache=cache_path, client=c)
    [wire] = requests
    assert wire["model"] == "gpt-6-luna"
    assert '"guidance": "Check codes."' in wire["input"]
    assert [q["name"] for q in wire["questions"]] == ["field_0", "field_1"]
    assert wire["questions"][0]["choices"][0]["value"] == "NORMAL"
    assert result.warning and result.model == "gpt-6-luna-2026-10-01"
    assert result.fields[0].probabilities == {"NORMAL": 0.1, "ANOMALY": 0.9}
    # Cached without credentials or network.
    [again] = scan(chunks, config, cache=cache_path)
    assert again.cached and again.score == result.score


def test_shared_batches_route_answers(cache_path):
    requests = []
    config = Config(model="gpt-6-luna")
    chunks = [
        Chunk(i, {"v": v}, source=f"t:{i}") for i, v in (("a", "ok"), ("b", "BAD"))
    ]
    with client(decisions_handler(requests)) as c:
        results = list(scan(chunks, config, cache=None, client=c, batch_size=2))
    assert len(requests) == 1
    assert [r.warning for r in results] == [False, True]


def test_preview_shows_wire_request_only_for_openai():
    chunks = [Chunk("a", {"v": 1}, source="t:1")]
    [item] = preview(chunks, Config(model="gpt-6-luna"))
    assert item["wire_request"]["questions"][0]["name"] == "field_0"
    [item] = preview(chunks, Config())
    assert "wire_request" not in item


@pytest.mark.parametrize(
    "answers",
    [
        [{"type": "refusal", "name": "field_0"}],
        [{"type": "choice", "name": "field_0"}],
        "not-a-list",
    ],
)
def test_refusals_and_malformed_answers_are_not_cached(cache_path, answers):
    def handler(request):
        return httpx.Response(
            200, json={"model": "gpt-6-luna", "answers": answers, "usage": {}}
        )

    with client(handler) as c, pytest.raises(JevError, match="not cached"):
        list(
            scan(
                [Chunk("a", {"v": 1}, source="t:1")],
                Config(model="gpt-6-luna"),
                cache=cache_path,
                client=c,
            )
        )
    with pytest.raises(JevError, match="OPENAI_API_KEY"):
        list(
            scan(
                [Chunk("a", {"v": 1}, source="t:1")],
                Config(model="gpt-6-luna"),
                cache=cache_path,
            )
        )


def test_errors_name_the_service_without_leaking_body():
    def handler(request):
        return httpx.Response(401, text="sensitive-service-body")

    with client(handler) as c, pytest.raises(JevError) as error:
        c.evaluate({"model": "gpt-6-luna", "state": {}, "questions": {}})
    assert str(error.value) == "OpenAI Decisions HTTP 401; scan stopped"


def openai_answer(probabilities, **extra):
    return httpx.Response(
        200,
        json={
            "model": "gpt-6-luna",
            "answers": [
                {
                    "type": "choice",
                    "name": "field_0",
                    "choice": "NORMAL",
                    "probabilities": [
                        {"value": k, "probability": v} for k, v in probabilities
                    ],
                    "confidence": 0.9,
                }
            ],
            **extra,
        },
    )


def test_runner_validation_errors_name_openai():
    chunk = Chunk("a", {"v": 1})
    bad = client(lambda r: openai_answer([("NORMAL", 0.7), ("ANOMALY", 0.7)]))
    with bad, pytest.raises(JevError, match="^OpenAI Decisions returned an invalid"):
        list(scan([chunk], Config(model="gpt-6-luna"), cache=None, client=bad))


def test_missing_usage_is_accepted_like_jev():
    chunk = Chunk("a", {"v": 1})
    ok = client(lambda r: openai_answer([("NORMAL", 0.9), ("ANOMALY", 0.1)]))
    with ok:
        [result] = scan([chunk], Config(model="gpt-6-luna"), cache=None, client=ok)
    assert result.label == "NORMAL" and result.usage == {}
