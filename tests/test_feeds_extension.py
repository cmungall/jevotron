import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from jevotron.extensions.feeds import cli, storage
from jevotron.extensions.feeds.adapters import bluesky as bluesky_adapter
from jevotron.extensions.feeds.bluesky import PublicClient, normalize
from jevotron.extensions.feeds.common import digest
from jevotron.extensions.feeds.preset import make_preview

NOW = "2026-09-24T00:00:00+00:00"
FIXTURES = Path(__file__).parents[1] / "examples/classified_feeds/fixtures"


@pytest.fixture
def records():
    feed = json.loads((FIXTURES / "bluesky.json").read_text())["feed"]
    result = [normalize(item, NOW, {}) for item in feed]
    for record in result:
        record["source"] = "synthetic-bluesky"
    return result


@pytest.fixture
def file(tmp_path, records):
    path = tmp_path / "records.jsonl"
    cli.write_jsonl(records, path)
    return path


@pytest.fixture
def client(monkeypatch):
    class Client:
        calls = 0

        def evaluate(self, request):
            self.calls += 1
            return {
                "model": request["model"],
                "usage": {},
                "answers": {
                    k: {
                        "type": "choice",
                        "choice": next(iter(q["criteria"])),
                        "confidence": 0.8,
                        "probabilities": {
                            label: float(i == 0)
                            for i, label in enumerate(q["criteria"])
                        },
                    }
                    for k, q in request["questions"].items()
                },
            }

        def close(self):
            pass

    instance = Client()
    monkeypatch.setattr("jevotron.classification.JevClient", lambda: instance)
    return instance


