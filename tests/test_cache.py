import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from jevotron.cache import Cache
from jevotron.cli import main


@pytest.fixture
def clock(monkeypatch):
    state = {"now": 0.0, "delays": []}

    def sleep(delay):
        state["delays"].append(delay)
        state["now"] += delay

    monkeypatch.setattr("jevotron.cache.time.monotonic", lambda: state["now"])
    monkeypatch.setattr("jevotron.cache.time.sleep", sleep)
    return state


def wal_fault(monkeypatch, code, failures):
    """Inject only WAL lock failures; use real SQLite for everything else."""
    state = {"attempts": 0, "closed": False}

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql == "PRAGMA journal_mode=WAL":
                state["attempts"] += 1
                if failures is None or state["attempts"] <= failures:
                    error = sqlite3.OperationalError("injected SQLite failure")
                    error.sqlite_errorcode = code
                    raise error
            return super().execute(sql, *args)

        def close(self):
            state["closed"] = True
            super().close()

    connection = sqlite3.connect(":memory:", factory=Connection)
    monkeypatch.setattr("jevotron.cache.sqlite3.connect", lambda *a, **k: connection)
    return connection, state


@pytest.mark.parametrize(
    "code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_BUSY_SNAPSHOT]
)
def test_wal_contention_retries_and_restores_sqlite_timeout(monkeypatch, clock, code):
    connection, state = wal_fault(monkeypatch, code, failures=2)
    with Cache(":memory:") as cache:
        cache.put("key", {}, {"ok": True}, "date")
        assert cache.get("key") == ({"ok": True}, "date")
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
    assert state == {"attempts": 3, "closed": True}
    assert clock["delays"] == [0.05, 0.05]


@pytest.mark.parametrize(
    "code", [sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_ERROR]
)
def test_wal_noncontention_fails_immediately_and_closes(monkeypatch, clock, code):
    connection, state = wal_fault(monkeypatch, code, failures=None)
    with pytest.raises(
        sqlite3.OperationalError, match="injected SQLite failure"
    ) as error:
        Cache(":memory:")
    assert error.value.sqlite_errorcode == code
    assert state == {"attempts": 1, "closed": True}
    assert clock["delays"] == []
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


def test_wal_contention_exhaustion_uses_one_budget_and_closes(monkeypatch, clock):
    monkeypatch.setattr("jevotron.cache._LOCK_TIMEOUT", 0.12)
    _, state = wal_fault(monkeypatch, sqlite3.SQLITE_BUSY, failures=None)
    with pytest.raises(sqlite3.OperationalError, match="injected SQLite failure"):
        Cache(":memory:")
    assert state == {"attempts": 4, "closed": True}
    assert clock["delays"] == pytest.approx([0.05, 0.05, 0.02])
    assert clock["now"] == pytest.approx(0.12)


def test_cli_wal_exhaustion_reports_controlled_cache_error(
    tmp_path, monkeypatch, clock, capsys
):
    monkeypatch.setattr("jevotron.cache._LOCK_TIMEOUT", 0.12)
    _, state = wal_fault(monkeypatch, sqlite3.SQLITE_BUSY, failures=None)
    data = tmp_path / "data.csv"
    data.write_text("value\nok\n")
    assert main(["scan", str(data), "--cache", str(tmp_path / "cache.db")]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "Error: injected SQLite failure" in output.err
    assert "INCOMPLETE: 0 entries assessed" in output.err
    assert "Traceback" not in output.err
    assert state["closed"]


@pytest.mark.parametrize("round_number", range(3))
def test_concurrent_first_use_preserves_all_records(tmp_path, round_number):
    path = tmp_path / f"cache-{round_number}.sqlite3"
    workers = 64
    barrier = Barrier(workers, timeout=10)

    def write(index):
        barrier.wait()
        with Cache(path) as cache:
            cache.put(str(index), {"index": index}, {"answer": index}, "date")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(write, range(workers)))
    with Cache(path) as cache:
        for index in range(workers):
            assert cache.get(str(index)) == ({"answer": index}, "date")
        assert cache.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert cache.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
