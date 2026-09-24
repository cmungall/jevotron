#!/usr/bin/env python3
"""Select a bounded review queue from saved jt JSONL without API calls."""

import argparse
import heapq
import json
import math
from pathlib import Path


def shortlist(path: Path, top: int, threshold: float) -> dict:
    candidates = []
    total = 0
    matched = 0
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                score = row["score"]
                if (
                    not isinstance(row["id"], str)
                    or not row["id"]
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                    or not math.isfinite(score)
                    or not 0 <= score <= 1
                ):
                    raise ValueError(
                        "expected a nonempty string id and score in [0, 1]"
                    )
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError(
                    f"{path}:{line_number}: invalid jt result: {exc}"
                ) from exc
            total += 1
            if score < threshold:
                continue
            matched += 1
            # Earlier rows win ties; the unique line number prevents dict comparisons.
            item = (score, -line_number, row)
            if len(candidates) < top:
                heapq.heappush(candidates, item)
            elif item[:2] > candidates[0][:2]:
                heapq.heapreplace(candidates, item)
    return {
        "report_rows": total,
        "threshold": threshold,
        "matching_rows": matched,
        "candidates": [item[2] for item in sorted(candidates, reverse=True)],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Saved jt JSONL report")
    parser.add_argument(
        "--top", type=int, default=20, help="Maximum candidates (default 20)"
    )
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()
    if args.top < 1:
        parser.error("--top must be positive")
    if not math.isfinite(args.threshold) or not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")
    try:
        result = shortlist(args.report, args.top, args.threshold)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
