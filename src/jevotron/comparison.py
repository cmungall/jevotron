"""Compare two complete datasets under one inference and warning policy."""

import hashlib
import html
from collections import Counter
from collections.abc import Iterable
from contextlib import closing
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from jevotron.client import ENDPOINT
from jevotron.config import Config
from jevotron.models import Chunk, Result, json_text, resolve
from jevotron.runner import Evaluator, make_request, request_hash, scan


def _digest(value) -> str:
    return hashlib.sha256(json_text(value).encode()).hexdigest()


def _index(chunks: Iterable[Chunk], side: str) -> dict[str, Chunk]:
    entries = {}
    for chunk in chunks:
        chunk.field_paths()
        if chunk.id in entries:
            raise ValueError(f"Duplicate stable ID in {side}: {chunk.id!r}")
        # Parsers may reuse a mutable row or fields buffer between yields.
        # Preserve the same per-yield values that a streaming scan would assess.
        entries[chunk.id] = deepcopy(chunk)
    return entries


def _inference(config: Config) -> dict:
    settings = {
        "endpoint": ENDPOINT,
        "model": config.model,
        "guidance": config.guidance,
        "exemplars": config.exemplars,
        "criteria": config.choice_criteria(),
        "anomaly_label": config.anomaly_label,
    }
    if references := getattr(config, "references", None):
        settings["references"] = [
            {
                "kind": type(reference).__name__,
                **{
                    key: value
                    for key, value in asdict(reference).items()
                    if key not in ("path", "nodes", "edges")
                },
            }
            for reference in sorted(references, key=lambda item: item.name)
        ]
    return {"fingerprint": _digest(settings), "settings": settings}


def _check_coverage(baseline: dict[str, Chunk], candidate: dict[str, Chunk]) -> None:
    for ident in baseline.keys() & candidate.keys():
        before, after = baseline[ident], candidate[ident]
        paths = [set(before.field_paths()), set(after.field_paths())]
        for selected, other, chunk in (
            (paths[0], paths[1], after),
            (paths[1], paths[0], before),
        ):
            for path in sorted(selected - other):
                try:
                    resolve(chunk.data, path)
                except ValueError:
                    continue  # This field was actually added to or removed from the data.
                raise ValueError(
                    f"Incompatible assessment coverage for ID {ident!r}: {path!r} "
                    "exists on both sides but is assessed on only one. Select matching fields."
                )


def _assessment(result: Result | None, chunk: Chunk | None) -> dict | None:
    if result is None:
        return None
    details = result.to_dict()
    details.pop("fields")
    details.update(id=chunk.id, source=chunk.source, content_hash=_digest(chunk.data))
    return details


