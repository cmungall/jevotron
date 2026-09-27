"""Offline contract/lifecycle tests. Fixtures are synthetic, never collected posts."""

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import pytest

from jevotron.client import JevError
from jevotron.extensions.feeds.adapters import bluesky as bluesky_adapter

FIXTURES = Path(__file__).parents[1] / "examples/classified_feeds/fixtures"
# Examples intentionally are not shipped in the core wheel. Load the package
# explicitly so both `pytest` and `python -m pytest` exercise the checkout.
spec = importlib.util.spec_from_file_location(
    "classified_feeds_demo", FIXTURES.parent / "__init__.py"
)
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
cli = importlib.import_module("classified_feeds_demo.__main__")
bluesky = importlib.import_module("classified_feeds_demo.bluesky")
mail = importlib.import_module("classified_feeds_demo.mail")
pipeline = importlib.import_module("classified_feeds_demo.pipeline")
pilot = importlib.import_module("classified_feeds_demo.pilot")
PublicClient, normalize, reconcile = (
    bluesky.PublicClient,
    bluesky.normalize,
    bluesky.reconcile,
)
normalize_thread, proposed_labels = mail.normalize_thread, mail.proposed_labels
decode, make_preview, rank, render = (
    pipeline.decode,
    pipeline.make_preview,
    pipeline.rank,
    pipeline.render,
)
NOW = "2026-09-23T12:00:00+00:00"


@pytest.fixture
def feed():
    return json.loads((FIXTURES / "bluesky.json").read_text())["feed"]


def response_for(preview, labels=None):
    answers = {}
    for name, question in preview["request"]["questions"].items():
        keys = list(question["criteria"])
        selected = (labels or {}).get(name, keys[0])
        probabilities = {k: 0.0 for k in keys}
        probabilities[selected] = 1.0
        answers[name] = {
            "type": "choice",
            "choice": selected,
            "probabilities": probabilities,
            "confidence": 1.0,
        }
    return {"model": preview["request"]["model"], "answers": answers, "usage": {}}


def test_reply_quote_context_and_injection_are_data(feed):
    reply = normalize(feed[1], NOW, {})
    assert reply["context"]["parent"]["status"] == "available"
    quoted = normalize(feed[2], NOW, {})
    assert quoted["context"]["quote"]["text"] == feed[0]["post"]["record"]["text"]
    preview = make_preview(quoted, "ontologies")
    assert len(preview["request"]["questions"]) == 4
    assert "send credentials" in preview["request"]["state"]["record"]["text"]
    assert all(
        "send credentials" not in q["instructions"]
        for q in preview["request"]["questions"].values()
    )
    rendered = render(rank([quoted], NOW))
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "default-src 'none'" in rendered


def test_missing_blocked_and_changed_context(feed):
    item = {"post": feed[1]["post"]}
    uri = feed[0]["post"]["uri"]
    for view, expected in [
        ({}, "not_fetched"),
        ({"blocked": True}, "blocked"),
        ({"not_found": True}, "unavailable"),
        ({"cid": "changed"}, "version_mismatch"),
    ]:
        record = normalize(item, NOW, {uri: view})
        assert record["context"]["parent"]["status"] == expected
        assert "text" not in record["context"]["parent"]


def test_request_identity_excludes_observation_but_includes_context_and_policy(feed):
    record = normalize(feed[1], NOW, {})
    baseline = make_preview(record, "science")["request_hash"]
    record["observed_at"] = "2027-01-01T00:00:00Z"
    record["handle"] = "new.invalid"
    assert make_preview(record, "science")["request_hash"] == baseline
    assert make_preview(record, "software")["request_hash"] != baseline
    record["context"]["parent"]["status"] = "unavailable"
    assert make_preview(record, "science")["request_hash"] != baseline


def test_choice_decode_and_uncertainty(feed):
    preview = make_preview(normalize(feed[0], NOW, {}), "science")
    response = response_for(preview)
    answer = response["answers"]["relevance"]
    answer.update(
        choice="high",
        confidence=0.7,
        probabilities={"high": 0.5, "medium": 0.4, "low": 0.1, "unclear": 0.0},
    )
    result = decode(response, preview)
    assert result["relevance"].uncertain
    assert not result["topic"].uncertain


