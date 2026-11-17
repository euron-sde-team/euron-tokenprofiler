"""The control-plane database (API keys, budgets, alerts, price overrides, dismissals): SQLite behind a lock."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    prefix TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT NOT NULL, scopes TEXT NOT NULL, apps TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL, last_used_at REAL, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, limit_micro INTEGER NOT NULL, scope TEXT NOT NULL DEFAULT '{}',
    warn_at REAL NOT NULL DEFAULT 0.8, webhook_url TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL, dedupe TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '{}', budget_id INTEGER, acknowledged INTEGER NOT NULL DEFAULT 0, delivered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS alerts_ts ON alerts (ts);
CREATE TABLE IF NOT EXISTS dismissed (rec_id TEXT PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS price_overrides (model TEXT PRIMARY KEY, body TEXT NOT NULL, updated_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, period TEXT NOT NULL, bucket TEXT NOT NULL, key TEXT NOT NULL, bytes INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def _run(self, sql: str, params: Iterable[Any]) -> tuple[list[sqlite3.Row], int, int | None]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(params))
            return cur.fetchall(), cur.rowcount, cur.lastrowid

    async def query(self, sql: str, *params: Any) -> list[sqlite3.Row]:
        return (await asyncio.to_thread(self._run, sql, params))[0]

    async def execute(self, sql: str, *params: Any) -> int:
        """Runs a write and returns the number of rows it changed."""
        return (await asyncio.to_thread(self._run, sql, params))[1]

    async def insert(self, sql: str, *params: Any) -> int:
        """Runs an INSERT and returns the new row id."""
        rowid = (await asyncio.to_thread(self._run, sql, params))[2]
        assert rowid is not None
        return rowid

    def ping(self) -> bool:
        self._run("SELECT 1", ())
        return True

    def close(self) -> None:
        with self._lock:
            self._conn.close()
