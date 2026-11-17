"""The usage store: SQLite (WAL), one connection behind a lock. Callers in async code run it in a worker thread."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .query import DIMENSIONS, METRICS, ORDERABLE, Filters, QueryError, dimension_sql

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    day INTEGER NOT NULL,
    hour INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    model_raw TEXT NOT NULL,
    app TEXT NOT NULL,
    environment TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    estimated INTEGER NOT NULL DEFAULT 0,
    cost_input INTEGER NOT NULL DEFAULT 0,
    cost_cached INTEGER NOT NULL DEFAULT 0,
    cost_cache_write INTEGER NOT NULL DEFAULT 0,
    cost_output INTEGER NOT NULL DEFAULT 0,
    cost_total INTEGER NOT NULL DEFAULT 0,
    priced INTEGER NOT NULL DEFAULT 1,
    saved_micro INTEGER NOT NULL DEFAULT 0,
    latency_ms REAL,
    ttft_ms REAL,
    status TEXT NOT NULL DEFAULT 'ok',
    error_type TEXT,
    http_status INTEGER,
    finish_reason TEXT,
    stream INTEGER NOT NULL DEFAULT 0,
    batch INTEGER NOT NULL DEFAULT 0,
    cache_hit INTEGER NOT NULL DEFAULT 0,
    max_tokens INTEGER,
    temperature REAL,
    prompt_id TEXT,
    prompt_hash TEXT,
    user_id TEXT,
    session_id TEXT,
    tags TEXT NOT NULL DEFAULT '{}',
    ingested_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ev_ts ON events (ts);
CREATE INDEX IF NOT EXISTS ev_app_ts ON events (app, ts);
CREATE INDEX IF NOT EXISTS ev_model_ts ON events (model, ts);
CREATE INDEX IF NOT EXISTS ev_prompt_ts ON events (prompt_id, ts);
CREATE INDEX IF NOT EXISTS ev_hash ON events (prompt_hash) WHERE prompt_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS ev_user_ts ON events (user_id, ts) WHERE user_id IS NOT NULL;
"""
COLUMNS = (
    "id", "ts", "day", "hour", "provider", "model", "model_raw", "app", "environment", "endpoint", "prompt_tokens",
    "completion_tokens", "cached_tokens", "cache_write_tokens", "reasoning_tokens", "estimated", "cost_input", "cost_cached",
    "cost_cache_write", "cost_output", "cost_total", "priced", "saved_micro", "latency_ms", "ttft_ms", "status", "error_type",
    "http_status", "finish_reason", "stream", "batch", "cache_hit", "max_tokens", "temperature", "prompt_id", "prompt_hash",
    "user_id", "session_id", "tags", "ingested_at",
)  # fmt: skip


@dataclass(slots=True)
class StoredEvent:
    """A fully resolved event: tokens settled, cost computed, names normalized."""

    id: str
    ts: int
    provider: str
    model: str
    model_raw: str
    app: str
    environment: str
    endpoint: str
    prompt_tokens: int
    completion_tokens: int
    cost_total: int
    ingested_at: int
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    estimated: bool = False
    cost_input: int = 0
    cost_cached: int = 0
    cost_cache_write: int = 0
    cost_output: int = 0
    priced: bool = True
    saved_micro: int = 0
    latency_ms: float | None = None
    ttft_ms: float | None = None
    status: str = "ok"
    error_type: str | None = None
    http_status: int | None = None
    finish_reason: str | None = None
    stream: bool = False
    batch: bool = False
    cache_hit: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    prompt_id: str | None = None
    prompt_hash: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    def row(self) -> tuple[Any, ...]:
        d = asdict(self)
        d["day"], d["hour"] = self.ts // DAY_MS, self.ts // HOUR_MS
        d["tags"] = json.dumps(self.tags, separators=(",", ":"), sort_keys=True)
        for k in ("estimated", "priced", "stream", "batch", "cache_hit"):
            d[k] = int(d[k])
        return tuple(d[c] for c in COLUMNS)