@pytest.mark.parametrize("damage", ["partial", "nan", "wrong_choice", "wrong_model"])
def test_reject_bad_assessments(feed, damage):
    preview = make_preview(normalize(feed[0], NOW, {}), "science")
    response = response_for(preview)
    if damage == "partial":
        response["answers"].pop("topic")
    elif damage == "nan":
        response["answers"]["topic"]["confidence"] = float("nan")
    elif damage == "wrong_choice":
        response["answers"]["topic"]["choice"] = "made_up"
    else:
        response["model"] = "some-other-model"
    with pytest.raises((JevError, ValueError)):
        decode(response, preview)


def test_bounded_collection_dedup_and_cursor(feed):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "public.api.bsky.app"
        assert request.method == "GET"
        assert "authorization" not in request.headers
        if request.url.path.endswith("getPosts"):
            assert len(request.url.params.get_list("uris")) <= 25
            return httpx.Response(200, json={"posts": [feed[0]["post"]]})
        if "cursor" in request.url.params:
            return httpx.Response(200, json={"feed": [feed[0]], "cursor": "next-2"})
        return httpx.Response(
            200, json={"feed": [{"post": feed[1]["post"]}, feed[0]], "cursor": "next-1"}
        )

    client = PublicClient(httpx.MockTransport(handler))
    try:
        records, cursor = client.collect(actor="example.invalid", limit=2, pages=2)
    finally:
        client.close()
    assert len(records) == 2
    assert len(requests) == 3
    assert cursor == "next-2"
    assert records[0]["context"]["parent"]["status"] == "available"


@pytest.mark.parametrize("status", [302, 403, 429, 500])
def test_public_errors_fail_closed(status):
    client = PublicClient(httpx.MockTransport(lambda r: httpx.Response(status)))
    try:
        with pytest.raises(RuntimeError, match=str(status)):
            client.collect(query="science")
        assert client.requests == 1
    finally:
        client.close()


def test_search_contract(feed):
    def handler(request):
        assert request.url.path.endswith("searchPosts")
        assert request.url.params["sort"] == "latest"
        assert request.url.params["q"] == "ontology"
        return httpx.Response(200, json={"posts": [feed[0]["post"]]})

    client = PublicClient(httpx.MockTransport(handler))
    try:
        records, cursor = client.collect(query="ontology")
        assert len(records) == 1 and cursor is None
    finally:
        client.close()


def test_repeated_cursor_fails_without_looping(feed):
    client = PublicClient(
        httpx.MockTransport(
            lambda r: httpx.Response(200, json={"feed": [feed[0]], "cursor": "same"})
        )
    )
    try:
        with pytest.raises(ValueError, match="repeated"):
            client.collect(actor="example.invalid", cursor="same")
        assert client.requests == 1
    finally:
        client.close()


def test_recheck_rotates_context_and_post_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    identity = {"actor": "example.invalid", "query": None}
    folder = tmp_path / "public" / pipeline.digest(identity)[:16]
    folder.mkdir(parents=True)
    records = [
        {
            "id": f"at://did:plc:synthetic/app.bsky.feed.post/{i:02}",
            "status": "active",
            "observed_at": NOW,
        }
        for i in range(30)
    ]
    (folder / "state.json").write_text(
        json.dumps(
            {
                "identity": identity,
                "records": records,
                "cursor": None,
            }
        )
    )
    requested = []

    def handler(request):
        requested.append(request.url.params.get_list("uris"))
        return httpx.Response(200, json={"posts": []})

    monkeypatch.setattr(
        bluesky_adapter,
        "PublicClient",
        lambda: PublicClient(httpx.MockTransport(handler)),
    )
    # Mock output only: the minimal records here exercise checkpoint selection.
    monkeypatch.setattr(bluesky_adapter, "write_snapshot", lambda *args: {})
    args = argparse.Namespace(
        command="recheck", actor="example.invalid", query=None, interests="science"
    )
    cli.run(args)
    cli.run(args)
    assert list(map(len, requested)) == [25, 5]
    assert len(set(requested[0] + requested[1])) == 30


def test_explicit_unavailability_purges_content_and_dependents(feed):
    records = [normalize(item, NOW, {}) for item in feed]
    uri = records[0]["id"]
    assert reconcile(records, {}, NOW) == records  # Page omission is not deletion.
    updated = reconcile(records, {uri: {"uri": uri, "not_found": True}}, NOW)
    assert updated[0]["status"] == "unavailable"
    assert "text" not in updated[0]
    assert "text" not in updated[1]["context"]["parent"]
    assert "text" not in updated[2]["context"]["quote"]
    assert len(rank(updated, NOW)) == 2


