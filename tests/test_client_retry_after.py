from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest

from jevotron.client import JevClient, JevError

START = datetime(2026, 9, 23, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def http_date(timestamp):
    return format_datetime(datetime.fromtimestamp(timestamp, timezone.utc), usegmt=True)


@pytest.fixture
def clock(monkeypatch):
    state = {"now": START, "delays": []}

    def sleep(delay):
        state["delays"].append(delay)
        state["now"] += delay

    monkeypatch.setattr("jevotron.client.time.time", lambda: state["now"])
    monkeypatch.setattr("jevotron.client.time.sleep", sleep)
    return state


@pytest.mark.parametrize("status", [429, 503, 529])
@pytest.mark.parametrize(
    "header",
    [
        "Wed, 23 Sep 2026 12:00:10 GMT",
        "Wednesday, 23-Sep-26 12:00:10 GMT",
        "Wed Sep 23 12:00:10 2026",
    ],
)
def test_date_retry_waits_until_service_recovers(clock, status, header):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status if clock["now"] < START + 10 else 200,
            headers={"Retry-After": header},
            json={"answers": {}},
        )

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        assert client.evaluate({}) == {"answers": {}}
    assert clock["delays"] == [10]
    assert len(calls) == 2


def test_date_delay_is_capped_and_recomputed_after_each_response(clock):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429 if clock["now"] < START + 50 else 200,
            headers={"Retry-After": http_date(START + 50)},
            json={"answers": {}},
        )

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        assert client.evaluate({}) == {"answers": {}}
    assert clock["delays"] == [30, 20]
    assert len(calls) == 3


@pytest.mark.parametrize(
    "header,delays",
    [
        (http_date(START + 120), [30, 30]),
        (http_date(START - 10), [1, 2]),
        ("not a date", [1, 2]),
        ("Wed, 31 Feb 2026 12:00:10 GMT", [1, 2]),
        ("nan", [1, 2]),
        ("inf", [1, 2]),
        ("3", [3, 3]),
        ("120", [30, 30]),
        ("0", [1, 2]),
        ("-5", [1, 2]),
        ("1.5", [1.5, 2]),
        (None, [1, 2]),
    ],
)
def test_retry_after_preserves_backoff_and_attempt_bounds(clock, header, delays):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            429,
            headers={"Retry-After": header} if header is not None else {},
        )

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(JevError, match="HTTP 429"):
            client.evaluate({})
    assert clock["delays"] == delays
    assert len(calls) == 3
