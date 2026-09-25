"""Bluesky validation and bounded source checkpoints; loaded only when selected."""

import json
from datetime import datetime, timezone
from pathlib import Path

from jevotron.models import json_text

from ..bluesky import PublicClient, post_uri, reconcile
from ..common import digest
from ..preset import INTERESTS
from ..storage import MAX_RECORDS, atomic, locked, write_snapshot


def validate_record(record: dict):
    post_uri(record["id"])


def collect_source(
    *,
    root: Path,
    actor=None,
    query=None,
    limit=10,
    pages=1,
    resume=False,
    recheck=False,
    interests=INTERESTS,
):
    if bool(actor) == bool(query):
        raise ValueError("Select exactly one actor or query")
    identity = {"actor": actor, "query": query}
    folder = root / "public" / digest(identity)[:16]
    now = datetime.now(timezone.utc).isoformat()
    with locked(folder):
        state_path = folder / "state.json"
        previous = (
            json.loads(state_path.read_text())
            if state_path.exists()
            else {
                "identity": identity,
                "records": [],
                "cursor": None,
            }
        )
        if previous["identity"] != identity:
            raise ValueError("Checkpoint source mismatch")
        client = PublicClient()
        try:
            if recheck:
                candidates = set()
                for record in previous["records"]:
                    if record["status"] == "active":
                        candidates.add(record["id"])
                        for context in record.get("context", {}).values():
                            if "/app.bsky.feed.post/" in context["id"]:
                                candidates.add(context["id"])
                after = previous.get("recheck_after", "")
                ordered = sorted(candidates)
                uris = (
                    [u for u in ordered if u > after]
                    + [u for u in ordered if u <= after]
                )[:25]
                records = reconcile(previous["records"], client.posts(uris), now)
                cursor = previous["cursor"]
            else:
                if resume and not previous["cursor"]:
                    raise ValueError("No saved cursor; start a fresh bounded poll")
                fresh, cursor = client.collect(
                    actor=actor,
                    query=query,
                    limit=limit,
                    pages=pages,
                    cursor=previous["cursor"] if resume else None,
                )
                merged = {r["id"]: r for r in previous["records"]}
                merged.update({r["id"]: r for r in fresh})
                records = sorted(
                    merged.values(),
                    key=lambda r: (r["observed_at"], r["id"]),
                    reverse=True,
                )[:MAX_RECORDS]
            state = {
                "identity": identity,
                "records": records,
                "cursor": cursor,
                "recheck_after": (uris[-1] if uris else "")
                if recheck
                else previous.get("recheck_after", ""),
            }
            manifest = write_snapshot(
                folder,
                records,
                interests,
                now,
                {
                    "provenance": "real_public_bluesky",
                    "source": identity,
                    "operation": "recheck" if recheck else "collect",
                    "http_requests": client.requests,
                    "cursor": cursor,
                    "coverage": "bounded_sample_not_exhaustive",
                },
            )
            atomic(state_path, json_text(state) + "\n")
            return {"folder": str(folder), **manifest}
        finally:
            client.close()