def test_rank_recency_diversity_and_unassessed(feed):
    first = normalize(feed[0], NOW, {})
    duplicate = {**first, "id": first["id"] + "-duplicate"}
    other = {
        **first,
        "id": first["id"] + "-other",
        "author": "did:plc:other",
        "text": "Different",
    }
    old = {**other, "id": other["id"] + "-old", "created_at": "2020-01-01T00:00:00Z"}
    queue = rank([old, duplicate, other, first], NOW)
    assert queue[0]["record"]["id"] == first["id"]
    assert queue[1]["record"]["id"] == other["id"]
    assert all(
        item["classification_status"] == "unassessed" and item["review"]
        for item in queue
    )
    assert all(item["dimensions"] == {} for item in queue)


def test_synthetic_mail_thread_version_and_shadow_policy():
    fixtures = json.loads((FIXTURES / "mail.json").read_text())
    for fixture in fixtures:
        record = normalize_thread(fixture)
        preview = make_preview(record, "science")
        labels = {"needs_reply": fixture["expected_needs_reply"]}
        choices = decode(response_for(preview, labels), preview)
        proposal = proposed_labels(record, choices)
        assert not proposal["action_authorized"]
        assert proposal["proposed_remove"] == []
        if fixture["expected_needs_reply"] == "yes":
            assert "JT/Needs-reply" in proposal["proposed_add"]
        if not fixture["complete"] or fixture["expected_needs_reply"] == "unclear":
            assert "JT/Review" in proposal["proposed_add"]
        assert proposed_labels(record, {})["proposed_add"] == ["JT/Review"]
    original = normalize_thread(fixtures[0])
    edited = copy.deepcopy(fixtures[0])
    edited["messages"][0]["body"] += " Changed request."
    assert normalize_thread(edited)["version"] != original["version"]
    edited["synthetic"] = False
    with pytest.raises(ValueError, match="synthetic"):
        normalize_thread(edited)


def test_checkpoint_failure_preserves_state_and_no_inference(
    tmp_path, monkeypatch, feed
):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(
        "jevotron.client.JevClient.__init__",
        lambda *a, **k: pytest.fail("Inference attempted"),
    )
    monkeypatch.setattr(
        bluesky_adapter,
        "PublicClient",
        lambda: PublicClient(
            httpx.MockTransport(
                lambda r: httpx.Response(
                    200, json={"feed": [feed[0]], "cursor": "next"}
                )
            )
        ),
    )
    args = argparse.Namespace(
        command="collect",
        actor="example.invalid",
        query=None,
        limit=1,
        pages=1,
        resume=False,
        interests="science",
    )
    cli.run(args)
    state_path = next(tmp_path.glob("public/*/state.json"))
    saved = state_path.read_bytes()
    monkeypatch.setattr(
        bluesky_adapter,
        "PublicClient",
        lambda: PublicClient(httpx.MockTransport(lambda r: httpx.Response(429))),
    )
    args.resume = True
    with pytest.raises(RuntimeError):
        cli.run(args)
    assert state_path.read_bytes() == saved
    assert not state_path.with_name(".lock").exists()


def test_oversized_response_and_invalid_bounds():
    client = PublicClient(
        httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 2_000_001))
    )
    try:
        with pytest.raises(ValueError, match="2 MB"):
            client.collect(actor="example.invalid")
        with pytest.raises(ValueError):
            client.collect(actor="example.invalid", pages=4)
        with pytest.raises(ValueError):
            client.collect(actor="example.invalid", query="science")
    finally:
        client.close()


def pilot_plan(feed):
    record = normalize(feed[0], NOW, {})
    return {
        "records": [record],
        "as_of": NOW,
        "interests": "science",
        "labels": {
            record["id"]: {
                "dimensions": {
                    "topic": "science",
                    "relevance": "high",
                    "content_kind": "resource",
                    "actionability": "act",
                }
            }
        },
    }


def test_pilot_reserves_before_call_and_reuses_verified_results(feed, tmp_path):
    plan = pilot_plan(feed)

    class Evaluator:
        calls = 0

        def evaluate(self, request):
            self.calls += 1
            saved = json.loads((tmp_path / "ledger.json").read_text())
            assert saved["attempts"][-1]["status"] == "started"
            response = response_for({"request": request})
            response["usage"] = {"input_tokens": 100}
            return response

    evaluator = Evaluator()
    ledger = pilot.run(plan, tmp_path, evaluator)
    assert evaluator.calls == 1
    assert pilot.run(plan, tmp_path, evaluator) == ledger
    assert evaluator.calls == 1
    report, queue = pilot.evaluate(plan, ledger)
    assert report["high_relevance_at_p_0_5"]["recall"] == 1
    assert report["ndcg_at_5"]["classified"] == 1
    assert report["estimated_cost_usd"] < report["reserved_cost_usd"] < 0.05
    assert "inference not run" not in render(queue)
    assert "relevance: high" in render(queue)
    plan["interests"] = "changed"
    with pytest.raises(ValueError, match="Frozen plan"):
        pilot.run(plan, tmp_path, evaluator)


