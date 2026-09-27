import json
import sqlite3
from dataclasses import replace

import pytest

from jevotron import Chunk, Config, preview, scan
from jevotron.batching import BatchOptions, estimate_request, pack, prepare
from jevotron.client import ContextLimitError, JevError
from jevotron.runner import ScanStats


@pytest.fixture
def long_guide():
    return Config(guidance="Check that the definition matches the name. " * 100)


def entries(n=5, fields=2):
    return [
        Chunk(
            str(i), {f"f{j}": "BAD" if j == 0 else f"value {i}" for j in range(fields)}
        )
        for i in range(n)
    ]


def test_auto_shares_only_guidance_and_preserves_each_entry(fake, long_guide):
    chunks = entries()
    shown = list(preview(chunks, long_guide, batch_size="auto"))
    stats = ScanStats()
    results = list(
        scan(
            chunks, long_guide, cache=None, client=fake, stats=stats, batch_size="auto"
        )
    )
    assert len(fake.requests) == len(shown) == 1
    assert shown[0]["request"] == fake.requests[0]
    assert shown[0]["estimated_tokens_saved"] > 0
    assert shown[0]["estimated_context_tokens"] < shown[0]["context_budget"]
    assert fake.requests[0]["state"] == {
        "guidance": long_guide.guidance,
        "exemplars": [],
    }
    assert len(fake.requests[0]["questions"]) == 10
    for key, question in fake.requests[0]["questions"].items():
        owner = shown[0]["question_entries"][key]
        assert question["instructions"]["entry"] == chunks[int(owner["id"])].data
        assert question["instructions"]["field_path"] == owner["field"]
    assert [r.id for r in results] == [c.id for c in chunks]
    assert all(r.label == "ANOMALY" and len(r.fields) == 2 for r in results)
    assert [r.request_hash for r in results] == [
        e["assessment_hash"] for e in shown[0]["entries"]
    ]
    assert stats.api_calls == 1 and stats.input_tokens == 50
    assert stats.estimated_tokens == shown[0]["estimated_tokens"]
    assert all(
        r.usage == {"batch_request_hashes": [shown[0]["request_hash"]]} for r in results
    )


def test_auto_retains_entry_layout_when_repetition_costs_more(fake):
    chunks = [
        Chunk(str(i), {"a": i, "b": "x" * 1000}, fields=["/a", "/b"]) for i in range(3)
    ]
    results = list(
        scan(chunks, Config(guidance="Check values."), cache=None, client=fake)
    )
    assert len(results) == len(fake.requests) == 3
    assert all("entry" in r["state"] for r in fake.requests)


def test_default_retains_original_layout_pending_domain_validation(fake, long_guide):
    list(scan(entries(2), long_guide, cache=None, client=fake))
    assert len(fake.requests) == 2
    assert all("entry" in r["state"] for r in fake.requests)
    assert all(p["layout"] == "entry-v1" for p in preview(entries(2), long_guide))


@pytest.mark.parametrize("size,calls", [(1, 5), (2, 3), (3, 2), (64, 1)])
def test_explicit_entry_cap(fake, long_guide, size, calls):
    result = list(scan(entries(), long_guide, batch_size=size, cache=None, client=fake))
    assert len(result) == 5 and len(fake.requests) == calls
    assert all(len(r["questions"]) <= size * 2 for r in fake.requests)


def test_token_budget_splits_one_entry_and_reassembles(fake, cache_path):
    chunk = entries(1, fields=12)[0]
    options = BatchOptions(2, 2000)
    plan = prepare(chunk, Config(), options)
    batches = list(pack([plan], options))
    assert len(batches) > 1
    for batch in batches:
        total, context = estimate_request(batch.request)
        assert total <= options.tokens and context <= options.context_tokens
    stats = ScanStats()
    result = list(
        scan(
            [chunk],
            batch_size=2,
            batch_tokens=2000,
            client=fake,
            cache=cache_path,
            stats=stats,
        )
    )[0]
    assert len(result.fields) == 12
    assert len(result.usage["batch_request_hashes"]) == len(batches)
    assert stats.input_tokens == len(batches) * 50
    replay = list(scan([chunk], batch_size=8, cache=cache_path))[0]
    assert replay.cached and replay.request_hash == result.request_hash


def test_entry_mode_can_split_many_fields(fake):
    results = list(
        scan(
            entries(1, fields=12),
            batch_size=1,
            batch_tokens=2000,
            cache=None,
            client=fake,
        )
    )
    assert len(results) == 1 and len(fake.requests) > 1
    assert len(results[0].fields) == 12
    assert all("entry" in r["state"] for r in fake.requests)


