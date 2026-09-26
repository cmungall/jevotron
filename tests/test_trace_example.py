"""Check evidence provenance and segmentation at the analyzer/jt boundary."""

import json
from pathlib import Path

from jevotron.config import load_config
from jevotron.models import resolve

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "agent-traces"


def test_segments_preserve_events_and_context_without_label_leakage(tmp_path):
    session = json.loads((EXAMPLE / "session.json").read_text())
    document = {"session": session, "classification": {"findings": ["gold-label"]}}
    path = tmp_path / "normalized.json"
    path.write_text(json.dumps(document))
    config = load_config(EXAMPLE / "jev_config.py")
    chunks = list(config.parser(path))
    assert [c.data["event_range"] for c in chunks] == [
        {"start": 0, "stop_exclusive": 7},
        {"start": 7, "stop_exclusive": 13},
        {"start": 13, "stop_exclusive": 15},
    ]
    assert len({c.id for c in chunks}) == 3
    actual = []
    for chunk in chunks:
        assert chunk.field_paths() == ["/segment"]
        assert "gold-label" not in json.dumps(chunk.data)
        for item in chunk.data["segment"]:
            assert resolve(document, item["source_pointer"]) == item["event"]
            actual.append(item["event"])
        for item in chunk.data["prior_user_messages"]:
            assert resolve(document, item["source_pointer"]) == item["event"]
    assert actual == session["events"]
    assert [
        item["event"]["event_id"] for item in chunks[2].data["prior_user_messages"]
    ] == ["e0", "e7"]


def test_whole_trace_keeps_all_events_and_unwrapped_pointers():
    config = load_config(EXAMPLE / "whole_trace_config.py")
    path = EXAMPLE / "session.json"
    chunks = list(config.parser(path))
    assert len(chunks) == 1
    assert config.anomaly_label == "OBSERVED"
    assert chunks[0].data["scope"] == "whole_trace"
    document = json.loads(path.read_text())
    assert len(chunks[0].data["segment"]) == len(document["events"])
    for item in chunks[0].data["segment"]:
        assert resolve(document, item["source_pointer"]) == item["event"]


def test_events_before_first_prompt_and_empty_trace(tmp_path):
    path = tmp_path / "trace.json"
    config = load_config(EXAMPLE / "jev_config.py")
    path.write_text(json.dumps({"events": []}))
    assert list(config.parser(path)) == []
    events = [
        {"kind": "lifecycle", "event_id": "start"},
        {"kind": "message", "role": "user", "event_id": "request"},
    ]
    path.write_text(json.dumps({"events": events}))
    chunks = list(config.parser(path))
    assert len(chunks) == 2
    assert [c.data["segment"][0]["event"] for c in chunks] == events
