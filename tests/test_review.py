"""Review the real captured inventory output and exercise the feedback loop offline."""

import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from jevotron import Chunk, Config, scan
from jevotron.cli import app
from jevotron.config import load_config
from jevotron.models import ENTRY_UNSET, json_text
from jevotron.review import ReviewStore, read_report

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs/assets/results/inventory.jsonl"
SOURCE = ROOT / "examples/inventory/items.yaml"
CONFIG = ROOT / "examples/inventory/jev_config.py"


def assessment(fake, *, value="BAD", guidance="first", context=None):
    row = next(
        scan(
            [
                Chunk(
                    "stable-id",
                    {"value": value, "description": "Full original context"},
                    fields=["/value"],
                )
            ],
            Config(guidance=guidance),
            cache=None,
            client=fake,
        )
    ).to_dict()
    if context is not None:
        row["context"] = context
    return row


def invoke(*args, input=None):
    return CliRunner().invoke(app, ["review", *map(str, args)], input=input)


def test_inventory_decisions_survive_process_restarts_and_export(tmp_path):
    store = tmp_path / "reviews.sqlite3"
    original_report, original_source = REPORT.read_bytes(), SOURCE.read_bytes()

    def command(*args):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "jevotron",
                "review",
                *map(str, args),
                "--store",
                str(store),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result

    imported = command("import", REPORT, "--source", SOURCE, "--config", CONFIG)
    assert "Imported 9 assessed fields" in imported.stderr
    result = command(
        "decide",
        "cable",
        "/quantity",
        "confirmed-error",
        "--note",
        "Count verified against stock",
    )
    decision = json.loads(result.stdout)
    assert decision["entry"]["description"] == "USB-C charging cable"
    assert decision["value"] == -30
    assert decision["model"] == "jev-1.13.0"
    assert decision["request_hash"] and decision["assessed_at"]
    assert json.loads(command("export").stdout) == decision
    command("import", REPORT, "--source", SOURCE, "--config", CONFIG)
    queue = [
        json.loads(line)
        for line in command(
            "list", "--unreviewed", "--warnings-only"
        ).stdout.splitlines()
    ]
    assert (
        len(queue) == 5
    )  # All fields of the two warning entries, minus reviewed quantity.
    assert all(item["entry_id"] in {"cable", "mug"} for item in queue)
    result = command("exemplars", "--decision", decision["decision_id"])
    exemplars = json.loads(result.stdout)
    assert exemplars == [
        {"entry": decision["entry"], "assessment": {"/quantity": "ANOMALY"}}
    ]
    selected = tmp_path / "selected.json"
    selected.write_text(result.stdout)
    preview = CliRunner().invoke(
        app,
        [
            "preview",
            str(SOURCE),
            "--config",
            str(CONFIG),
            "--exemplars",
            str(selected),
            "--limit",
            "1",
        ],
    )
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.stdout)["request"]["state"]["exemplars"] == exemplars
    assert (
        REPORT.read_bytes() == original_report
        and SOURCE.read_bytes() == original_source
    )


def test_legacy_report_requires_verified_full_context(tmp_path):
    store = tmp_path / "reviews.db"
    result = invoke("import", REPORT, "--store", store)
    assert (
        result.exit_code == 1
        and "selected fields alone are insufficient" in result.output
    )
    assert not store.exists()
    wrong_config = tmp_path / "config.py"
    wrong_config.write_text(
        "from jevotron import Config\nconfig=Config(guidance='different guidance')\n"
    )
    result = invoke(
        "import", REPORT, "--source", SOURCE, "--config", wrong_config, "--store", store
    )
    assert result.exit_code == 1 and "matching request" in result.output
    assert not store.exists()


