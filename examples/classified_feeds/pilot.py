"""Bounded public-data pilot; no automatic retries or email support."""

import argparse
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from jevotron.client import ENDPOINT
from jevotron.models import json_text

from .__main__ import atomic
from .pipeline import decode, digest, make_preview, rank, render, timestamp

# Verified https://docs.typesafe.ai/models on 2026-09-24. Reserve the entire
# documented context window (rounded up) for every attempt, including failures.
MAX_ATTEMPTS = 10
PRICE_PER_MILLION = 0.042
MAX_INPUT_TOKENS = 65_536
RESERVATION_USD = MAX_INPUT_TOKENS * PRICE_PER_MILLION / 1_000_000
BUDGET_USD = 0.05


class ProviderHTTPError(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Provider HTTP {status}; no automatic retry")


class SingleAttemptClient:
    def __init__(self):
        # Use the configured environment credential normally; never inspect files,
        # echo keys, or place credentials in the plan, ledger, or errors.
        self.key = os.environ.get("TYPESAFE_API_KEY")
        if not self.key:
            raise RuntimeError("TYPESAFE_API_KEY is unavailable in this process")
        self.http = httpx.Client(timeout=60, trust_env=False, follow_redirects=False)

    def evaluate(self, request):
        try:
            response = self.http.post(
                ENDPOINT,
                json=request,
                headers={"Authorization": "Bearer " + self.key},
            )
        except httpx.HTTPError:
            raise RuntimeError(
                "Provider transport failure; attempt retained; no retry"
            ) from None
        if response.status_code != 200:
            raise ProviderHTTPError(response.status_code)
        try:
            return response.json()
        except ValueError:
            raise RuntimeError("Provider returned invalid JSON; no retry") from None

    def close(self):
        self.http.close()


def validate_plan(plan):
    records, labels = plan["records"], plan["labels"]
    if not 1 <= len(records) <= MAX_ATTEMPTS:
        raise ValueError("Pilot requires 1–10 public records")
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("Duplicate pilot IDs")
    if set(labels) != {r["id"] for r in records}:
        raise ValueError("Every record must be labeled before inference")
    previews = []
    for record in records:
        if record["source"] != "bluesky-public" or record["status"] != "active":
            raise ValueError(
                "Only active collected public Bluesky records are permitted"
            )
        preview = make_preview(record, plan["interests"])
        expected = labels[record["id"]]["dimensions"]
        questions = preview["request"]["questions"]
        if set(expected) != set(questions) or any(
            expected[name] not in question["criteria"]
            for name, question in questions.items()
        ):
            raise ValueError("Annotations must cover the exact dimension vocabularies")
        previews.append(preview)
    return previews


def authorize_recovery(ledger: dict, request_hash: str):
    """One explicit recovery of a known HTTP 402; retain its cost reservation."""
    matches = [
        i
        for i, a in enumerate(ledger["attempts"])
        if a["request_hash"] == request_hash and a.get("http_status") == 402
    ]
    if len(matches) != 1:
        raise ValueError("Recovery requires exactly one recorded HTTP 402")
    index = matches[0]
    recoveries = ledger.setdefault("recoveries", [])
    if any(r["attempt_index"] == index for r in recoveries):
        return
    if recoveries or ledger["attempts"][index]["status"] != "failed_or_unknown":
        raise ValueError("Only one explicit HTTP 402 recovery is allowed")
    recoveries.append(
        {
            "attempt_index": index,
            "request_hash": request_hash,
            "authorized_at": datetime.now(timezone.utc).isoformat(),
            "basis": "User confirmed billing/access resolved and requested continuation",
        }
    )
    ledger["max_attempts"] = MAX_ATTEMPTS + 1


def run(plan: dict, folder: Path, evaluator, *, recover_request: str | None = None):
    """Single-writer caller; durable reservation precedes each paid request."""
    previews = validate_plan(plan)
    plan_hash = digest(plan)
    ledger_path = folder / "ledger.json"
    ledger = (
        json.loads(ledger_path.read_text())
        if ledger_path.exists()
        else {
            "plan_hash": plan_hash,
            "attempts": [],
            "budget_usd": BUDGET_USD,
            "price_per_million_input_tokens": PRICE_PER_MILLION,
            "reservation_per_attempt_usd": RESERVATION_USD,
            "max_attempts": MAX_ATTEMPTS,
        }
    )
    if ledger["plan_hash"] != plan_hash:
        raise ValueError("Frozen plan changed; refusing a new budget or stale cache")
    if recover_request:
        authorize_recovery(ledger, recover_request)
        atomic(ledger_path, json_text(ledger) + "\n")
    recoveries = ledger.get("recoveries", [])
    if len(recoveries) > 1:
        raise ValueError("Too many recovery authorizations")
    recovered_indices = {r["attempt_index"] for r in recoveries}
    for preview in previews:
        existing = [
            a
            for i, a in enumerate(ledger["attempts"])
            if a["request_hash"] == preview["request_hash"]
            and i not in recovered_indices
        ]
        if existing:
            if len(existing) != 1 or existing[0]["status"] != "complete":
                raise RuntimeError(
                    "Prior incomplete/failed attempt requires review; no retry"
                )
            decode(existing[0]["response"], preview)
            continue
        count = len(ledger["attempts"]) + 1
        if (
            count > MAX_ATTEMPTS + len(recoveries)
            or count * RESERVATION_USD > BUDGET_USD
        ):
            raise RuntimeError("Pilot attempt or conservative cost limit reached")
        attempt = {
            "id": preview["id"],
            "request_hash": preview["request_hash"],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "status": "started",
        }
        ledger["attempts"].append(attempt)
        atomic(ledger_path, json_text(ledger) + "\n")
        started = time.monotonic()
        try:
            response = evaluator.evaluate(preview["request"])
            decode(response, preview)
            usage = response.get("usage", {}).get("input_tokens")
            if type(usage) is not int or not 0 <= usage <= MAX_INPUT_TOKENS:
                raise ValueError(
                    "Missing or unexpected input usage; stop for billing review"
                )
            attempt.update(
                status="complete",
                response=response,
                elapsed_seconds=time.monotonic() - started,
            )
        except Exception as exc:
            attempt["status"] = "failed_or_unknown"
            if isinstance(exc, ProviderHTTPError):
                attempt["http_status"] = exc.status
            atomic(ledger_path, json_text(ledger) + "\n")
            raise
        atomic(ledger_path, json_text(ledger) + "\n")
    return ledger


def evaluate(plan, ledger):
    if ledger["plan_hash"] != digest(plan):
        raise ValueError("Plan identity mismatch")
    previews = validate_plan(plan)
    responses = {
        a["request_hash"]: a["response"]
        for a in ledger["attempts"]
        if a["status"] == "complete"
    }
    assessments = {p["id"]: decode(responses[p["request_hash"]], p) for p in previews}
    records, labels = plan["records"], plan["labels"]
    classified = rank(records, plan["as_of"], assessments)
    baseline = rank(records, plan["as_of"])
    chronological = sorted(
        records, key=lambda r: (-timestamp(r["created_at"]).timestamp(), r["id"])
    )
    dimensions = {}
    disagreements = []
    for name in previews[0]["request"]["questions"]:
        confusion = {}
        matches = 0
        for record in records:
            uri = record["id"]
            expected, actual = (
                labels[uri]["dimensions"][name],
                assessments[uri][name].label,
            )
            key = expected + " → " + actual
            confusion[key] = confusion.get(key, 0) + 1
            matches += expected == actual
            if expected != actual:
                disagreements.append(
                    {
                        "id": uri,
                        "dimension": name,
                        "annotation": expected,
                        "prediction": actual,
                    }
                )
        dimensions[name] = {
            "matches": matches,
            "total": len(records),
            "agreement": matches / len(records),
            "confusion": confusion,
        }
    gains = {
        uri: {"high": 2, "medium": 1, "low": 0, "unclear": 0}[
            label["dimensions"]["relevance"]
        ]
        for uri, label in labels.items()
    }

    def dcg(ids):
        return sum(
            (2 ** gains[uri] - 1) / math.log2(i + 2) for i, uri in enumerate(ids[:5])
        )

    ideal = dcg(sorted(gains, key=lambda uri: -gains[uri]))
    orders = {
        "recency_only": [r["id"] for r in chronological],
        "recency_diversity": [r["record"]["id"] for r in baseline],
        "classified": [r["record"]["id"] for r in classified],
    }
    predicted = {
        uri
        for uri, choices in assessments.items()
        if choices["relevance"].probabilities["high"] >= 0.5
    }
    positives = {uri for uri, gain in gains.items() if gain == 2}
    tp = len(predicted & positives)
    report = {
        "plan_hash": digest(plan),
        "n": len(records),
        "reference_labels": "agent annotated before inference; not independent human gold",
        "dimensions": dimensions,
        "disagreements": disagreements,
        "review_count": sum(item["review"] for item in classified),
        "high_relevance_at_p_0_5": {
            "true_positives": tp,
            "false_positives": len(predicted - positives),
            "false_negatives": len(positives - predicted),
            "precision": tp / len(predicted) if predicted else None,
            "recall": tp / len(positives) if positives else None,
        },
        "ndcg_at_5": {
            name: dcg(ids) / ideal if ideal else None for name, ids in orders.items()
        },
        "orders": orders,
        "input_tokens": sum(
            a["response"]["usage"]["input_tokens"]
            for a in ledger["attempts"]
            if a["status"] == "complete"
        ),
        "attempts": len(ledger["attempts"]),
        "completed_classifications": len(responses),
        "failed_attempts": sum(a["status"] != "complete" for a in ledger["attempts"]),
        "reserved_cost_usd": len(ledger["attempts"]) * RESERVATION_USD,
    }
    report["estimated_cost_usd"] = (
        report["input_tokens"] * PRICE_PER_MILLION / 1_000_000
    )
    return report, classified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument(
        "--execute", action="store_true", help="Spend within the fixed pilot bounds"
    )
    parser.add_argument(
        "--recover-request",
        help="Explicitly recover one recorded HTTP 402 request hash",
    )
    args = parser.parse_args()
    plan = json.loads((args.folder / "plan.json").read_text())
    previews = validate_plan(plan)
    if not args.execute:
        print(
            json.dumps(
                {
                    "records": len(previews),
                    "plan_hash": digest(plan),
                    "max_cost_usd": len(previews) * RESERVATION_USD,
                }
            )
        )
        return
    lock = args.folder / ".pilot-lock"
    with lock.open("x"):
        pass
    client = None
    try:
        client = SingleAttemptClient()
        ledger = run(plan, args.folder, client, recover_request=args.recover_request)
        report, queue = evaluate(plan, ledger)
        atomic(args.folder / "report.json", json.dumps(report, indent=2) + "\n")
        atomic(
            args.folder / "queue.jsonl", "".join(json_text(row) + "\n" for row in queue)
        )
        atomic(args.folder / "digest.html", render(queue))
        atomic(
            args.folder / "status.json",
            json.dumps(
                {
                    "status": "complete",
                    "plan_hash": digest(plan),
                    "http_attempts": len(ledger["attempts"]),
                    "completed_classifications": report["completed_classifications"],
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                },
                indent=2,
            )
            + "\n",
        )
        print(json.dumps(report, indent=2))
    except (RuntimeError, ValueError) as exc:
        parser.exit(1, f"Pilot stopped: {exc}\n")
    finally:
        if client:
            client.close()
        lock.unlink()


if __name__ == "__main__":
    main()