@dataclass
class Comparison:
    metadata: dict
    entries: list[dict]

    def violates(self, policy: str) -> bool:
        if policy not in ("none", "new-warning", "any-warning"):
            raise ValueError(f"Unknown comparison policy: {policy!r}")
        warnings = self.metadata["summary"]["field_warnings"]
        return policy != "none" and (
            warnings["new"] > 0
            or (policy == "any-warning" and warnings["persistent"] > 0)
        )

    def jsonl(self, policy: str = "none") -> str:
        header = {
            **self.metadata,
            "policy": policy,
            "policy_violated": self.violates(policy),
        }
        return "\n".join(json_text(row) for row in [header, *self.entries]) + "\n"

    def markdown(self, policy: str = "none") -> str:
        summary = self.metadata["summary"]
        outcome = "Policy violation" if self.violates(policy) else "Policy passed"
        lines = [
            "# Dataset comparison",
            "",
            f"**{outcome}** (`{policy}`). Both inputs were assessed completely.",
            "",
            "Entries: "
            + ", ".join(f"{count} {kind}" for kind, count in summary["entries"].items())
            + ".",
            "Field warnings: "
            + ", ".join(
                f"{count} {kind}" for kind, count in summary["field_warnings"].items()
            )
            + ".",
            "",
            f"Threshold for both inputs: **{self.metadata['threshold']}**. "
            f"Inference fingerprint: `{self.metadata['inference']['fingerprint']}`.",
            f"Resolved model: {_cell(', '.join(self.metadata['models']) or '(no entries)')}.",
            "",
            "| Entry | Change | Field | Warning | Before value | After value | Score before → after | Δ score | Baseline source / assessed | Candidate source / assessed |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for entry in self.entries:
            for field in entry["fields"]:
                if entry["change"] == "unchanged" and field["warning"] == "none":
                    continue
                before, after = field["before"], field["after"]
                values = [
                    entry["id"],
                    entry["change"],
                    field["path"] or "(root)",
                    field["warning"],
                    json_text(before["value"]) if before else "(absent)",
                    json_text(after["value"]) if after else "(absent)",
                    f"{before['score'] if before else '—'} → {after['score'] if after else '—'}",
                    field["score_delta"] if field["score_delta"] is not None else "—",
                    _provenance(entry["baseline"]),
                    _provenance(entry["candidate"]),
                ]
                lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
        lines.extend(
            ["", "Unchanged fields without warnings are omitted from this table."]
        )
        return "\n".join(lines) + "\n"


def _cell(value) -> str:
    # Treat arbitrary input values as text, including HTML and Markdown syntax.
    text = html.escape(str(value), quote=False)
    return (
        text.replace("\\", "&#92;")
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("*", "&#42;")
        .replace("_", "&#95;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
        .replace("\r", " ")
        .replace("\n", "<br>")
    )


def _provenance(assessment) -> str:
    return (
        f"{assessment['source'] or '(unspecified)'} / {assessment['assessed_at']}"
        if assessment
        else "(absent)"
    )


def compare(
    baseline: Iterable[Chunk],
    candidate: Iterable[Chunk],
    config: Config | None = None,
    *,
    cache: str | Path | None = ".jevotron/cache.sqlite3",
    refresh: bool = False,
    client: Evaluator | None = None,
) -> Comparison:
    """Compare stable Chunk IDs; validate both inputs before any API calls.

    One config and threshold apply to both sides. Identical requests share one
    assessment even with refresh or persistence disabled. Input iterators and
    injected clients remain caller-owned, as with scan(). Results are buffered.
    """
    config = config or Config()
    config.validate()
    inference = _inference(config)
    sides = [_index(baseline, "baseline"), _index(candidate, "candidate")]
    _check_coverage(*sides)
    resolver = None
    if getattr(config, "references", None):
        from jevotron.context import ContextResolver

        # Index reference sources once for the complete preflight. scan() owns
        # its assessment snapshot; changed dependencies fail the hash check below.
        resolver = ContextResolver(config.references)
    requests = set()
    batches = []
    identities = []
    for chunks in sides:
        keys = {}
        batch = []
        for ident, chunk in chunks.items():
            request, _ = (
                make_request(chunk, config, context=resolver.resolve(chunk))
                if resolver is not None
                else make_request(chunk, config)
            )
            key = request_hash(request)
            keys[ident] = key
            if key not in requests:
                batch.append(chunk)
                requests.add(key)
        identities.append(keys)
        batches.append(batch)
    assessed = {}
    for batch in batches:
        if batch:
            with closing(
                scan(batch, config, cache=cache, refresh=refresh, client=client)
            ) as results:
                for result in results:
                    assessed[result.request_hash] = result
    if set(assessed) != requests or _inference(config) != inference:
        raise ValueError(
            "Inference inputs changed during comparison; retry with stable inputs and settings"
        )
    models = sorted({result.model for result in assessed.values()})
    if len(models) > 1:
        raise ValueError(
            "Comparison requires one resolved model; found "
            + ", ".join(models)
            + ". Pin the model and refresh the cache before comparing."
        )
    entries = []
    counts = Counter({kind: 0 for kind in ("added", "removed", "changed", "unchanged")})
    warnings = Counter({kind: 0 for kind in ("new", "resolved", "persistent")})
    for ident in sorted(sides[0].keys() | sides[1].keys()):
        chunks = [side.get(ident) for side in sides]
        results = [assessed.get(keys.get(ident)) for keys in identities]
        before, after = chunks
        change = (
            "added"
            if before is None
            else "removed"
            if after is None
            else "unchanged"
            if json_text(before.data) == json_text(after.data)
            else "changed"
        )
        counts[change] += 1
        fields = [
            {field.path: field for field in result.fields} if result else {}
            for result in results
        ]
        transitions = []
        for path in sorted(fields[0].keys() | fields[1].keys()):
            pair = [asdict(side[path]) if path in side else None for side in fields]
            for field in pair:
                if field is not None:
                    field["warning"] = field["score"] >= config.threshold
            left, right = pair
            old_warning = left is not None and left["warning"]
            new_warning = right is not None and right["warning"]
            warning = (
                "persistent"
                if old_warning and new_warning
                else "new"
                if new_warning
                else "resolved"
                if old_warning
                else "none"
            )
            if warning != "none":
                warnings[warning] += 1
            transitions.append(
                {
                    "path": path,
                    "warning": warning,
                    "before": left,
                    "after": right,
                    "score_delta": right["score"] - left["score"]
                    if left and right
                    else None,
                }
            )
        entries.append(
            {
                "type": "entry",
                "id": ident,
                "change": change,
                "baseline": _assessment(results[0], before),
                "candidate": _assessment(results[1], after),
                "fields": transitions,
            }
        )
    return Comparison(
        {
            "type": "comparison",
            "schema_version": 1,
            "complete": True,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "inference": inference,
            "threshold": config.threshold,
            "models": models,
            "summary": {
                "entries": dict(counts),
                "field_warnings": dict(warnings),
                "unique_requests": len(assessed),
                "cached_requests": sum(result.cached for result in assessed.values()),
            },
        },
        entries,
    )
