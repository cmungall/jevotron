import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from jevotron import Chunk, Config, compare
from jevotron.client import JevError


def test_added_removed_changed_and_persistent_fields(fake):
    baseline = [
        Chunk("same", {"a": "ok"}),
        Chunk("changed", {"a": "ok", "b": "BAD", "c": "BAD"}),
        Chunk("removed", {"a": "BAD"}),
    ]
    candidate = [
        Chunk("added", {"a": "BAD"}),
        Chunk("changed", {"a": "BAD", "b": "BAD", "d": "ok"}),
        Chunk("same", {"a": "ok"}),
    ]
    result = compare(baseline, candidate, cache=None, client=fake)
    assert result.metadata["summary"] == {
        "entries": {"added": 1, "removed": 1, "changed": 1, "unchanged": 1},
        "field_warnings": {"new": 2, "resolved": 2, "persistent": 1},
        "unique_requests": 4,
        "cached_requests": 0,
    }
    assert [entry["id"] for entry in result.entries] == [
        "added",
        "changed",
        "removed",
        "same",
    ]
    fields = {field["path"]: field for field in result.entries[1]["fields"]}
    assert fields["/a"]["warning"] == "new"
    assert fields["/a"]["before"]["value"] == "ok"
    assert fields["/a"]["after"]["value"] == "BAD"
    assert fields["/a"]["score_delta"] == pytest.approx(0.8)
    assert fields["/b"]["warning"] == "persistent"
    assert fields["/c"]["after"] is None and fields["/c"]["warning"] == "resolved"
    assert fields["/d"]["before"] is None and fields["/d"]["warning"] == "none"
    assert result.violates("new-warning") and result.violates("any-warning")
    assert not result.violates("none")
    assert not fake.closed


def test_persistent_warnings_do_not_fail_new_only_policy(fake):
    chunks = [Chunk("x", {"value": "BAD"})]
    result = compare(chunks, chunks, cache=None, client=fake)
    assert not result.violates("new-warning")
    assert result.violates("any-warning")
    assert len(fake.requests) == 1
    with pytest.raises(ValueError, match="Unknown comparison policy"):
        result.violates("unknown")


def test_reused_parser_buffers_are_snapshotted_when_yielded(fake):
    def parse(values):
        row = {}
        fields = ["/value"]
        for ident, value in zip(("a", "b"), values, strict=True):
            row["value"] = value
            yield Chunk(ident, row, fields)
        # Mutation after the last yield must not change buffered selections.
        fields[:] = ["/missing"]

    result = compare(
        parse(["ok", "BAD"]), parse(["BAD", "BAD"]), cache=None, client=fake
    )
    assert result.metadata["summary"]["entries"] == {
        "added": 0,
        "removed": 0,
        "changed": 1,
        "unchanged": 1,
    }
    assert result.metadata["summary"]["field_warnings"] == {
        "new": 1,
        "resolved": 0,
        "persistent": 1,
    }
    assert result.entries[0]["fields"][0]["before"]["value"] == "ok"
    assert result.metadata["summary"]["unique_requests"] == 2


def test_preflight_checks_both_complete_inputs_before_inference(fake):
    good = [Chunk("x", {"a": "ok"})]
    with pytest.raises(ValueError, match="Duplicate stable ID in candidate"):
        compare(good, [good[0], good[0]], cache=None, client=fake)
    with pytest.raises(ValueError, match="nonempty string"):
        compare(good, [Chunk("", {"a": "ok"})], cache=None, client=fake)
    with pytest.raises(ValueError, match="Field does not exist"):
        compare(good, [Chunk("x", {"a": "ok"}, ["/missing"])], cache=None, client=fake)
    assert fake.requests == []


def test_empty_sides_and_removed_warnings_are_explicit(fake):
    empty = compare([], [], cache=None, client=fake)
    assert empty.entries == [] and not empty.violates("new-warning")
    assert json.loads(empty.jsonl())["complete"]
    removed = compare([Chunk("old", {"v": "BAD"})], [], cache=None, client=fake)
    assert removed.entries[0]["change"] == "removed"
    assert removed.entries[0]["candidate"] is None
    assert removed.metadata["summary"]["field_warnings"]["resolved"] == 1
    assert not removed.violates("any-warning")


@pytest.mark.parametrize(
    "before,after", [(["/a"], ["/b"]), (["/a", "/b"], ["/b"]), (["/b"], ["/a", "/b"])]
)
def test_changed_assessment_coverage_cannot_hide_existing_fields(fake, before, after):
    data = {"a": "BAD", "b": None}
    with pytest.raises(ValueError, match="Incompatible assessment coverage"):
        compare(
            [Chunk("x", data, before)],
            [Chunk("x", data, after)],
            cache=None,
            client=fake,
        )
    assert fake.requests == []