def test_legacy_context_from_cache_never_changes_model_probabilities(tmp_path, fake):
    cache = tmp_path / "cache.sqlite3"
    config = load_config(CONFIG)
    list(scan(config.parser(SOURCE), config, cache=cache, client=fake))
    connection = sqlite3.connect(cache)
    before = connection.execute(
        "SELECT * FROM assessments ORDER BY request_hash"
    ).fetchall()
    connection.close()
    store = tmp_path / "reviews.db"
    result = invoke("import", REPORT, "--cache", cache, "--store", store)
    assert result.exit_code == 0, result.output
    assert (
        invoke(
            "decide", "cable", "/quantity", "valid-exception", "--store", store
        ).exit_code
        == 0
    )
    connection = sqlite3.connect(cache)
    assert (
        connection.execute("SELECT * FROM assessments ORDER BY request_hash").fetchall()
        == before
    )
    connection.close()
    with ReviewStore(store) as queue:
        decision = queue.decisions()[0]
        assert decision["decision"] == "valid-exception"
        assert decision["assessment"]["probabilities"]["ANOMALY"] == 1.0


def test_changed_content_and_guidance_require_reconsideration(tmp_path, fake):
    store = tmp_path / "reviews.db"
    first = assessment(fake)
    with ReviewStore(store) as queue:
        queue.import_results([first])
        original = queue.decide(
            "stable-id", "/value", "valid-exception", note="Exception verified"
        )
    with ReviewStore(store) as queue:
        queue.import_results([first])
        assert not queue.items(unreviewed=True)
        for changed in (
            assessment(fake, guidance="second"),
            assessment(fake, value="fixed"),
        ):
            queue.import_results([changed])
            current = queue.items()[0]
            assert current["status"] == "reconsider" and current["review"] is None
            assert current["request_hash"] != first["request_hash"]
        assert queue.decisions() == [original]
        assert len(queue.items(history=True)) == 3
        with pytest.raises(ValueError, match="changed assessment"):
            queue.exemplars([original["decision_id"]])
        with pytest.raises(ValueError, match="Assessment changed"):
            queue.decide(
                "stable-id",
                "/value",
                "confirmed-error",
                expected_hash=first["request_hash"],
            )
        assert queue.decisions() == [original]


def test_refresh_keeps_review_but_preserves_original_decision_provenance(
    tmp_path, fake
):
    first = assessment(fake)
    with ReviewStore(tmp_path / "reviews.db") as queue:
        queue.import_results([first])
        decision = queue.decide("stable-id", "/value", "confirmed-error")
        refreshed = copy.deepcopy(first)
        refreshed.update(
            model="new-resolved-alias", assessed_at="2027-01-01T00:00:00+00:00"
        )
        queue.import_results([refreshed])
        item = queue.items()[0]
        assert item["status"] == "confirmed-error"
        assert item["model"] == "new-resolved-alias"
        assert item["review"] == decision
        assert (
            decision["model"] == first["model"]
            and decision["assessed_at"] == first["assessed_at"]
        )


def test_explicit_exemplar_selection_rejects_conflicts_and_deferred(tmp_path, fake):
    with ReviewStore(tmp_path / "reviews.db") as queue:
        queue.import_results([assessment(fake)])
        error = queue.decide("stable-id", "/value", "confirmed-error")
        valid = queue.decide("stable-id", "/value", "valid-exception")
        deferred = queue.decide("stable-id", "/value", "deferred")
        assert queue.items(unreviewed=True)[0]["status"] == "deferred"
        assert queue.exemplars([error["decision_id"]])[0]["assessment"] == {
            "/value": "ANOMALY"
        }
        assert queue.exemplars([valid["decision_id"]])[0]["assessment"] == {
            "/value": "NORMAL"
        }
        with pytest.raises(ValueError, match="Contradictory selections"):
            queue.exemplars([error["decision_id"], valid["decision_id"]])
        for ids, message in [
            ([], "explicitly"),
            ([deferred["decision_id"]], "deferred"),
            ([999], "Unknown decision"),
        ]:
            with pytest.raises(ValueError, match=message):
                queue.exemplars(ids)
        with pytest.raises(ValueError, match="configured labels"):
            queue.exemplars([error["decision_id"]], anomaly_label="FAIL")


