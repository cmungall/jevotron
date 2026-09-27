"""Experimental ranking and escaped static rendering; never suppress active records."""

import html
from dataclasses import asdict

from .common import digest, timestamp


def rank(records: list[dict], now: str, assessments: dict | None = None) -> list[dict]:
    """Retain every active item; greedily penalize repeated authors and exact text."""
    clock = timestamp(now)
    pending = []
    for record in records:
        if record.get("status") != "active":
            continue
        choices = (assessments or {}).get(record["id"], {})
        age = max(0, (clock - timestamp(record["created_at"])).total_seconds() / 3600)
        recency = 2 ** (-age / 48)
        relevance = choices.get("relevance")
        action = choices.get("actionability")
        # Unclassified records get no invented semantic score or label.
        semantic = (
            0.6
            * (
                relevance.probabilities["high"]
                + 0.5 * relevance.probabilities["medium"]
            )
            if relevance
            else 0
        )
        semantic += 0.15 * action.probabilities["act"] if action else 0
        pending.append(
            {
                "record": record,
                "base_score": semantic + 0.25 * recency,
                "classification_status": "decoded" if choices else "unassessed",
                "dimensions": {k: asdict(v) for k, v in choices.items()},
                "review": not choices or any(v.uncertain for v in choices.values()),
            }
        )
    authors: dict[str, int] = {}
    texts: dict[str, int] = {}
    ranked = []

    def score(item):
        record = item["record"]
        text_key = digest(record["text"].strip().casefold())
        penalty = 0.08 * authors.get(record["author"], 0)
        penalty += 0.12 * texts.get(text_key, 0) if record["text"].strip() else 0
        return item["base_score"] - penalty

    while pending:
        pending.sort(key=lambda item: (-score(item), item["record"]["id"]))
        item = pending.pop(0)
        item["rank_score"] = round(score(item), 6)
        record = item["record"]
        authors[record["author"]] = authors.get(record["author"], 0) + 1
        key = digest(record["text"].strip().casefold())
        texts[key] = texts.get(key, 0) + 1
        ranked.append(item)
    return ranked


def render(queue: list[dict]) -> str:
    assessed = sum(item["classification_status"] == "decoded" for item in queue)
    title = (
        "Local classified feed"
        if assessed
        else "Local feed preview — inference not run"
    )
    description = (
        f"{assessed}/{len(queue)} items classified. Experimental relevance, actionability, recency and diversity ranking."
        if assessed
        else "Ranking uses recency and diversity only in this demo."
    )
    rows = []
    for item in queue:
        record = item["record"]
        rows.append(
            "<article><h2>"
            + html.escape(record["id"])
            + "</h2><p>"
            + html.escape(f"{item['classification_status']}; rank {item['rank_score']}")
            + "; "
            + html.escape(
                ", ".join(
                    f"{name}: {value['label']}"
                    for name, value in item["dimensions"].items()
                )
            )
            + "</p><pre>"
            + html.escape(record["text"])
            + "</pre></article>"
        )
    return (
        '<!doctype html><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        "content=\"default-src 'none'; style-src 'unsafe-inline'\">"
        "<title>Local feed preview</title><style>body{max-width:60rem;margin:2rem auto;"
        "font-family:system-ui}pre{white-space:pre-wrap}article{border-top:1px solid #aaa}"
        "h2{font-size:1rem;overflow-wrap:anywhere}</style>"
        f"<h1>{title}</h1>"
        f"<p>Untrusted source text. {description}</p>" + "".join(rows)
    )