def test_pilot_failure_never_retries(feed, tmp_path):
    class Evaluator:
        calls = 0

        def evaluate(self, request):
            self.calls += 1
            raise RuntimeError("network failure")

    evaluator = Evaluator()
    plan = pilot_plan(feed)
    with pytest.raises(RuntimeError, match="network failure"):
        pilot.run(plan, tmp_path, evaluator)
    with pytest.raises(RuntimeError, match="no retry"):
        pilot.run(plan, tmp_path, evaluator)
    assert evaluator.calls == 1
    assert (
        json.loads((tmp_path / "ledger.json").read_text())["attempts"][0]["status"]
        == "failed_or_unknown"
    )


def test_pilot_budget_and_public_only_guard(feed, tmp_path, monkeypatch):
    plan = pilot_plan(feed)
    monkeypatch.setattr(pilot, "BUDGET_USD", 0)
    with pytest.raises(RuntimeError, match="cost limit"):
        pilot.run(plan, tmp_path, None)
    assert not (tmp_path / "ledger.json").exists()
    plan["records"][0]["source"] = "synthetic-mail"
    with pytest.raises(ValueError, match="public Bluesky"):
        pilot.run(plan, tmp_path, None)
    plan["records"] *= 11
    with pytest.raises(ValueError, match="1–10"):
        pilot.run(plan, tmp_path, None)


def test_pilot_refuses_incomplete_reference_labels(feed):
    plan = pilot_plan(feed)
    plan["labels"] = {}
    with pytest.raises(ValueError, match="labeled"):
        pilot.validate_plan(plan)


def test_pilot_explicit_402_recovery_preserves_attempt_and_budget(feed, tmp_path):
    plan = pilot_plan(feed)

    class Evaluator:
        calls = 0

        def evaluate(self, request):
            self.calls += 1
            if self.calls == 1:
                raise pilot.ProviderHTTPError(402)
            response = response_for({"request": request})
            response["usage"] = {"input_tokens": 100}
            return response

    evaluator = Evaluator()
    with pytest.raises(pilot.ProviderHTTPError):
        pilot.run(plan, tmp_path, evaluator)
    key = pilot.validate_plan(plan)[0]["request_hash"]
    with pytest.raises(RuntimeError, match="no retry"):
        pilot.run(plan, tmp_path, evaluator)
    ledger = pilot.run(plan, tmp_path, evaluator, recover_request=key)
    assert evaluator.calls == 2
    assert ledger["attempts"][0]["http_status"] == 402
    assert ledger["attempts"][0]["status"] == "failed_or_unknown"
    assert ledger["max_attempts"] == 11 and ledger["budget_usd"] == 0.05
    report, _ = pilot.evaluate(plan, ledger)
    assert report["input_tokens"] == 100
    assert report["reserved_cost_usd"] == 2 * pilot.RESERVATION_USD
    assert report["failed_attempts"] == 1
    pilot.run(plan, tmp_path, evaluator, recover_request=key)
    assert evaluator.calls == 2


def test_pilot_recovery_does_not_bypass_spend_limit(feed, tmp_path, monkeypatch):
    plan = pilot_plan(feed)
    key = pilot.validate_plan(plan)[0]["request_hash"]
    ledger = {
        "plan_hash": pipeline.digest(plan),
        "budget_usd": 0.05,
        "attempts": [
            {
                "request_hash": key,
                "status": "failed_or_unknown",
                "http_status": 402,
            }
        ],
    }
    (tmp_path / "ledger.json").write_text(json.dumps(ledger))
    monkeypatch.setattr(pilot, "BUDGET_USD", pilot.RESERVATION_USD)
    with pytest.raises(RuntimeError, match="cost limit"):
        pilot.run(plan, tmp_path, None, recover_request=key)
    assert len(json.loads((tmp_path / "ledger.json").read_text())["attempts"]) == 1


def test_pilot_recovery_rejects_unknown_outcome():
    with pytest.raises(ValueError, match="recorded HTTP 402"):
        pilot.authorize_recovery(
            {
                "attempts": [
                    {
                        "request_hash": "x",
                        "status": "failed_or_unknown",
                    }
                ]
            },
            "x",
        )