def test_interactive_review_shows_full_entry_context_and_persists_notes(tmp_path, fake):
    context = {"rules": [{"id": "source-1", "allowed": True}]}
    store = tmp_path / "reviews.db"
    row = assessment(fake, context=context)
    with ReviewStore(store) as queue:
        queue.import_results([row])
    result = invoke(
        "start",
        "--store",
        store,
        "--unreviewed",
        "--warnings-only",
        input="x\nc\nChecked manually\n",
    )
    assert result.exit_code == 0, result.output
    assert "Full original context" in result.output and "source-1" in result.output
    assert "Field assessment" in result.output and row["request_hash"] in result.output
    assert "Choose c, v, d, s, or q" in result.output
    with ReviewStore(store) as queue:
        (decision,) = queue.decisions()
        assert (
            decision["decision"] == "confirmed-error"
            and decision["note"] == "Checked manually"
        )
        assert decision["context"] == context
    assert invoke("start", "--store", store, "--unreviewed").exit_code == 0


@pytest.mark.parametrize("answer", ["s\n", "q\n"])
def test_interactive_skip_or_quit_does_not_record_decision(tmp_path, fake, answer):
    store = tmp_path / "reviews.db"
    with ReviewStore(store) as queue:
        queue.import_results([assessment(fake)])
    result = invoke("start", "--store", store, input=answer)
    assert result.exit_code == 0, result.output
    with ReviewStore(store) as queue:
        assert not queue.decisions()


def test_reports_include_full_entry_and_preserve_scalar_null(tmp_path, fake):
    row = assessment(fake)
    assert row["entry"]["description"] == "Full original context"
    result = next(scan([Chunk("null", None)], cache=None, client=fake))
    assert result.to_dict()["entry"] is None
    path = tmp_path / "null.jsonl"
    path.write_text(json_text(result.to_dict()))
    assert read_report(path)[0]["entry"] is None
    result.entry = ENTRY_UNSET
    assert "entry" not in copy.deepcopy(result).to_dict()
    path.write_text(json_text(result.to_dict()))
    assert (
        read_report(path)[0]["entry"] is None
    )  # A whole-entry field is complete context.


def test_invalid_import_is_atomic_and_same_hash_cannot_hide_changed_context(
    tmp_path, fake
):
    first = assessment(fake)
    with ReviewStore(tmp_path / "reviews.db") as queue:
        queue.import_results([first])
        queue.decide("stable-id", "/value", "valid-exception")
        previous = queue.items(history=True)
        changed = copy.deepcopy(first)
        changed["entry"]["description"] = "Different hidden context"
        with pytest.raises(ValueError, match="Same request hash"):
            queue.import_results([changed])
        changed["fields"][0]["value"] = "different from entry"
        with pytest.raises(ValueError, match="disagrees with original field"):
            queue.import_results([changed])
        with pytest.raises(ValueError, match="repeated entry/field"):
            queue.import_results([first, first])
        assert queue.items(history=True) == previous


@pytest.mark.parametrize("target", ["report", "source", "cache"])
def test_review_store_cannot_overwrite_protected_aliases(tmp_path, target):
    source = tmp_path / "data.yaml"
    source.write_bytes(SOURCE.read_bytes())
    report = tmp_path / "report.jsonl"
    report.write_bytes(REPORT.read_bytes())
    cache = tmp_path / "cache.db"
    cache.write_bytes(b"existing cache contents")
    protected = {"source": source, "report": report, "cache": cache}[target]
    original = protected.read_bytes()
    store = tmp_path / "reviews.db"
    store.hardlink_to(protected)
    result = invoke(
        "import",
        report,
        "--store",
        store,
        "--source",
        source,
        "--config",
        CONFIG,
        "--cache",
        cache,
    )
    assert result.exit_code == 1 and "separate" in result.output
    assert protected.read_bytes() == original


