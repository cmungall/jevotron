import json
import sqlite3

import httpx
import pytest

from jevotron.cli import main
from jevotron.client import JevClient, JevError


def malformed_response(status):
    return httpx.Response(
        status,
        headers={"Content-Encoding": "gzip"},
        stream=httpx.ByteStream(b"sensitive-invalid-gzip"),
    )


@pytest.mark.parametrize("status", [200, 503])
def test_decoding_failure_recovers_with_bounded_backoff(monkeypatch, status):
    delays = []
    calls = []
    monkeypatch.setattr("jevotron.client.time.sleep", delays.append)

    def handler(request):
        calls.append(request)
        if len(calls) < 3:
            return malformed_response(status)
        return httpx.Response(200, json={"answers": {}})

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        assert client.evaluate({}) == {"answers": {}}
    assert len(calls) == 3
    assert delays == [1, 2]


@pytest.mark.parametrize("status", [200, 503])
def test_decoding_failure_exhaustion_is_sanitized(monkeypatch, status):
    delays = []
    calls = []
    monkeypatch.setattr("jevotron.client.time.sleep", delays.append)

    def handler(request):
        calls.append(request)
        return malformed_response(status)

    with JevClient(
        api_key="test-only-key", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(JevError, match="after 3 attempts") as error:
            client.evaluate({})
    assert len(calls) == 3
    assert delays == [1, 2]
    assert "sensitive" not in str(error.value)
    assert "test-only-key" not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("status", [200, 503])
def test_cli_decoding_failure_retains_success_and_resumes(
    tmp_path, monkeypatch, capsys, fake, status
):
    data = tmp_path / "data.csv"
    data.write_text("value\nok\nBAD\n")
    cache = tmp_path / "cache.sqlite3"
    calls = []
    failing = True
    monkeypatch.setattr("jevotron.client.time.sleep", lambda _: None)

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if failing and payload["state"]["entry"]["value"] == "BAD":
            return malformed_response(status)
        return httpx.Response(200, json=fake.evaluate(payload))

    monkeypatch.setattr(
        "jevotron.runner.JevClient",
        lambda: JevClient(
            api_key="test-only-key", transport=httpx.MockTransport(handler)
        ),
    )
    args = ["scan", str(data), "--cache", str(cache)]
    assert main(args) == 1
    output = capsys.readouterr()
    assert len(output.out.splitlines()) == 1
    assert "Error: Jev request failed after 3 attempts" in output.err
    assert "INCOMPLETE: 1 entries assessed" in output.err
    assert "Traceback" not in output.err
    assert "sensitive" not in output.err and "test-only-key" not in output.err
    assert len(calls) == 4
    with sqlite3.connect(cache) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 1

    failing = False
    assert main(args) == 0
    output = capsys.readouterr()
    results = [json.loads(line) for line in output.out.splitlines()]
    assert [result["cached"] for result in results] == [True, False]
    assert len(calls) == 5
    with sqlite3.connect(cache) as db:
        assert db.execute("SELECT count(*) FROM assessments").fetchone()[0] == 2
