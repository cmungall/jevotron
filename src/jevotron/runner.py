"""Independent chunks → batched field questions → cached assessments."""

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from jevotron.batching import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_BATCH_TOKENS,
    Batch,
    BatchOptions,
    EntryPlan,
    estimate_request,
    pack,
    prepared,
    windows,
)
from jevotron.cache import Cache
from jevotron.client import (
    ContextLimitError,
    JevClient,
    JevError,
    OpenAIDecisionsClient,
    RefusalError,
    is_openai_model,
    service_name,
    to_openai,
)
from jevotron.config import Config
from jevotron.models import Chunk, FieldResult, Result, json_text, resolve
from jevotron.requests import make_request, request_hash


class Evaluator(Protocol):
    def evaluate(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def preview(
    chunks: Iterable[Chunk],
    config: Config | None = None,
    *,
    batch_size: str | int = DEFAULT_BATCH_SIZE,
    batch_tokens: int = DEFAULT_BATCH_TOKENS,
) -> Iterator[dict]:
    """Show exact planned requests, assuming cache misses, without network/cache I/O."""
    config = config or Config()
    config.validate()
    options = BatchOptions(batch_size, batch_tokens)
    for group in windows(prepared(chunks, config, options), options):
        for batch in pack(group, options):
            indexes = list(dict.fromkeys(index for index, _ in batch.owners.values()))
            total, context = estimate_request(batch.request)
            entries = [
                {
                    "id": group[index].chunk.id,
                    "source": group[index].chunk.source,
                    "fields": group[index].paths,
                    "absent": list(group[index].chunk.absent or []),
                    "assessment_hash": group[index].key,
                }
                for index in indexes
            ]
            baseline = 0
            for index in indexes:
                legacy, _ = make_request(group[index].chunk, config)
                selected = {
                    field for owner, field in batch.owners.values() if owner == index
                }
                legacy["questions"] = {
                    k: q for k, q in legacy["questions"].items() if k in selected
                }
                baseline += estimate_request(legacy)[0]
            item = {
                "request_hash": request_hash(batch.request),
                "request": batch.request,
                "layout": group[indexes[0]].layout,
                "estimated_tokens": total,
                "estimated_context_tokens": context,
                "estimated_unbatched_tokens": baseline,
                "estimated_tokens_saved": baseline - total,
                "token_budget": options.tokens,
                "context_budget": options.context_tokens,
                "entries": entries,
                "question_entries": {
                    key: {
                        "id": group[index].chunk.id,
                        "field": group[index].paths[int(field.removeprefix("field_"))],
                    }
                    for key, (index, field) in batch.owners.items()
                },
                "cache_assumption": "all misses",
            }
            # Cache identities use the canonical request; show what is sent.
            if is_openai_model(config.model):
                item["wire_request"] = to_openai(batch.request)
            # Keep the familiar entry preview fields for single-entry requests.
            if len(indexes) == 1:
                item.update(
                    {k: v for k, v in entries[0].items() if k != "assessment_hash"}
                )
            yield item


def _probability(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and 0 <= value <= 1
    )


def validate_response(response: Any, request: dict) -> None:
    """Never persist failed, partial, or malformed assessments as successes."""
    try:
        if (
            not isinstance(response, dict)
            or not isinstance(response["model"], str)
            or not response["model"]
        ):
            raise ValueError
        answers = response["answers"]
        if not isinstance(answers, dict) or set(answers) != set(request["questions"]):
            raise ValueError
        for key, question in request["questions"].items():
            answer = answers[key]
            probabilities = answer["probabilities"]
            if (
                answer["type"] != "choice"
                or not isinstance(probabilities, dict)
                or set(probabilities) != set(question["criteria"])
                or not all(_probability(p) for p in probabilities.values())
                or not _probability(answer["confidence"])
            ):
                raise ValueError
            # Jev may round each option probability to two decimal places.
            rounded = all(
                math.isclose(p, round(p, 2), abs_tol=1e-12, rel_tol=0)
                for p in probabilities.values()
            )
            tolerance = 0.005 * len(probabilities) + 1e-12 if rounded else 0.001
            if not math.isclose(
                sum(probabilities.values()), 1, rel_tol=0, abs_tol=tolerance
            ):
                raise ValueError
            if answer["choice"] not in probabilities or probabilities[
                answer["choice"]
            ] < max(probabilities.values()):
                raise ValueError
        if not isinstance(response.get("usage", {}), dict):
            raise ValueError
        json_text(response)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise JevError(
            f"{service_name(request.get('model'))} returned an invalid or incomplete assessment; "
            "not cached"
        ) from None


@dataclass
class ScanStats:
    """Network usage counted once per successful response, never per entry."""

    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_tokens: int = 0


def _evaluate(batch: Batch, client: Evaluator, stats: ScanStats, depth=0):
    stats.api_calls += 1
    try:
        response = client.evaluate(batch.request)
    except ContextLimitError as error:
        keys = list(batch.owners)
        if len(keys) == 1 or depth >= 10:
            raise ContextLimitError(
                f"{service_name(batch.request.get('model'))} rejected the context "
                "size after splitting; reduce guidance, "
                "exemplars, or entry context. Nothing was truncated."
            ) from error
        middle = len(keys) // 2
        for subset in (keys[:middle], keys[middle:]):
            child = Batch(
                {
                    **batch.request,
                    "questions": {k: batch.request["questions"][k] for k in subset},
                },
                {k: batch.owners[k] for k in subset},
            )
            yield from _evaluate(child, client, stats, depth + 1)
        return
    validate_response(response, batch.request)
    stats.estimated_tokens += estimate_request(batch.request)[0]
    usage = response.get("usage", {})
    for name in ("input_tokens", "output_tokens"):
        value = usage.get(name, 0)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            setattr(stats, name, getattr(stats, name) + value)
    yield batch, response


def _refusals_named(results, batch: Batch, plans: list[EntryPlan]):
    """Say which entry and field was refused, so it can be excluded or edited."""
    try:
        yield from results
    except RefusalError as error:
        owner = batch.owners.get(error.question)
        if owner is None:
            raise
        index, field = owner
        plan = plans[index]
        path = plan.paths[int(field.removeprefix("field_"))]
        raise RefusalError(
            f"{service_name(batch.request.get('model'))} declined to assess field "
            f"{path} of entry {plan.chunk.id!r}; nothing from that request was "
            "cached. Exclude or edit that entry to continue.",
            error.question,
        ) from None


def _result(
    plan: EntryPlan, response: dict, assessed_at: str, cached: bool, config: Config
):
    fields = []
    for i, path in enumerate(plan.paths):
        answer = response["answers"][f"field_{i}"]
        fields.append(
            FieldResult(
                path,
                resolve(plan.chunk.data, path),
                answer["choice"],
                answer["probabilities"],
                answer["confidence"],
                answer["probabilities"][config.anomaly_label],
            )
        )
    worst = max(fields, key=lambda f: f.score)
    return Result(
        plan.chunk.id,
        plan.chunk.source,
        worst.label,
        worst.score,
        worst.score >= config.threshold,
        fields,
        list(plan.chunk.absent or []),
        response["model"],
        assessed_at,
        plan.key,
        cached,
        response.get("usage", {}),
    )


def scan(
    chunks: Iterable[Chunk],
    config: Config | None = None,
    *,
    cache: str | Path | None = ".jevotron/cache.sqlite3",
    refresh: bool = False,
    client: Evaluator | None = None,
    batch_size: str | int = DEFAULT_BATCH_SIZE,
    batch_tokens: int = DEFAULT_BATCH_TOKENS,
    stats: ScanStats | None = None,
) -> Iterator[Result]:
    """Stream ordered results from bounded batches of independent questions.

    The caller owns an injected client. Credentials are needed only for misses.
    Complete entries are committed after each successful request, even when a
    later request fails. Shared-layout cache keys do not depend on neighbors.
    """
    config = config or Config()
    config.validate()
    options = BatchOptions(batch_size, batch_tokens)
    stats = stats if stats is not None else ScanStats()
    store = Cache(cache) if cache is not None else None
    owned_client = None
    try:
        for group in windows(prepared(chunks, config, options), options):
            ready = {}
            missing = []
            positions = []
            aliases = {}
            unique = {}
            for position, plan in enumerate(group):
                saved = store.get(plan.key) if store and not refresh else None
                if saved is not None:
                    response, assessed_at = saved
                    validate_response(response, plan.request)
                    ready[position] = _result(plan, response, assessed_at, True, config)
                elif plan.key in unique:
                    aliases.setdefault(unique[plan.key], []).append(position)
                else:
                    unique[plan.key] = position
                    positions.append(position)
                    missing.append(plan)
            next_position = 0
            while next_position in ready:
                yield ready.pop(next_position)
                next_position += 1
            if not missing:
                continue
            if client is None:
                owned_client = (
                    OpenAIDecisionsClient
                    if is_openai_model(config.model)
                    else JevClient
                )()
                client = owned_client
            responses = [{"answers": {}} for _ in missing]
            provenance = [[] for _ in missing]
            for proposed in pack(missing, options):
                for batch, response in _refusals_named(
                    _evaluate(proposed, client, stats), proposed, missing
                ):
                    stamp = datetime.now(timezone.utc).isoformat()
                    wire_hash = request_hash(batch.request)
                    first_index = next(iter(batch.owners.values()))[0]
                    # Unsplit legacy requests are already stored verbatim in
                    # assessments; avoid doubling existing cache storage.
                    if store and wire_hash != missing[first_index].key:
                        store.put_batch(wire_hash, batch.request, response, stamp)
                    touched = set()
                    for key, (index, field) in batch.owners.items():
                        entry_response = responses[index]
                        if (
                            entry_response.get("model", response["model"])
                            != response["model"]
                        ):
                            raise JevError(
                                f"{service_name(config.model)} model changed while "
                                "assessing a split entry; not cached"
                            )
                        entry_response["model"] = response["model"]
                        entry_response["answers"][field] = response["answers"][key]
                        touched.add(index)
                    for index in sorted(touched):
                        provenance[index].append(wire_hash)
                        plan = missing[index]
                        entry_response = responses[index]
                        if len(entry_response["answers"]) != len(plan.paths):
                            continue
                        # Legacy unsplit usage retains its existing shape. Shared
                        # or split requests reference the ledger, never duplicate
                        # a request's token bill on every participating entry.
                        entry_response["usage"] = (
                            response.get("usage", {})
                            if plan.layout == "entry-v1" and len(provenance[index]) == 1
                            else {"batch_request_hashes": provenance[index]}
                        )
                        validate_response(entry_response, plan.request)
                        if store:
                            store.put(plan.key, plan.request, entry_response, stamp)
                        position = positions[index]
                        ready[position] = _result(
                            plan, entry_response, stamp, False, config
                        )
                        for alias in aliases.get(position, []):
                            ready[alias] = _result(
                                group[alias], entry_response, stamp, True, config
                            )
                    while next_position in ready:
                        yield ready.pop(next_position)
                        next_position += 1
    finally:
        if owned_client:
            owned_client.close()
        if store:
            store.close()