def test_existing_input_database_cannot_be_used_as_review_store(tmp_path):
    source = tmp_path / "data.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE items (id TEXT)")
    connection.commit()
    connection.close()
    original = source.read_bytes()
    result = invoke("list", "--store", source)
    assert result.exit_code == 1 and "separate review store" in result.output
    assert source.read_bytes() == original


def test_legacy_cache_context_recovery_checks_request_hash(tmp_path, fake):
    from jevotron.cache import Cache
    from jevotron.runner import make_request, request_hash

    report = assessment(fake)
    request, _ = make_request(
        Chunk(report["id"], report["entry"], fields=["/value"]),
        Config(guidance="first"),
    )
    context = {"neighbors": [{"id": "ref-1", "relationship": "part_of"}]}
    request["state"]["context"] = context
    report["request_hash"] = request_hash(request)
    del report["entry"]
    path = tmp_path / "legacy.jsonl"
    path.write_text(json_text(report))
    cache = tmp_path / "cache.db"
    with Cache(cache) as saved:
        saved.put(
            report["request_hash"],
            request,
            fake.evaluate(request),
            report["assessed_at"],
        )
    recovered = read_report(path, cache=cache)
    assert recovered[0]["context"]["data"] == context
    assert recovered[0]["context"]["provenance"] is None
    assert "unavailable" in recovered[0]["context"]["provenance_status"]
    with ReviewStore(tmp_path / "review.db") as queue:
        queue.import_results(recovered)
        decision = queue.decide(report["id"], "/value", "confirmed-error")
        assert decision["context"] == recovered[0]["context"]
    with Cache(cache) as saved:
        request["state"]["guidance"] = "tampered"
        saved.put(
            report["request_hash"],
            request,
            fake.evaluate(request),
            report["assessed_at"],
        )
    with pytest.raises(ValueError, match="does not match its request hash"):
        read_report(path, cache=cache)