def test_budget_boundary_exact_fit_and_one_token_short():
    chunks = entries(2, fields=2)
    config = Config()
    plans = [prepare(c, config, BatchOptions(2)) for c in chunks]
    large = list(pack(plans, BatchOptions(2)))[0]
    exact, _ = estimate_request(large.request)
    assert len(list(pack(plans, BatchOptions(2, exact)))) == 1
    assert len(list(pack(plans, BatchOptions(2, exact - 1)))) == 2


@pytest.mark.parametrize("size", ["auto", 1, 8])
def test_oversized_individual_context_fails_before_network(fake, size):
    chunks = [Chunk("large", {"value": "x" * 5000})]
    with pytest.raises(ValueError, match="Entry 'large'.*largest question"):
        list(scan(chunks, batch_size=size, batch_tokens=2000, cache=None, client=fake))
    assert not fake.requests


def test_unicode_is_budgeted_as_utf8():
    ascii_plan = prepare(Chunk("x", {"v": "a" * 100}), Config(), BatchOptions(2))
    unicode_plan = prepare(Chunk("x", {"v": "界" * 100}), Config(), BatchOptions(2))
    assert (
        estimate_request(unicode_plan.request)[0]
        > estimate_request(ascii_plan.request)[0]
    )


def test_cache_skips_hits_before_packing_and_preserves_order(
    fake, long_guide, cache_path
):
    chunks = entries(6)
    first = list(
        scan(chunks[::2], long_guide, cache=cache_path, client=fake, batch_size=2)
    )
    stats = ScanStats()
    all_results = list(
        scan(
            chunks,
            long_guide,
            cache=cache_path,
            client=fake,
            stats=stats,
            batch_size="auto",
        )
    )
    assert [r.id for r in all_results] == [c.id for c in chunks]
    assert [r.cached for r in all_results] == [True, False] * 3
    assert stats.api_calls == 1 and stats.input_tokens == 50
    assert len(fake.requests[-1]["questions"]) == 6
    assert [r.request_hash for r in all_results[::2]] == [r.request_hash for r in first]
    moved = [replace(c, id="moved_" + c.id, source="new") for c in reversed(chunks)]
    replay_stats = ScanStats()
    replay = list(
        scan(moved, long_guide, cache=cache_path, batch_size=3, stats=replay_stats)
    )
    assert all(r.cached for r in replay) and replay_stats.api_calls == 0
    assert [r.source for r in replay] == ["new"] * 6
    with sqlite3.connect(cache_path) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 6
        requests = db.execute(
            "SELECT request_json, response_json FROM batch_requests"
        ).fetchall()
    assert len(requests) == 3
    assert (
        sum(json.loads(response)["usage"]["input_tokens"] for _, response in requests)
        == 150
    )


def test_legacy_and_shared_cache_namespaces_are_separate(fake, long_guide, cache_path):
    chunk = entries(1)[0]
    old = list(scan([chunk], long_guide, cache=cache_path, client=fake, batch_size=1))[
        0
    ]
    new = list(scan([chunk], long_guide, cache=cache_path, client=fake, batch_size=2))[
        0
    ]
    assert not new.cached and new.request_hash != old.request_hash
    assert list(scan([chunk], long_guide, cache=cache_path, batch_size=1))[0].cached
    assert list(scan([chunk], long_guide, cache=cache_path, batch_size="auto"))[
        0
    ].cached


def test_refresh_replaces_shared_assessments(fake, long_guide, cache_path):
    chunks = entries(2)
    list(scan(chunks, long_guide, cache=cache_path, client=fake, batch_size="auto"))
    refreshed = list(
        scan(
            chunks,
            long_guide,
            cache=cache_path,
            client=fake,
            refresh=True,
            batch_size="auto",
        )
    )
    assert len(fake.requests) == 2 and all(not r.cached for r in refreshed)
    assert (
        list(scan(chunks, long_guide, cache=cache_path, batch_size="auto"))[
            0
        ].assessed_at
        == refreshed[0].assessed_at
    )


@pytest.mark.parametrize(
    "change",
    [
        {"guidance": "Different guidance"},
        {"exemplars": [{"v": "different"}]},
        {"model": "different-model"},
        {"criteria": {"NORMAL": "good", "ANOMALY": "bad"}},
    ],
)
def test_shared_semantic_inputs_invalidate_cache(fake, cache_path, change):
    chunks = entries(2)
    list(scan(chunks, batch_size=2, cache=cache_path, client=fake))
    results = list(
        scan(chunks, Config(**change), batch_size=2, cache=cache_path, client=fake)
    )
    assert len(fake.requests) == 2 and all(not r.cached for r in results)


def test_shared_duplicate_content_has_one_assessment_and_ordered_results(
    fake, cache_path
):
    first = entries(1)[0]
    chunks = [first, replace(first, id="copy", source="other-file")]
    results = list(scan(chunks, batch_size=2, cache=cache_path, client=fake))
    assert [r.id for r in results] == ["0", "copy"]
    assert results[1].source == "other-file"
    assert results[0].request_hash == results[1].request_hash
    assert len(fake.requests[0]["questions"]) == 2
    assert all(r.cached for r in scan(chunks, batch_size=2, cache=cache_path))


