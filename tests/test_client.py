import json

import httpx
import pytest

from jevotron.client import ENDPOINT, JevClient, JevError


def test_http_request_contract_and_credentials():
    payload = {"model": "jev-test", "state": {"x": 1}, "questions": {}}

    def handler(request):
        assert str(request.url) == ENDPOINT
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert json.loads(request.content) == payload
        return httpx.Response(200, json={"answers": {}})

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        assert client.evaluate(payload) == {"answers": {}}


@pytest.mark.parametrize("status", [401, 403, 422])
def test_permanent_failures_stop_without_retry_or_leaking_body(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="sensitive-service-body")

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(JevError) as error:
            client.evaluate({})
    assert len(calls) == 1
    assert "sensitive" not in str(error.value) and "test-only-key" not in str(
        error.value
    )
    assert str(status) in str(error.value)


@pytest.mark.parametrize("status", [429, 500, 529])
def test_transient_status_retried_with_backoff(monkeypatch, status):
    delays = []
    monkeypatch.setattr("jevotron.client.time.sleep", delays.append)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status if len(calls) < 3 else 200,
            json={"answers": {}},
            headers={"Retry-After": "3"},
        )

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        client.evaluate({})
    assert delays == [3, 3] and len(calls) == 3


def test_network_failure_bounded_and_sanitized(monkeypatch):
    monkeypatch.setattr("jevotron.client.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("sensitive details", request=request)

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(JevError, match="after 3 attempts") as error:
            client.evaluate({})
    assert len(calls) == 3 and "sensitive" not in str(error.value)


def test_missing_credentials_fail_clearly():
    with pytest.raises(JevError, match="TYPESAFE_API_KEY"):
        JevClient()
