"""Offline field review with human decisions separate from model assessments."""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from jevotron.config import Config, load_config
from jevotron.models import json_text, resolve, validate_json
from jevotron.parsers import for_path
from jevotron.runner import preview, request_hash

DECISIONS = ("confirmed-error", "valid-exception", "deferred")
PENDING = {"unreviewed", "reconsider", "deferred"}


def _key(entry_id, path, request):
    return hashlib.sha256(json_text([entry_id, path, request]).encode()).hexdigest()


def protect_store(store: Path, *inputs: Path | None):
    for path in inputs:
        if path is not None and (
            store.resolve() == path.resolve()
            or (store.exists() and path.exists() and store.samefile(path))
        ):
            raise ValueError(
                "Review store must be separate from reports, source, config, and model cache"
            )


def read_report(report: Path, *, source=None, config=None, cache=None) -> list[dict]:
    """Recover legacy entry context only from matching model requests."""
    if config is not None and source is None:
        raise ValueError("--config requires --source")
    rows = []
    with report.open(encoding="utf-8") as stream:
        for line, text in enumerate(stream, 1):
            if text.strip():
                row = json.loads(text)
                if not isinstance(row, dict):
                    raise ValueError(f"{report}:{line}: Expected a JSONL scan result")
                rows.append(row)
    entries = {}
    contexts = {}
    if source is not None:
        settings = load_config(config) if config else Config()
        parser = settings.parser or for_path(source)
        chunks = iter(parser(source))
        try:
            for item in preview(chunks, settings):
                entries[item["request_hash"]] = item["request"]["state"]["entry"]
                if "context" in item["request"]["state"]:
                    contexts[item["request_hash"]] = item["request"]["state"]["context"]
        finally:
            if hasattr(chunks, "close"):
                chunks.close()
    if cache is not None:
        connection = sqlite3.connect(cache.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            for row in rows:
                saved = connection.execute(
                    "SELECT request_json FROM assessments WHERE request_hash = ?",
                    (row.get("request_hash"),),
                ).fetchone()
                if saved is not None:
                    request = json.loads(saved[0])
                    if request_hash(request) != row["request_hash"]:
                        raise ValueError(
                            "Cached request does not match its request hash"
                        )
                    entries[row["request_hash"]] = request["state"]["entry"]
                    if "context" in request["state"]:
                        contexts[row["request_hash"]] = request["state"]["context"]
        finally:
            connection.close()
    for row in rows:
        if "entry" not in row:
            key = row.get("request_hash")
            root = next(
                (
                    field
                    for field in row.get("fields", [])
                    if isinstance(field, dict) and field.get("path") == ""
                ),
                None,
            )
            if key in entries:
                row["entry"] = entries[key]
            elif root is not None and "value" in root:
                row["entry"] = root["value"]
            else:
                raise ValueError(
                    f"Result {row.get('id')!r} has no full entry context. Supply --source with the original --config, or --cache with a matching request; selected fields alone are insufficient."
                )
        elif row.get("request_hash") in entries and json_text(
            row["entry"]
        ) != json_text(entries[row["request_hash"]]):
            raise ValueError(
                f"Result {row.get('id')!r} disagrees with the matching source/cache entry"
            )
        key = row.get("request_hash")
        if key in contexts:
            if "context" in row and json_text(row["context"]) != json_text(
                contexts[key]
            ):
                raise ValueError(
                    f"Result {row.get('id')!r} disagrees with matching assessment context"
                )
            row["context"] = contexts[key]
    return rows


def _snapshots(rows):
    seen = set()
    for row in rows:
        validate_json(row)
        for name in ("id", "model", "assessed_at", "request_hash"):
            if not isinstance(row.get(name), str) or not row[name]:
                raise ValueError(f"Report requires a nonempty {name}")
        if not re.fullmatch(r"[0-9a-f]{64}", row["request_hash"]):
            raise ValueError("Report request_hash must be a SHA-256 hex digest")
        datetime.fromisoformat(row["assessed_at"])
        if not isinstance(row.get("warning"), bool):
            raise ValueError("Report warning must be a boolean")
        if (
            "entry" not in row
            or not isinstance(row.get("fields"), list)
            or not row["fields"]
        ):
            raise ValueError("Report requires full entry context and assessed fields")
        for field in row["fields"]:
            if (
                not isinstance(field, dict)
                or not isinstance(field.get("path"), str)
                or "value" not in field
            ):
                raise ValueError(
                    "Report field requires a JSON Pointer path and original value"
                )
            if (
                not isinstance(field.get("probabilities"), dict)
                or not field["probabilities"]
            ):
                raise ValueError(
                    "Report field requires its original model probabilities"
                )
            pair = (row["id"], field["path"])
            if pair in seen:
                raise ValueError(
                    f"Report contains repeated entry/field {pair!r}; import one assessment version at a time"
                )
            seen.add(pair)
            if json_text(resolve(row["entry"], field["path"])) != json_text(
                field["value"]
            ):
                raise ValueError(
                    f"Full entry disagrees with original field value: {pair!r}"
                )
            snapshot = {
                "entry_id": row["id"],
                "field_path": field["path"],
                "value": field["value"],
                "entry": row["entry"],
                "request_hash": row["request_hash"],
                "assessed_at": row["assessed_at"],
                "model": row["model"],
                "source": row.get("source"),
                "warning": row["warning"],
                "assessment": field,
            }
            if "context" in row:
                snapshot["context"] = row["context"]
            yield snapshot


class ReviewStore:
    """SQLite queue and append-only decision history; never writes the model cache."""

    def __init__(self, path: Path):
        protect_store(path, Path(".jevotron/cache.sqlite3"))
        existing_database = path.exists() and path.stat().st_size > 0
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        try:
            tables = {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            allowed = {
                "review_meta",
                "review_items",
                "review_decisions",
                "sqlite_sequence",
            }
            if (existing_database and "review_meta" not in tables) or tables - allowed:
                raise ValueError(
                    "Use a separate review store, not an input database or model cache"
                )
            with self.connection:
                self.connection.execute(
                    "CREATE TABLE IF NOT EXISTS review_meta (version INTEGER NOT NULL)"
                )
                version = self.connection.execute(
                    "SELECT version FROM review_meta"
                ).fetchall()
                if not version:
                    self.connection.execute("INSERT INTO review_meta VALUES (1)")
                elif version != [(1,)]:
                    raise ValueError("Unsupported review store version")
                self.connection.execute("""CREATE TABLE IF NOT EXISTS review_items (
                    key TEXT PRIMARY KEY, entry_id TEXT NOT NULL, field_path TEXT NOT NULL,
                    request_hash TEXT NOT NULL, snapshot TEXT NOT NULL, current INTEGER NOT NULL
                )""")
                self.connection.execute("""CREATE TABLE IF NOT EXISTS review_decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, item_key TEXT NOT NULL,
                    payload TEXT NOT NULL
                )""")
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def import_results(self, rows) -> int:
        snapshots = list(_snapshots(rows))
        with self.connection:
            for entry_id in {item["entry_id"] for item in snapshots}:
                self.connection.execute(
                    "UPDATE review_items SET current=0 WHERE entry_id=?", (entry_id,)
                )
            for item in snapshots:
                key = _key(item["entry_id"], item["field_path"], item["request_hash"])
                existing = self.connection.execute(
                    "SELECT snapshot FROM review_items WHERE key=?", (key,)
                ).fetchone()
                if existing:
                    previous = json.loads(existing[0])
                    if json_text(previous["entry"]) != json_text(
                        item["entry"]
                    ) or json_text(previous.get("context")) != json_text(
                        item.get("context")
                    ):
                        raise ValueError(
                            "Same request hash has different entry or context; import an authentic scan report"
                        )
                self.connection.execute(
                    "INSERT INTO review_items VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT(key) DO UPDATE SET snapshot=excluded.snapshot, current=1",
                    (
                        key,
                        item["entry_id"],
                        item["field_path"],
                        item["request_hash"],
                        json_text(item),
                    ),
                )
        return len(snapshots)

    def decisions(self):
        return [
            json.loads(row[0])
            for row in self.connection.execute(
                "SELECT payload FROM review_decisions ORDER BY id"
            )
        ]

    def items(self, *, unreviewed=False, warnings_only=False, history=False):
        decisions = self.decisions()
        latest = {item["item_key"]: item for item in decisions}
        reviewed = {(item["entry_id"], item["field_path"]) for item in decisions}
        result = []
        for key, snapshot, current in self.connection.execute(
            "SELECT key, snapshot, current FROM review_items ORDER BY entry_id, field_path, rowid"
        ):
            if not history and not current:
                continue
            item = json.loads(snapshot)
            decision = latest.get(key)
            status = (
                decision["decision"]
                if decision
                else (
                    "reconsider"
                    if (item["entry_id"], item["field_path"]) in reviewed
                    else "unreviewed"
                )
            )
            if unreviewed and status not in PENDING:
                continue
            if warnings_only and not item["warning"]:
                continue
            result.append(
                {
                    **item,
                    "item_key": key,
                    "current": bool(current),
                    "status": status,
                    "review": decision,
                }
            )
        return result

    def decide(self, entry_id, path, decision, *, note="", expected_hash=None):
        if decision not in DECISIONS:
            raise ValueError(f"Decision must be one of {', '.join(DECISIONS)}")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT key, snapshot FROM review_items WHERE entry_id=? AND field_path=? AND current=1",
                (entry_id, path),
            ).fetchone()
            if row is None:
                raise ValueError(
                    f"No current review item for {entry_id!r} field {path!r}"
                )
            key, raw = row
            item = json.loads(raw)
            if expected_hash is not None and expected_hash != item["request_hash"]:
                raise ValueError(
                    "Assessment changed; list the queue and reconsider the current request"
                )
            payload = {
                **item,
                "item_key": key,
                "decision": decision,
                "note": note,
                "reviewed_at": datetime.now(timezone.utc).isoformat(),
            }
            cursor = self.connection.execute(
                "INSERT INTO review_decisions(item_key,payload) VALUES (?, '{}')",
                (key,),
            )
            payload["decision_id"] = cursor.lastrowid
            self.connection.execute(
                "UPDATE review_decisions SET payload=? WHERE id=?",
                (json_text(payload), cursor.lastrowid),
            )
        return payload

    def exemplars(self, selected, *, normal_label="NORMAL", anomaly_label="ANOMALY"):
        if not selected:
            raise ValueError("Select at least one decision explicitly with --decision")
        if normal_label == anomaly_label:
            raise ValueError("Normal and anomaly labels must differ")
        decisions = {item["decision_id"]: item for item in self.decisions()}
        current = {
            row[0]
            for row in self.connection.execute(
                "SELECT key FROM review_items WHERE current=1"
            )
        }
        grouped = {}
        for ident in selected:
            if ident not in decisions:
                raise ValueError(f"Unknown decision {ident}")
            item = decisions[ident]
            if item["decision"] == "deferred":
                raise ValueError(
                    f"Decision {ident} is deferred; resolve it before selecting an exemplar"
                )
            if item["item_key"] not in current:
                raise ValueError(
                    f"Decision {ident} refers to a changed assessment; reconsider its current request before export"
                )
            label = (
                anomaly_label if item["decision"] == "confirmed-error" else normal_label
            )
            if label not in item["assessment"]["probabilities"]:
                raise ValueError(
                    f"Label {label!r} is absent from decision {ident}; set --normal-label and --anomaly-label to your configured labels"
                )
            entry_key = json_text(item["entry"])
            exemplar = grouped.setdefault(
                entry_key, {"entry": item["entry"], "assessment": {}}
            )
            labels = exemplar["assessment"]
            path = item["field_path"]
            if path in labels and labels[path] != label:
                raise ValueError(
                    f"Contradictory selections for field {path!r} of entry {item['entry_id']!r}: both {labels[path]} and {label}"
                )
            labels[path] = label
        return list(grouped.values())