def test_cache_replay_and_threshold_reaggregation(fake, tmp_path):
    baseline = [Chunk("x", {"v": "ok"}, source="old.csv:1")]
    candidate = [Chunk("x", {"v": "BAD"}, source="new.csv:9")]
    cache = tmp_path / "cache.db"
    first = compare(baseline, candidate, cache=cache, client=fake)
    replay = compare(baseline, candidate, Config(threshold=0.95), cache=cache)
    assert len(fake.requests) == 2
    assert first.violates("new-warning") and not replay.violates("new-warning")
    assert first.metadata["inference"] == replay.metadata["inference"]
    assert replay.metadata["threshold"] == 0.95
    assert replay.metadata["summary"]["cached_requests"] == 2
    entry = replay.entries[0]
    assert entry["baseline"]["source"] == "old.csv:1"
    assert entry["candidate"]["source"] == "new.csv:9"
    assert (
        entry["baseline"]["assessed_at"] == first.entries[0]["baseline"]["assessed_at"]
    )
    assert entry["baseline"]["id"] == "x"
    assert entry["candidate"]["request_hash"] != entry["baseline"]["request_hash"]
    refreshed = compare(baseline, baseline, cache=cache, refresh=True, client=fake)
    assert len(fake.requests) == 3
    assert refreshed.metadata["summary"]["unique_requests"] == 1


def test_shared_inference_settings_change_fingerprint_and_reassess(fake, tmp_path):
    chunks = [Chunk("x", {"v": "ok"})]
    first = compare(chunks, chunks, cache=tmp_path / "cache.db", client=fake)
    settings = Config(guidance="Different rules", model="pinned-model")
    second = compare(chunks, chunks, settings, cache=tmp_path / "cache.db", client=fake)
    assert (
        first.metadata["inference"]["fingerprint"]
        != second.metadata["inference"]["fingerprint"]
    )
    assert second.metadata["inference"]["settings"]["guidance"] == "Different rules"
    assert second.metadata["inference"]["settings"]["model"] == "pinned-model"
    assert len(fake.requests) == 2
    assert second.metadata["summary"]["entries"]["unchanged"] == 1


def test_mixed_resolved_models_are_rejected(fake):
    class ChangingModel:
        def evaluate(self, request):
            response = fake.evaluate(request)
            response["model"] = f"model-{len(fake.requests)}"
            return response

    with pytest.raises(ValueError, match="requires one resolved model"):
        compare(
            [Chunk("x", {"v": "ok"})],
            [Chunk("x", {"v": "BAD"})],
            cache=None,
            client=ChangingModel(),
        )


def test_api_failure_preserves_prior_cache_success(fake, tmp_path):
    class Failing:
        def evaluate(self, request):
            if fake.requests:
                raise JevError("interrupted")
            return fake.evaluate(request)

    before = [Chunk("x", {"v": "ok"})]
    after = [Chunk("x", {"v": "BAD"})]
    cache = tmp_path / "cache.db"
    with pytest.raises(JevError):
        compare(before, after, cache=cache, client=Failing())
    result = compare(before, after, cache=cache, client=fake)
    assert result.metadata["summary"]["cached_requests"] == 1
    assert len(fake.requests) == 2


def test_markdown_retains_values_scores_provenance_and_escapes_input(fake):
    old = Chunk("entry|<script>[link]", {"v": "ok"}, source="old.csv:1")
    new = replace(old, data={"v": "BAD"}, source="new.csv:2")
    result = compare([old], [new], cache=None, client=fake)
    text = result.markdown("new-warning")
    assert "Policy violation" in text and "0.1 → 0.9" in text
    assert "old.csv:1" in text and "new.csv:2" in text
    assert result.entries[0]["baseline"]["assessed_at"] in text
    assert '"ok"' in text and '"BAD"' in text
    assert "<script>" not in text and "&#124;" in text and "&#91;link&#93;" in text


def test_optional_reference_settings_fingerprint_excludes_source_paths():
    from jevotron.comparison import _inference

    @dataclass
    class Reference:
        name: str
        path: Path
        source_key: str

    settings = Config()
    settings.references = [Reference("lookup", Path("before.json"), "/country")]
    before = _inference(settings)
    settings.references[0].path = Path("moved.json")
    assert _inference(settings) == before
    settings.references[0].source_key = "/region"
    assert _inference(settings)["fingerprint"] != before["fingerprint"]