def test_one_entry_model_must_stay_consistent_across_split_requests(fake, cache_path):
    class ChangesModel:
        def evaluate(self, request):
            response = fake.evaluate(request)
            response["model"] = f"version-{len(fake.requests)}"
            return response

    with pytest.raises(JevError, match="model changed"):
        list(
            scan(
                entries(1, 12),
                batch_size=2,
                batch_tokens=2000,
                cache=cache_path,
                client=ChangesModel(),
            )
        )
    with sqlite3.connect(cache_path) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0


def test_context_rejection_splits_and_keeps_exact_question_context(fake, long_guide):
    attempts = []

    class SmallServer:
        def evaluate(self, request):
            attempts.append(request)
            if len(request["questions"]) > 2:
                raise ContextLimitError("too big")
            return fake.evaluate(request)

    stats = ScanStats()
    results = list(
        scan(
            entries(4),
            long_guide,
            cache=None,
            client=SmallServer(),
            stats=stats,
            batch_size="auto",
        )
    )
    assert len(results) == 4 and len(fake.requests) == 4
    assert stats.api_calls == 7 and stats.input_tokens == 200
    original = attempts[0]
    actual = {k: v for r in fake.requests for k, v in r["questions"].items()}
    assert actual == original["questions"]
    assert all(r["state"] == original["state"] for r in fake.requests)


def test_unsplittable_server_context_fails_bounded(long_guide, cache_path):
    calls = []

    class Rejects:
        def evaluate(self, request):
            calls.append(request)
            raise ContextLimitError("too big")

    with pytest.raises(ContextLimitError, match="Nothing was truncated"):
        list(
            scan(
                entries(2),
                long_guide,
                cache=cache_path,
                client=Rejects(),
                batch_size="auto",
            )
        )
    assert len(calls) == 3  # four questions -> two -> one
    with sqlite3.connect(cache_path) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0


@pytest.mark.parametrize("malformed", [False, True])
def test_failure_retains_completed_entries_and_resumes(
    fake, long_guide, cache_path, malformed
):
    class FailsSecond:
        def evaluate(self, request):
            if fake.requests:
                if malformed:
                    return {"model": "jev-test", "answers": {}}
                raise JevError("permanent validation failure")
            return fake.evaluate(request)

    chunks = entries(4)
    stream = scan(
        chunks, long_guide, cache=cache_path, client=FailsSecond(), batch_size=2
    )
    assert next(stream).id == "0"
    assert next(stream).id == "1"
    with pytest.raises(JevError):
        next(stream)
    results = list(
        scan(chunks, long_guide, cache=cache_path, client=fake, batch_size="auto")
    )
    assert [r.cached for r in results] == [True, True, False, False]
    assert len(fake.requests) == 2


def test_split_entry_is_not_cached_until_all_fields_succeed(fake, cache_path):
    class FailsSecond:
        def evaluate(self, request):
            if fake.requests:
                raise JevError("stop")
            return fake.evaluate(request)

    with pytest.raises(JevError):
        list(
            scan(
                entries(1, 12),
                batch_size=2,
                batch_tokens=2000,
                cache=cache_path,
                client=FailsSecond(),
            )
        )
    with sqlite3.connect(cache_path) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM batch_requests").fetchone()[0] == 1


def test_buffered_entries_survive_later_parser_error(fake, long_guide, cache_path):
    def source():
        yield from entries(2)
        raise ValueError("bad input")

    stream = scan(
        source(), long_guide, cache=cache_path, client=fake, batch_size="auto"
    )
    assert next(stream).id == "0"
    assert next(stream).id == "1"
    with pytest.raises(ValueError, match="bad input"):
        next(stream)
    assert all(
        r.cached
        for r in scan(entries(2), long_guide, cache=cache_path, batch_size="auto")
    )


def test_bounded_lookahead_and_client_cleanup(
    monkeypatch, fake, long_guide, cache_path
):
    consumed = []

    def source():
        for c in entries(100):
            consumed.append(c.id)
            yield c

    monkeypatch.setattr("jevotron.runner.JevClient", lambda: fake)
    stream = scan(source(), long_guide, cache=cache_path, batch_size=3)
    next(stream)
    assert len(consumed) == 3
    stream.close()
    assert fake.closed


@pytest.mark.parametrize("size", [0, -1, "invalid", "1.5", True])
def test_bad_batch_size(size):
    with pytest.raises(ValueError, match="batch-size"):
        BatchOptions(size)


@pytest.mark.parametrize("tokens", [0, -1, 64001, True, 1.5])
def test_bad_token_budget(tokens):
    with pytest.raises(ValueError, match="batch-tokens"):
        BatchOptions(tokens=tokens)