class UsageStore:
    def __init__(self, path: str | Path) -> None:
        self._lock = threading.RLock()
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    # ── writing ──────────────────────────────────────────────────────────────
    def record(self, events: Iterable[StoredEvent]) -> tuple[int, int]:
        """Inserts events in one transaction. Returns (inserted, duplicates): an existing id is skipped, not an error."""
        rows = [e.row() for e in events]
        if not rows:
            return 0, 0
        # ON CONFLICT(id) skips only a duplicate id. (INSERT OR IGNORE would also swallow NOT NULL and CHECK violations.)
        sql = f"INSERT INTO events ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))}) ON CONFLICT(id) DO NOTHING"
        with self._lock:
            before = self._conn.total_changes
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, rows)
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            inserted = self._conn.total_changes - before
        return inserted, len(rows) - inserted

    def delete_before(self, ts: int) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (ts,))
            return cur.rowcount

    def delete_all(self) -> int:
        with self._lock:
            return self._conn.execute("DELETE FROM events").rowcount

    # ── reading ──────────────────────────────────────────────────────────────
    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def ping(self) -> bool:
        self._all("SELECT 1")
        return True

    def count(self, f: Filters | None = None) -> int:
        where, params = (f or Filters()).where()
        return int(self._all(f"SELECT COUNT(*) AS n FROM events{where}", params)[0]["n"])

    def bounds(self) -> dict[str, int | None]:
        r = self._all("SELECT MIN(ts) AS lo, MAX(ts) AS hi, COUNT(*) AS n FROM events")[0]
        return {"first_ts": r["lo"], "last_ts": r["hi"], "events": r["n"]}

    def aggregate(
        self, f: Filters, group_by: list[str] | None = None, metrics: list[str] | None = None, *, order_by: str = "cost",
        desc: bool = True, limit: int = 100,
    ) -> list[dict[str, Any]]:  # fmt: skip
        """Metrics grouped by zero or more dimensions. Every name is checked against a whitelist."""
        metrics = metrics or ["requests", "prompt_tokens", "completion_tokens", "cost"]
        for m in metrics:
            if m not in METRICS:
                raise QueryError(f"unknown metric {m!r}")
        group_by = group_by or []
        if len(group_by) > 3:
            raise QueryError("group by at most 3 dimensions")
        if order_by not in ORDERABLE and not order_by.startswith("tag:"):
            raise QueryError(f"cannot order by {order_by!r}")
        dims, dim_params = [], []
        for g in group_by:
            sql, p = dimension_sql(g)
            dims.append(sql)
            dim_params.extend(p)
        select = [f"{d} AS g{i}" for i, d in enumerate(dims)] + [f"{METRICS[m]} AS {m}" for m in metrics]
        where, params = f.where()
        sql = f"SELECT {', '.join(select)} FROM events{where}"
        if dims:
            sql += " GROUP BY " + ", ".join(f"g{i}" for i in range(len(dims)))
        if order_by in metrics:
            sql += f" ORDER BY {order_by} {'DESC' if desc else 'ASC'}"
        elif group_by and order_by in group_by:
            sql += f" ORDER BY g{group_by.index(order_by)} {'DESC' if desc else 'ASC'}"
        sql += " LIMIT ?"
        out = []
        for r in self._all(sql, [*dim_params, *params, max(1, min(limit, 10_000))]):
            row: dict[str, Any] = {g: r[f"g{i}"] for i, g in enumerate(group_by)}
            row.update({m: (r[m] if r[m] is not None else 0) for m in metrics})
            out.append(row)
        return out

    def timeseries(
        self, f: Filters, bucket: str = "day", metrics: list[str] | None = None, group_by: str | None = None, *, top: int = 6,
    ) -> list[dict[str, Any]]:  # fmt: skip
        """Per-bucket metrics, optionally split by one dimension (the ``top`` largest by cost, the rest as "other")."""
        if bucket not in ("hour", "day"):
            raise QueryError("bucket must be hour or day")
        metrics = metrics or ["requests", "cost"]
        if group_by is None:
            return self.aggregate(f, [bucket], metrics, order_by=bucket, desc=False, limit=10_000)
        rows = self.aggregate(
            f,
            [bucket, group_by],
            [*metrics, "cost"] if "cost" not in metrics else metrics,
            order_by=bucket,
            desc=False,
            limit=10_000,
        )
        totals: dict[str, int] = {}
        for r in rows:
            totals[str(r[group_by])] = totals.get(str(r[group_by]), 0) + int(r["cost"])
        keep = {k for k, _ in sorted(totals.items(), key=lambda kv: -kv[1])[:top]}
        merged: dict[tuple[Any, str], dict[str, Any]] = {}
        for r in rows:
            key = str(r[group_by]) if str(r[group_by]) in keep else "other"
            slot = merged.setdefault(
                (r[bucket], key), {bucket: r[bucket], group_by: key, **dict.fromkeys(metrics, 0)}
            )
            for m in metrics:
                slot[m] += r[m] or 0
        return sorted(merged.values(), key=lambda r: (r[bucket], str(r[group_by])))

    def latency_percentiles(
        self, f: Filters, group_by: str | None = None, qs: tuple[float, ...] = (0.5, 0.95, 0.99)
    ) -> dict[str, dict[str, float]]:
        """Latency percentiles per group (or ``"all"``), over events that reported a latency."""
        gsql, gparams = dimension_sql(group_by) if group_by else ("'all'", [])
        where, params = f.where()
        where = (where + " AND " if where else " WHERE ") + "latency_ms IS NOT NULL"
        rows = self._all(
            f"SELECT {gsql} AS g, latency_ms FROM events{where} ORDER BY g, latency_ms", [*gparams, *params]
        )
        groups: dict[str, list[float]] = {}
        for r in rows:
            groups.setdefault(str(r["g"]), []).append(r["latency_ms"])
        out: dict[str, dict[str, float]] = {}
        for g, vals in groups.items():
            out[g] = {f"p{round(q * 100)}": _percentile(vals, q) for q in qs}
            out[g]["count"] = float(len(vals))
        return out

    def list_events(
        self, f: Filters, limit: int = 50, before: tuple[int, str] | None = None
    ) -> list[dict[str, Any]]:
        """Newest first. ``before`` is a (ts, id) cursor from the last row of the previous page."""
        where, params = f.where()
        if before is not None:
            where += (" AND " if where else " WHERE ") + "(ts < ? OR (ts = ? AND id < ?))"
            params = [*params, before[0], before[0], before[1]]
        rows = self._all(
            f"SELECT * FROM events{where} ORDER BY ts DESC, id DESC LIMIT ?",
            [*params, max(1, min(limit, 1000))],
        )
        return [_public(r) for r in rows]

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        rows = self._all("SELECT * FROM events WHERE id = ?", (event_id,))
        return _public(rows[0]) if rows else None

    def distinct(self, dimension: str, f: Filters | None = None, limit: int = 200) -> list[str]:
        if dimension not in DIMENSIONS and not dimension.startswith("tag:"):
            raise QueryError(f"unknown dimension {dimension!r}")
        sql, p = dimension_sql(dimension)
        where, params = (f or Filters()).where()
        rows = self._all(
            f"SELECT {sql} AS v, COUNT(*) AS n FROM events{where} GROUP BY v ORDER BY n DESC LIMIT ?",
            [*p, *params, limit],
        )
        return [str(r["v"]) for r in rows]

    def repeated_prompts(self, f: Filters, min_count: int = 2, limit: int = 50) -> list[dict[str, Any]]:
        """Prompts sent more than once, by fingerprint: the candidates for a response cache."""
        where, params = f.where()
        where = (
            where + " AND " if where else " WHERE "
        ) + "prompt_hash IS NOT NULL AND status = 'ok' AND cache_hit = 0"
        rows = self._all(
            f"SELECT prompt_hash, app, model, COUNT(*) AS n, SUM(cost_total) AS cost, AVG(prompt_tokens) AS avg_prompt, "
            f"AVG(completion_tokens) AS avg_completion, MIN(prompt_id) AS prompt_id FROM events{where} "
            f"GROUP BY prompt_hash, app, model HAVING n >= ? ORDER BY cost DESC LIMIT ?",
            [*params, min_count, limit],
        )
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolation percentile of an already sorted list."""
    if not sorted_vals:
        return 0.0
    pos = q * (len(sorted_vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo), 2)


def _public(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["tags"] = json.loads(d["tags"])
    for k in ("estimated", "priced", "stream", "batch", "cache_hit"):
        d[k] = bool(d[k])
    return d
