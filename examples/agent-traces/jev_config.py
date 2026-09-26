"""Assess normalized agent traces without depending on the trace analyzer."""

import json
from pathlib import Path

from jevotron import Chunk, Config


def trace_chunks(path, *, whole_trace=False):
    """Read TraceSession.to_dict() or the analyzer's `analyze --format json`."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Expected a normalized trace object")
    wrapped = "session" in document
    session = document["session"] if wrapped else document
    if not isinstance(session, dict) or not isinstance(session.get("events"), list):
        raise ValueError("Expected a normalized trace with an events array")
    events = session["events"]
    if any(not isinstance(event, dict) for event in events):
        raise ValueError("Every normalized event must be an object")
    prefix = "/session/events" if wrapped else "/events"
    trace_id = session.get("session_id") or path.stem

    def referenced(index):
        return {
            "source_pointer": f"{prefix}/{index}",
            "event": events[index],
        }

    prompts = [
        i
        for i, event in enumerate(events)
        if event.get("kind") == "message" and event.get("role") == "user"
    ]
    starts = [0] if whole_trace else sorted({0, *prompts})
    for start, stop in zip(starts, [*starts[1:], len(events)]):
        if start == stop:
            continue
        yield Chunk(
            id=f"{trace_id}:{start}-{stop}",
            data={
                "trace_id": trace_id,
                "scope": "whole_trace" if whole_trace else "user_turn_segment",
                "event_range": {"start": start, "stop_exclusive": stop},
                "prior_user_messages": [referenced(i) for i in prompts if i < start],
                "segment": [referenced(i) for i in range(start, stop)],
                "context_limit": (
                    "Only the selected events and earlier user messages are supplied. "
                    "Earlier assistant/tool activity and external state may be absent. "
                    "The file ending does not prove task completion."
                ),
            },
            fields=["/segment"],
            source=f"{path}#{prefix}/{start}",
        )


def parse_segments(path):
    yield from trace_chunks(path)


def parse_trace(path):
    yield from trace_chunks(path, whole_trace=True)


def make_config(parser):
    return Config(
        parser=parser,
        guidance=Path(__file__).with_name("guidance.md").read_text(encoding="utf-8"),
        labels=["NOT_OBSERVED", "OBSERVED", "INSUFFICIENT_CONTEXT"],
        criteria={
            "NOT_OBSERVED": "The supplied segment does not exhibit the defined pattern.",
            "OBSERVED": "The supplied segment contains evidence of the defined pattern.",
            "INSUFFICIENT_CONTEXT": "Missing context prevents judging the defined pattern.",
        },
        anomaly_label="OBSERVED",
    )


config = make_config(parse_segments)
