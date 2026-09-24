import copy
import sqlite3
from dataclasses import replace

import pytest

from jevotron import Chunk, Config, preview, scan
from jevotron.client import JevError
from jevotron.runner import make_request, validate_response


def test_independent_entries_batched_fields_and_max_score(fake):
    chunks = [Chunk("one", {"a": "ok", "b": "BAD"}), Chunk("two", {"c": "ok"})]
    config = Config(
        guidance="Read carefully", exemplars=[{"data": {"a": "ok"}, "label": "NORMAL"}]
    )
    results = list(scan(chunks, config, cache=None, client=fake))
    assert len(fake.requests) == 2
    assert len(fake.requests[0]["questions"]) == 2
    assert fake.requests[0]["state"]["entry"] == chunks[0].data
    assert fake.requests[1]["state"]["entry"] == chunks[1].data
    assert fake.requests[0]["state"]["exemplars"] == config.exemplars
    assert [r.score for r in results] == [0.9, 0.1]
    assert [r.label for r in results] == ["ANOMALY", "NORMAL"]
    assert results[0].fields[1].path == "/b"
    assert not fake.closed  # injected clients are owned by the caller


def test_reordered_rows_keys_and_source_reuse_cache(fake, cache_path):
    original = [Chunk("old", {"b": "BAD", "a": "ok"}, source="old.csv:1")]
    first = list(scan(original, cache=cache_path, client=fake))[0]
    moved = [Chunk("new", {"a": "ok", "b": "BAD"}, source="new.csv:42")]
    second = list(scan(moved, cache=cache_path))[0]  # no API key
    assert second.cached and len(fake.requests) == 1
    assert second.id == "new" and second.source == "new.csv:42"
    assert first.request_hash == second.request_hash
    assert first.assessed_at == second.assessed_at
    assert second.model == "jev-test"


@pytest.mark.parametrize(
    "change",
    [
        {"guidance": "new guidance"},
        {"exemplars": [{"example": "new"}]},
        {"model": "new-model"},
        {"criteria": {"NORMAL": "good", "ANOMALY": "bad"}},
    ],
)
def test_assessment_config_changes_invalidate_cache(fake, cache_path, change):
    chunk = Chunk("id", {"a": "ok"})
    list(scan([chunk], cache=cache_path, client=fake))
    result = list(scan([chunk], Config(**change), cache=cache_path, client=fake))[0]
    assert not result.cached and len(fake.requests) == 2


def test_context_and_selected_fields_invalidate_cache(fake, cache_path):
    original = Chunk("x", {"a": "ok", "context": "first"}, fields=["/a"])
    list(scan([original], cache=cache_path, client=fake))
    changed = replace(original, data={"a": "ok", "context": "second"})
    list(scan([changed], cache=cache_path, client=fake))
    list(scan([replace(changed, fields=["/context"])], cache=cache_path, client=fake))
    assert len(fake.requests) == 3


def test_threshold_change_reaggregates_without_inference(fake, cache_path):
    chunk = Chunk("x", {"value": "BAD"})
    first = list(scan([chunk], cache=cache_path, client=fake))[0]
    second = list(scan([chunk], Config(threshold=0.95), cache=cache_path))[0]
    assert first.warning and not second.warning and second.cached
    assert second.score == 0.9


def test_changed_version_only_assesses_changed_chunks(fake, cache_path):
    chunks = [Chunk("a", {"value": 1}), Chunk("b", {"value": 2})]
    list(scan(chunks, cache=cache_path, client=fake))
    chunks = [Chunk("b", {"value": 3}), Chunk("a", {"value": 1})]
    results = list(scan(chunks, cache=cache_path, client=fake))
    assert len(fake.requests) == 3
    assert [r.cached for r in results] == [False, True]


def test_refresh_replaces_existing_assessment(fake, cache_path):
    chunks = [Chunk("x", {"a": "ok"})]
    first = list(scan(chunks, cache=cache_path, client=fake))[0]
    second = list(scan(chunks, cache=cache_path, client=fake, refresh=True))[0]
    replay = list(scan(chunks, cache=cache_path))[0]
    assert len(fake.requests) == 2 and not second.cached
    assert replay.assessed_at == second.assessed_at
    assert first.request_hash == second.request_hash


def test_partial_failure_retains_success_and_resumes(fake, cache_path):
    class FailsSecond:
        def evaluate(self, request):
            if len(fake.requests):
                raise JevError("stopped")
            return fake.evaluate(request)

    chunks = [Chunk("a", {"a": "ok"}), Chunk("b", {"a": "BAD"})]
    with pytest.raises(JevError):
        list(scan(chunks, cache=cache_path, client=FailsSecond()))
    results = list(scan(chunks, cache=cache_path, client=fake))
    assert [r.cached for r in results] == [True, False]
    assert len(fake.requests) == 2


def test_invalid_response_not_cached(fake, cache_path):
    class Broken:
        def evaluate(self, request):
            return {"model": "jev-test", "answers": {}}

    chunk = Chunk("x", {"a": "ok"})
    with pytest.raises(JevError):
        list(scan([chunk], cache=cache_path, client=Broken()))
    with sqlite3.connect(cache_path) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
    assert not list(scan([chunk], cache=cache_path, client=fake))[0].cached


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["answers"].clear(),
        lambda r: r["answers"]["field_0"].update(choice="unknown"),
        lambda r: r["answers"]["field_0"].update(choice="ANOMALY"),
        lambda r: r["answers"]["field_0"].update(confidence=float("nan")),
        lambda r: r["answers"]["field_0"].update(confidence=True),
        lambda r: r["answers"]["field_0"]["probabilities"].update(NORMAL=0.3),
        lambda r: r["answers"]["field_0"]["probabilities"].update(NORMAL=-1),
        lambda r: r["answers"]["field_0"].update(type="noul"),
    ],
)
def test_response_contract(fake, mutation):
    request, _ = make_request(Chunk("x", {"a": "ok"}), Config())
    response = copy.deepcopy(fake.evaluate(request))
    mutation(response)
    with pytest.raises(JevError):
        validate_response(response, request)


def test_custom_labels_and_criteria(fake):
    config = Config(
        labels=["PASS", "REVIEW"],
        anomaly_label="REVIEW",
        criteria={"PASS": "correct", "REVIEW": "incorrect"},
    )
    result = list(scan([Chunk("x", {"a": "BAD"})], config, cache=None, client=fake))[0]
    assert result.label == "REVIEW" and result.score == 0.9


def test_duplicate_ids_and_bad_paths_stop(fake):
    with pytest.raises(ValueError, match="Duplicate chunk id"):
        list(
            scan([Chunk("x", {"a": 1}), Chunk("x", {"a": 2})], cache=None, client=fake)
        )
    with pytest.raises(ValueError, match="Field does not exist"):
        list(scan([Chunk("y", {"a": 1}, fields=["/b"])], cache=None, client=fake))
    assert len(fake.requests) == 1


def test_preview_is_exact_request(fake):
    chunks = [Chunk("x", {"a": "ok"})]
    shown = list(preview(chunks))[0]
    result = list(scan(chunks, cache=None, client=fake))[0]
    assert shown["request"] == fake.requests[0]
    assert shown["request_hash"] == result.request_hash


def test_owned_client_closed_on_early_stop(monkeypatch, fake, cache_path):
    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    results = scan([Chunk("x", {"a": 1}), Chunk("y", {"a": 2})], cache=cache_path)
    next(results)
    results.close()
    assert fake.closed