def test_core_cli_does_not_import_extension():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import jevotron.cli; assert not any(m.startswith('jevotron.extensions') for m in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_presets_and_preview_are_offline(file, client, capsys):
    assert cli.main(["presets"]) == 0
    assert "research-feed" in capsys.readouterr().out
    assert cli.main(["preview", str(file), "--limit", "1"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert set(preview["request"]["questions"]) == {
        "topic",
        "relevance",
        "content_kind",
        "actionability",
    }
    assert client.calls == 0


def test_classify_requires_execute_then_cache_replays_without_calls(
    file, tmp_path, client
):
    output, cache = tmp_path / "answers.jsonl", tmp_path / "cache.sqlite3"
    output.write_text("previous output\n")
    args = [
        "classify",
        str(file),
        "--limit",
        "1",
        "--cache",
        str(cache),
        "-o",
        str(output),
    ]
    assert cli.main(args) == 1
    assert client.calls == 0 and output.read_text() == "previous output\n"
    assert cli.main([*args, "--execute"]) == 0
    assert client.calls == 1
    assert cli.main(args) == 0
    assert client.calls == 1
    assert json.loads(output.read_text())["cached"]
    assert cli.main([*args, "--refresh"]) == 1
    assert client.calls == 1


def test_digest_keeps_unassessed_and_rejects_stale_results(file, tmp_path, client):
    answers, cache = tmp_path / "answers.jsonl", tmp_path / "cache.sqlite3"
    assert (
        cli.main(
            [
                "classify",
                str(file),
                "--limit",
                "1",
                "--execute",
                "--cache",
                str(cache),
                "-o",
                str(answers),
            ]
        )
        == 0
    )
    folder = tmp_path / "digest"
    args = [
        "digest",
        str(file),
        "--assessments",
        str(answers),
        "-o",
        str(folder),
        "--as-of",
        NOW,
    ]
    assert cli.main(args) == 0
    queue = storage.read_jsonl(folder / "queue.jsonl")
    assert len(queue) == 3
    assert sum(r["classification_status"] == "unassessed" for r in queue) == 2
    assert all(r["review"] for r in queue if r["classification_status"] == "unassessed")
    assert "<script>" not in (folder / "digest.html").read_text()
    saved = (folder / "queue.jsonl").read_bytes()
    assert cli.main([*args, "--interests", "changed reader"]) == 1
    records = storage.load_records(file)
    records[0]["version"] = "changed-version"
    cli.write_jsonl(records, file)
    assert cli.main(args) == 1
    assert (folder / "queue.jsonl").read_bytes() == saved
    assert client.calls == 1


def test_tampered_assessment_and_unavailable_record_are_rejected(
    file, tmp_path, client
):
    answers = tmp_path / "answers.jsonl"
    assert (
        cli.main(
            [
                "classify",
                str(file),
                "--execute",
                "--limit",
                "1",
                "--cache",
                str(tmp_path / "c.db"),
                "-o",
                str(answers),
            ]
        )
        == 0
    )
    rows = storage.read_jsonl(answers)
    rows[0]["answers"]["topic"]["confidence"] = -1
    cli.write_jsonl(rows, answers)
    with pytest.raises(RuntimeError):
        cli.bound_assessments(storage.load_records(file), answers, cli.INTERESTS)
    rows[0]["answers"]["topic"]["confidence"] = 0.8
    cli.write_jsonl(rows, answers)
    records = storage.load_records(file)
    records[0] = {
        "id": records[0]["id"],
        "source": "synthetic-bluesky",
        "status": "unavailable",
    }
    with pytest.raises(ValueError, match="unavailable"):
        cli.bound_assessments(records, answers, cli.INTERESTS)


def test_output_and_cache_cannot_overwrite_input(file, tmp_path, client):
    before = file.read_bytes()
    assert cli.main(["preview", str(file), "-o", str(file)]) == 1
    assert cli.main(["classify", str(file), "--execute", "--cache", str(file)]) == 1
    folder = tmp_path / "collision"
    folder.mkdir()
    assert cli.main(["classify", str(file), "--execute", "--output", str(folder)]) == 1
    hardlink = folder / "queue.jsonl"
    hardlink.hardlink_to(file)
    assert cli.main(["digest", str(file), "-o", str(folder)]) == 1
    assert file.read_bytes() == before and client.calls == 0


def test_input_bounds_duplicate_ids_and_mail_rejection(file, records, monkeypatch):
    cli.write_jsonl([records[0], records[0]], file)
    with pytest.raises(ValueError, match="Duplicate"):
        storage.load_records(file)
    records[0]["source"] = "synthetic-mail"
    cli.write_jsonl(records, file)
    assert cli.main(["preview", str(file)]) == 1
    monkeypatch.setattr(storage, "MAX_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="16 MB"):
        storage.load_records(file)


def test_collection_recheck_and_failures_preserve_checkpoint(tmp_path, monkeypatch):
    feed = json.loads((FIXTURES / "bluesky.json").read_text())["feed"]
    status = 200

    def handler(request):
        assert request.url.host == "public.api.bsky.app" and request.method == "GET"
        assert "authorization" not in request.headers
        if request.url.path.endswith("getPosts"):
            return httpx.Response(200, json={"posts": []})
        return httpx.Response(status, json={"feed": [feed[0]], "cursor": "next"})

    monkeypatch.setattr(
        bluesky_adapter,
        "PublicClient",
        lambda: PublicClient(httpx.MockTransport(handler)),
    )
    args = [
        "collect",
        "--actor",
        "example.invalid",
        "--root",
        str(tmp_path),
        "--limit",
        "1",
    ]
    assert cli.main(args) == 0
    folder = (
        tmp_path / "public" / digest({"actor": "example.invalid", "query": None})[:16]
    )
    checkpoint = (folder / "state.json").read_bytes()
    status = 429
    assert cli.main([*args, "--resume"]) == 1
    assert (folder / "state.json").read_bytes() == checkpoint
    assert (
        cli.main(["recheck", "--actor", "example.invalid", "--root", str(tmp_path)])
        == 0
    )
    assert storage.load_records(folder / "records.jsonl")[0]["status"] == "unavailable"
    assert (folder / "preview.jsonl").read_text() == ""


def test_preset_excludes_observation_but_versions_context(records):
    record = records[1]
    before = make_preview(record)
    record["observed_at"] = "2027-01-01T00:00:00Z"
    assert make_preview(record)["request_hash"] == before["request_hash"]
    record["context"]["parent"]["status"] = "unavailable"
    assert make_preview(record)["request_hash"] != before["request_hash"]


def test_local_workflow_never_imports_bluesky_or_constructs_inference_client(tmp_path):
    fixture = FIXTURES.parent / "research-items.jsonl"
    script = """
import importlib.abc
import sys
class BlockBluesky(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'jevotron.extensions.feeds.bluesky',
                        'jevotron.extensions.feeds.adapters.bluesky'}:
            raise AssertionError('Unselected provider imported')
sys.meta_path.insert(0, BlockBluesky())
from jevotron.extensions.feeds import cli
def forbidden(*args, **kwargs):
    raise AssertionError('Inference client constructed')
import jevotron.classification
jevotron.classification.JevClient = forbidden
assert cli.main(['adapters']) == 0
assert cli.main(['preview', sys.argv[1]]) == 0
assert cli.main(['digest', sys.argv[1], '-o', sys.argv[2]]) == 0
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(fixture), str(tmp_path / "digest")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert len(storage.read_jsonl(tmp_path / "digest/queue.jsonl")) == 2


def test_mixed_sources_classify_cache_and_digest(file, records, tmp_path, client):
    imported = storage.load_records(FIXTURES.parent / "research-items.jsonl")
    # Public-import has the same envelope; these contents remain synthetic.
    imported[1]["source"] = "public-import"
    rows = records + imported
    cli.write_jsonl(rows, file)
    answers, cache = tmp_path / "answers.jsonl", tmp_path / "cache.db"
    args = ["classify", str(file), "--cache", str(cache), "-o", str(answers)]
    assert cli.main([*args, "--execute"]) == 0
    assert client.calls == 5
    assert cli.main(args) == 0
    assert client.calls == 5
    folder = tmp_path / "digest"
    digest_args = [
        "digest",
        str(file),
        "--assessments",
        str(answers),
        "-o",
        str(folder),
    ]
    assert cli.main(digest_args) == 0
    assert len(storage.read_jsonl(folder / "queue.jsonl")) == 5
    assert all(
        r["classification_status"] == "decoded"
        for r in storage.read_jsonl(folder / "queue.jsonl")
    )
    saved = answers.read_bytes()
    results = storage.read_jsonl(answers)
    results[-1]["source"] = "synthetic-feed"
    cli.write_jsonl(results, answers)
    assert cli.main(digest_args) == 1
    answers.write_bytes(saved)
    rows[-1]["version"] = "synthetic-v2"
    cli.write_jsonl(rows, file)
    assert cli.main(args) == 1  # New content cannot silently reuse the old cache.
    assert cli.main(digest_args) == 1
    assert client.calls == 5


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", ""),
        ("version", None),
        ("author", " "),
        ("created_at", "2026-09-24"),
        ("context", []),
        ("source", "synthetic-mail"),
        ("source", "untrusted.module.path"),
    ],
)
def test_local_envelope_rejects_invalid_records(field, value):
    record = storage.load_records(FIXTURES.parent / "research-items.jsonl")[0]
    record[field] = value
    with pytest.raises(ValueError):
        storage.validate_record(record)


def test_bluesky_uri_validation_remains_adapter_specific(records):
    record = {**records[0], "id": "urn:synthetic:paper:1"}
    with pytest.raises(ValueError, match="URI"):
        storage.validate_record(record)
    record["source"] = "synthetic-feed"
    storage.validate_record(record)


def test_local_tombstones_suppress_content_and_reject_previous_assessment(
    tmp_path, client
):
    records = storage.load_records(FIXTURES.parent / "research-items.jsonl")
    path, answers = tmp_path / "items.jsonl", tmp_path / "answers.jsonl"
    cli.write_jsonl(records, path)
    assert (
        cli.main(
            [
                "classify",
                str(path),
                "--execute",
                "--cache",
                str(tmp_path / "c.db"),
                "-o",
                str(answers),
            ]
        )
        == 0
    )
    tombstone = {k: records[0][k] for k in ("id", "source")}
    tombstone["status"] = "unavailable"
    with pytest.raises(ValueError, match="content-free"):
        storage.validate_record({**tombstone, "text": "removed"})
    cli.write_jsonl([tombstone, records[1]], path)
    assert (
        cli.main(
            [
                "digest",
                str(path),
                "--assessments",
                str(answers),
                "-o",
                str(tmp_path / "stale"),
            ]
        )
        == 1
    )
    folder = tmp_path / "digest"
    assert cli.main(["digest", str(path), "-o", str(folder)]) == 0
    assert [r["record"]["id"] for r in storage.read_jsonl(folder / "queue.jsonl")] == [
        records[1]["id"]
    ]
