"""Content-addressed successful assessments, committed after each API call."""

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from jevotron.models import json_text

_LOCK_TIMEOUT = 30


class Cache:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(path), timeout=_LOCK_TIMEOUT)
        try:
            self._enable_wal()
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS assessments (
                    request_hash TEXT PRIMARY KEY,
                    request_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    assessed_at TEXT NOT NULL
                )
            """)
            self.connection.commit()
        except BaseException:
            self.connection.close()
            raise

    def _enable_wal(self) -> None:
        # Journal-mode lock upgrades can fail without invoking SQLite's busy
        # handler. Own this wait budget so retries cannot each spend 30 seconds.
        self.connection.execute("PRAGMA busy_timeout=0")
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while True:
            try:
                self.connection.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
                remaining = deadline - time.monotonic()
                if (
                    code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                    or remaining <= 0
                ):
                    raise
                time.sleep(min(0.05, remaining))
        self.connection.execute(f"PRAGMA busy_timeout={int(_LOCK_TIMEOUT * 1000)}")

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