def test_downloadable_inventory_review_example_runs_offline(tmp_path):
    import os
    import shutil

    if shutil.which("jq") is None:
        pytest.skip("The shell example documents jq as a dependency")
    environment = os.environ.copy()
    environment["PATH"] = (
        str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
    )
    result = subprocess.run(
        ["bash", str(ROOT / "examples/review/inventory.sh"), str(tmp_path)],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    exemplars = json.loads((tmp_path / "exemplars.json").read_text())
    assert len(exemplars) == 2
    assert exemplars[0]["assessment"] == {"/quantity": "ANOMALY"}
    assert exemplars[1]["assessment"] == {"/category": "ANOMALY"}
    assert (
        json.loads((tmp_path / "preview.jsonl").read_text())["request"]["state"][
            "exemplars"
        ]
        == exemplars
    )


def test_default_model_cache_path_is_never_initialized_for_review(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    path = Path(".jevotron/cache.sqlite3")
    result = invoke("list", "--store", path)
    assert result.exit_code == 1 and "separate" in result.output
    assert not path.exists()


def test_existing_empty_database_is_not_initialized_for_review(tmp_path):
    path = tmp_path / "empty.db"
    connection = sqlite3.connect(path)
    connection.execute("VACUUM")
    connection.close()
    original = path.read_bytes()
    assert original
    result = invoke("list", "--store", path)
    assert result.exit_code == 1 and "separate review store" in result.output
    assert path.read_bytes() == original


def reference_context(limit=4, location="nodes.json:entry:1"):
    return {
        "data": [
            {
                "name": "rules",
                "kind": "graph",
                "nodes": [{"id": "rule", "data": {"limit": limit}}],
                "edges": [],
            }
        ],
        "provenance": [
            {
                "name": "rules",
                "files": ["nodes.json"],
                "nodes": [{"id": "rule", "source": location}],
            }
        ],
    }


def test_reference_source_and_cache_recovery_preserve_envelope(
    tmp_path, fake, monkeypatch
):
    from jevotron.cache import Cache
    from jevotron.runner import make_request, request_hash

    row = assessment(fake, context=reference_context())
    request, _ = make_request(
        Chunk(row["id"], row["entry"], fields=["/value"]), Config(guidance="first")
    )
    request["state"]["context"] = row["context"]["data"]
    row["request_hash"] = request_hash(request)
    # A current preview may have different line locations without changing input data.
    preview_item = {
        "request_hash": row["request_hash"],
        "request": request,
        "context": reference_context(location="nodes.json:entry:2"),
    }
    monkeypatch.setattr("jevotron.review.preview", lambda *args: iter([preview_item]))
    source = tmp_path / "data.csv"
    source.write_text("value\nBAD\n")
    path = tmp_path / "results.jsonl"
    path.write_text(json_text(row))
    assert read_report(path, source=source)[0]["context"] == row["context"]
    cache = tmp_path / "cache.db"
    with Cache(cache) as saved:
        saved.put(
            row["request_hash"], request, fake.evaluate(request), row["assessed_at"]
        )
    assert read_report(path, cache=cache)[0]["context"] == row["context"]
    legacy = {
        key: value for key, value in row.items() if key not in ("entry", "context")
    }
    path.write_text(json_text(legacy))
    assert read_report(path, source=source)[0]["context"] == preview_item["context"]
    from_cache = read_report(path, cache=cache)[0]["context"]
    assert (
        from_cache["data"] == row["context"]["data"]
        and from_cache["provenance"] is None
    )
    assert "unavailable" in from_cache["provenance_status"]


def test_provenance_changes_keep_reviews_and_original_decision_snapshot(tmp_path, fake):
    first = assessment(fake, context=reference_context())
    with ReviewStore(tmp_path / "reviews.db") as queue:
        queue.import_results([first])
        decision = queue.decide("stable-id", "/value", "confirmed-error")
        moved = copy.deepcopy(first)
        moved["context"] = reference_context(location="nodes.json:entry:2")
        queue.import_results([moved])
        item = queue.items()[0]
        assert item["status"] == "confirmed-error"
        assert item["context"] == moved["context"]
        assert item["review"]["context"] == first["context"]
        assert queue.decisions() == [decision]


def test_exemplars_keep_context_data_and_group_by_evidence_not_provenance(
    tmp_path, fake
):
    first = assessment(fake, context=reference_context())
    second = copy.deepcopy(first)
    second["id"] = "different-reporting-id"
    second["context"] = reference_context(limit=10)
    # The model-visible reference change results in a distinct request identity.
    second["request_hash"] = "a" * 64
    same_evidence = copy.deepcopy(first)
    same_evidence["id"] = "same-evidence-different-location"
    same_evidence["context"] = reference_context(location="nodes.json:entry:2")
    with ReviewStore(tmp_path / "reviews.db") as queue:
        queue.import_results([first, second, same_evidence])
        error = queue.decide(first["id"], "/value", "confirmed-error")
        valid = queue.decide(second["id"], "/value", "valid-exception")
        same = queue.decide(same_evidence["id"], "/value", "confirmed-error")
        exemplars = queue.exemplars(
            [error["decision_id"], valid["decision_id"], same["decision_id"]]
        )
        assert len(exemplars) == 2
        assert exemplars[0]["context"] == first["context"]["data"]
        assert exemplars[1]["context"] == second["context"]["data"]
        assert exemplars[0]["assessment"] == {"/value": "ANOMALY"}
        assert exemplars[1]["assessment"] == {"/value": "NORMAL"}
        Config(exemplars=exemplars).validate()
        contradiction = queue.decide(same_evidence["id"], "/value", "valid-exception")
        with pytest.raises(ValueError, match="Contradictory selections"):
            queue.exemplars([error["decision_id"], contradiction["decision_id"]])
