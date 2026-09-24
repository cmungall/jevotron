"""Content-addressed successful assessments, committed after each API call."""

import json
import sqlite3
from pathlib import Path
from typing import Any

from jevotron.models import json_text


class Cache:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS assessments (
                request_hash TEXT PRIMARY KEY,
                request_json TEXT NOT NULL,
                response_json TEXT NOT NULL,
                assessed_at TEXT NOT NULL
            )
        """)
        self.connection.commit()

    def get(self, key: str) -> tuple[dict[str, Any], str] | None:
        row = self.connection.execute(
            "SELECT response_json, assessed_at FROM assessments WHERE request_hash = ?",
            (key,),
        ).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def put(self, key: str, request: dict, response: dict, assessed_at: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO assessments VALUES (?, ?, ?, ?)",
                (key, json_text(request), json_text(response), assessed_at),
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
