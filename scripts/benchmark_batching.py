"""Compare request packing and, optionally, live Jev scores on public examples.

    uv run python scripts/benchmark_batching.py
    uv run python scripts/benchmark_batching.py --live --output /tmp/batching.json

The long-guidance cases repeat the same rules to isolate shared-context costs;
they are synthetic size tests, not a substitute for a curated domain benchmark.
Live mode sends the repository's public example data to Jev and incurs API usage.
"""

import argparse
import json
import time
from dataclasses import replace
from itertools import islice
from pathlib import Path

from jevotron import preview, scan
from jevotron.config import load_config
from jevotron.runner import ScanStats

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {
        "live": args.live,
        "note": "Long guidance repeats identical rules 20 times; public examples only.",
        "cases": [],
    }
    for folder, filename in (("units", "units.obo"), ("airports", "spiked.csv")):
        directory = ROOT / "examples" / folder
        config = load_config(directory / "jev_config.py")
        source = iter(config.parser(directory / filename))
        try:
            chunks = list(islice(source, 3))
        finally:
            if hasattr(source, "close"):
                source.close()
        for repeats in (1, 20):
            settings = replace(
                config, guidance="\n\n".join([config.guidance] * repeats)
            )
            case = {"dataset": folder, "guidance_repeats": repeats, "modes": []}
            baseline = None
            for mode in (1, 8, "auto"):
                plans = list(preview(chunks, settings, batch_size=mode))
                record = {
                    "batch_size": mode,
                    "planned_requests": len(plans),
                    "estimated_input_tokens": sum(p["estimated_tokens"] for p in plans),
                    "layouts": sorted({p["layout"] for p in plans}),
                }
                if args.live:
                    stats = ScanStats()
                    start = time.monotonic()
                    results = list(
                        scan(chunks, settings, batch_size=mode, cache=None, stats=stats)
                    )
                    scores = {
                        (r.id, f.path): (f.label, f.probabilities, f.confidence)
                        for r in results
                        for f in r.fields
                    }
                    if baseline is None:
                        baseline = scores
                    record.update(
                        seconds=round(time.monotonic() - start, 3),
                        api_calls=stats.api_calls,
                        input_tokens=stats.input_tokens,
                        output_tokens=stats.output_tokens,
                        fields=len(scores),
                        label_changes=sum(
                            v[0] != baseline[k][0] for k, v in scores.items()
                        ),
                        max_probability_delta=max(
                            abs(p - baseline[k][1][label])
                            for k, v in scores.items()
                            for label, p in v[1].items()
                        ),
                        max_confidence_delta=max(
                            abs(v[2] - baseline[k][2]) for k, v in scores.items()
                        ),
                        assessments=[r.to_dict() for r in results],
                    )
                case["modes"].append(record)
            report["cases"].append(case)
    output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
