"""Synthetic thread adapter only. No gog invocation, credentials, or Gmail client."""

from .pipeline import digest, timestamp


def normalize_thread(thread: dict) -> dict:
    if thread.get("synthetic") is not True:
        raise ValueError("Only explicitly synthetic mail fixtures are accepted")
    messages = sorted(
        thread["messages"], key=lambda m: (timestamp(m["sent_at"]), m["id"])
    )
    if not messages or len({m["id"] for m in messages}) != len(messages):
        raise ValueError("Thread requires unique messages")
    data = {
        "self_id": thread["self_id"],
        "messages": messages,
        "complete": thread["complete"],
        "subject": thread["subject"],
    }
    return {
        "id": "synthetic-mail:" + thread["id"],
        "source": "synthetic-mail",
        "version": digest(data),
        "status": "active",
        "author": messages[-1]["sender"],
        "created_at": messages[-1]["sent_at"],
        "text": thread["subject"],
        **data,
    }


def proposed_labels(record: dict, choices: dict) -> dict:
    reply = choices.get("needs_reply")
    # Conservative shadow policy. Even a confident 'no' cannot clear a label.
    needs_review = not record["complete"] or reply is None or reply.uncertain
    labels = []
    if reply and reply.label == "yes":
        labels.append("JT/Needs-reply")
    if needs_review or (reply and reply.label == "unclear"):
        labels.append("JT/Review")
    return {
        "thread_id": record["id"],
        "thread_version": record["version"],
        "mode": "shadow",
        "proposed_add": labels,
        "proposed_remove": [],
        "action_authorized": False,
        "basis": "decoded" if reply else "unassessed",
    }
