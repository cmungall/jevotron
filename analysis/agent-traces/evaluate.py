"""Compare saved jt output with labels that were withheld from model inputs."""

import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
DATA = ROOT / ".jevotron/public-traces"
LABELS = {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1}


def metrics(cases):
    correct = sum(c["gold"] == c["predicted"] for c in cases)
    support = Counter(c["gold"] for c in cases)
    confusion = {
        str(gold): {
            str(pred): sum(c["gold"] == gold and c["predicted"] == pred for c in cases)
            for pred in [-1, 0, 1]
        }
        for gold in [-1, 0, 1]
    }
    tp = sum(c["gold"] == -1 and c["predicted"] == -1 for c in cases)
    predicted_negative = sum(c["predicted"] == -1 for c in cases)
    return {
        "n": len(cases),
        "correct": correct,
        "accuracy": correct / len(cases) if cases else None,
        "majority_baseline": max(support.values()) / len(cases) if cases else None,
        "confusion_gold_rows_prediction_columns": confusion,
        "negative_precision": tp / predicted_negative if predicted_negative else None,
        "negative_recall": tp / support[-1] if support[-1] else None,
    }


def main():
    path = Path(sys.argv[1])
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    originals = {
        str(row["total_index"]): row
        for row in map(json.loads, (DATA / "sample.jsonl").read_text().splitlines())
    }
    manifest = json.loads((HERE / "sample-manifest.json").read_text())
    domains = {str(row["total_index"]): row["domain"] for row in manifest["sample"]}
    seen = set()
    cases = []
    for row in rows:
        assert row["id"] not in seen
        seen.add(row["id"])
        original = originals[row["id"]]
        expected = {f"/messages/{i}" for i in original["step_labels"]} | {
            "/answer_text"
        }
        assert {field["path"] for field in row["fields"]} == expected
        for field in row["fields"]:
            kind = "outcome" if field["path"] == "/answer_text" else "step"
            gold = (
                original["final_label"]
                if kind == "outcome"
                else original["step_labels"][field["path"].split("/")[-1]]
            )
            cases.append(
                {
                    "trace_id": row["id"],
                    "domain": domains[row["id"]],
                    "kind": kind,
                    "path": field["path"],
                    "gold": gold,
                    "predicted": LABELS[field["label"]],
                    "probabilities": field["probabilities"],
                    "confidence": field["confidence"],
                    "request_hash": row["request_hash"],
                }
            )
    summary = {
        "model": sorted({row["model"] for row in rows}),
        "traces_assessed": len(rows),
        "traces_selected": len(originals),
        "complete_selected_sample": seen == set(originals),
        "overall": {
            kind: metrics([c for c in cases if c["kind"] == kind])
            for kind in ["step", "outcome"]
        },
        "by_domain": {
            domain: {
                kind: metrics(
                    [c for c in cases if c["kind"] == kind and c["domain"] == domain]
                )
                for kind in ["step", "outcome"]
            }
            for domain in sorted(set(domains.values()))
        },
        "usage": [row["usage"] for row in rows],
        "total_original_call_tokens": {
            key: sum(row["usage"].get(key, 0) for row in rows)
            for key in ["input_tokens", "output_tokens"]
        },
    }
    stem = "results" if summary["complete_selected_sample"] else "pilot-results"
    (HERE / f"{stem}.json").write_text(json.dumps(summary, indent=2) + "\n")
    (HERE / f"{stem}-fields.jsonl").write_text(
        "".join(json.dumps(case) + "\n" for case in cases)
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "usage"}, indent=2))


if __name__ == "__main__":
    main()
