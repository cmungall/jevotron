"""Example compatibility and synthetic-only mail questions; feeds live in the extension."""

from jevotron.assessment import request_hash
from jevotron.classification import ChoiceResult as Choice
from jevotron.classification import decode_choices
from jevotron.extensions.feeds.common import digest, timestamp
from jevotron.extensions.feeds.preset import MODEL, SCHEMA, question
from jevotron.extensions.feeds.preset import make_preview as feed_preview
from jevotron.extensions.feeds.ranking import rank, render

__all__ = [
    "Choice",
    "MODEL",
    "SCHEMA",
    "decode",
    "digest",
    "make_preview",
    "rank",
    "render",
    "timestamp",
]


def make_preview(record: dict, interests: str) -> dict:
    preview = feed_preview(record, interests)
    questions = preview["request"]["questions"]
    if record["source"] == "synthetic-mail":
        questions["needs_reply"] = question(
            "Does the full ordered thread still need a reply from state.record.self_id? "
            "Track unresolved requests across messages, recipients, and sent replies; "
            "a recent outgoing message alone does not prove resolution.",
            yes="An unresolved request is directed to this reader.",
            no="No unresolved reply obligation; requests were answered or informational.",
            unclear="Ambiguous responsibility, incomplete thread, or uncertain resolution.",
        )
    preview["request_hash"] = request_hash(preview["request"])
    return preview


def decode(response: dict, preview: dict) -> dict[str, Choice]:
    return decode_choices(response, preview["request"])
