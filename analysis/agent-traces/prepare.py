"""Download pinned public JSONL and choose a small reproducible pilot."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / ".jevotron/public-traces"
HERE = Path(__file__).parent
REPO = "LulaCola/AgentProcessBench"
REVISION = "cd81f326aece8b0a3f6359e20df370690d3a98bf"
DOMAINS = ["hotpotqa", "gaia_dev", "bfcl", "tau2"]


def main():
    (DATA / "raw").mkdir(parents=True, exist_ok=True)
    selected = {}
    sources = []
    manifest = []
    for domain in DOMAINS:
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{domain}/test.jsonl"
        path = DATA / "raw" / f"{domain}.jsonl"
        # Always fetch the pinned revision; no moving-main input.
        response = httpx.get(url, follow_redirects=True, timeout=60)
        response.raise_for_status()
        path.write_bytes(response.content)
        rows = [json.loads(line) for line in path.read_text().splitlines() if line]
        eligible = [
            row
            for row in rows
            if len(json.dumps(row)) <= 80000 and len(row["step_labels"]) <= 20
        ]
        ordered = sorted(
            eligible,
            key=lambda row: hashlib.sha256(
                f"jt-pilot-2026-09-25:{domain}:{row['total_index']}".encode()
            ).hexdigest(),
        )
        picked = []
        queries = set()
        for label in [-1, 1, -1, 1, -1, 1]:
            row = next(
                row
                for row in ordered
                if row["final_label"] == label and row["query_index"] not in queries
            )
            queries.add(row["query_index"])
            picked.append(row)
            manifest.append(
                {
                    "domain": domain,
                    "total_index": row["total_index"],
                    "query_index": row["query_index"],
                    "final_label": row["final_label"],
                    "steps": len(row["step_labels"]),
                    "step_label_counts": dict(Counter(row["step_labels"].values())),
                }
            )
        selected[domain] = picked
        sources.append(
            {
                "domain": domain,
                "url": url,
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "rows": len(rows),
                "eligible": len(eligible),
            }
        )
    # Interleave domains so --limit 4 assesses one of each.
    sample = [selected[domain][i] for i in range(6) for domain in DOMAINS]
    (DATA / "sample.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in sample)
    )
    (HERE / "sample-manifest.json").write_text(
        json.dumps({"sources": sources, "sample": manifest}, indent=2) + "\n"
    )
    print(
        f"Selected {len(sample)} traces, {sum(len(r['step_labels']) for r in sample)} steps"
    )


if __name__ == "__main__":
    main()
