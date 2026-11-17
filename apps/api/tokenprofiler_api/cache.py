"""A tiny single-flight TTL cache for the heavy read endpoints.

A dashboard polls the same few expensive queries; computing each once per few seconds is plenty fresh. The cache key always
includes the caller's app allow-list, so one team's cached numbers can never be served to a key that may not see them.
Concurrent requests for the same key wait for one computation instead of each running it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

MAX_ENTRIES = 256


class TTLCache:
    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl, self._clock = ttl_s, clock
        self._items: dict[Any, tuple[float, Any]] = {}
        self._locks: dict[Any, asyncio.Lock] = {}
        self.hits = self.misses = 0

    async def get_or_compute(self, key: Any, factory: Callable[[], Awaitable[Any]]) -> Any:
        if self.ttl <= 0:
            return await factory()
        now = self._clock()
        hit = self._items.get(key)
        if hit and now - hit[0] < self.ttl:
            self.hits += 1
            return hit[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            hit = self._items.get(key)  # another request may have filled it while we waited
            if hit and self._clock() - hit[0] < self.ttl:
                self.hits += 1
                return hit[1]
            self.misses += 1
            value = await factory()
            self._items[key] = (self._clock(), value)
            if len(self._items) > MAX_ENTRIES:
                for old in sorted(self._items, key=lambda k: self._items[k][0])[
                    : len(self._items) - MAX_ENTRIES
                ]:
                    self._items.pop(old, None)
                    self._locks.pop(old, None)
            return value

    def clear(self) -> None:
        self._items.clear()
