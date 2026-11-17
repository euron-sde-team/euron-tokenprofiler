"""Builders for events used across the core and API tests."""

from __future__ import annotations

from typing import Any

from tokenprofiler_core.events import EventIn
from tokenprofiler_core.ingest import resolve
from tokenprofiler_core.pricing import PriceBook
from tokenprofiler_core.store import DAY_MS, StoredEvent

BOOK = PriceBook()
T0 = 1_760_000_000_000 // DAY_MS * DAY_MS  # a day boundary in Oct 2025


def ev(ts: int = T0 + 3_600_000, **kw: Any) -> StoredEvent:
    base: dict[str, Any] = {"model": "gpt-4o", "prompt_tokens": 1_000, "completion_tokens": 500, "ts": ts}
    base.update(kw)
    return resolve(EventIn(**base), BOOK)


def many(n: int, **kw: Any) -> list[StoredEvent]:
    return [ev(**{**kw, "id": f"{kw.get('id', 'e')}{i}"}) for i in range(n)]
