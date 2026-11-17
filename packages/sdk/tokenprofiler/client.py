"""The profiler client: a bounded queue and a background thread that batches events to TokenProfiler.

Design rules, because this runs inside someone else's application:

* ``track`` never raises and never blocks: it puts the event on a bounded queue and returns.
* When the queue is full the *oldest* event is dropped (and counted): losing telemetry beats growing memory without bound.
* Sending happens on a daemon thread with retries and backoff; every event carries an id, so a retried batch cannot be
  double counted. A TokenProfiler outage costs the application nothing but the telemetry.
* ``flush`` and ``close`` (also registered with ``atexit``) drain the queue with a deadline.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from .adapters import finish_reason, usage_from_openai

log = logging.getLogger("tokenprofiler")


@dataclass
class Stats:
    queued: int = 0
    sent: int = 0
    duplicates: int = 0
    rejected: int = 0
    dropped: int = 0
    failed_batches: int = 0
    last_error: str | None = None


class Profiler:
    def __init__(
        self, url: str | None = None, api_key: str | None = None, *, app: str = "default", environment: str = "production", batch_size: int = 200,
        flush_interval: float = 2.0, max_queue: int = 10_000, timeout: float = 5.0, max_retries: int = 3, enabled: bool = True,
        transport: httpx.BaseTransport | None = None, tags: dict[str, str] | None = None,
    ) -> None:  # fmt: skip
        self.app, self.environment, self.default_tags = app, environment, dict(tags or {})
        self.enabled = bool(enabled and url and api_key)
        if enabled and not self.enabled:
            log.warning("tokenprofiler: no url or api_key given, telemetry is disabled")
        self.batch_size, self.flush_interval, self.max_retries = batch_size, flush_interval, max_retries
        self.stats = Stats()
        self._q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=max_queue)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._client = (
            httpx.Client(
                base_url=(url or "").rstrip("/"),
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
                transport=transport,
            )
            if self.enabled
            else None
        )
        self._thread: threading.Thread | None = None
        if self.enabled:
            self._thread = threading.Thread(target=self._run, name="tokenprofiler", daemon=True)
            self._thread.start()
            atexit.register(self.close)

    # ── recording ────────────────────────────────────────────────────────────
    def track(
        self,
        model: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        **fields: Any,
    ) -> str | None:
        """Queues one usage event. Returns its id (or None when disabled). Never raises, never blocks."""
        if not self.enabled:
            return None
        try:
            event: dict[str, Any] = {
                "id": uuid.uuid4().hex,
                "ts": int(time.time() * 1000),
                "model": model,
                "app": self.app,
                "environment": self.environment,
                **fields,
            }
            if prompt_tokens is not None:
                event["prompt_tokens"] = int(prompt_tokens)
            if completion_tokens is not None:
                event["completion_tokens"] = int(completion_tokens)
            if self.default_tags or event.get("tags"):
                event["tags"] = {**self.default_tags, **(event.get("tags") or {})}
            self._put(event)
            return str(event["id"])
        except Exception:
            self.stats.last_error = "track failed"
            return None

    def _put(self, event: dict[str, Any]) -> None:
        while True:
            try:
                self._q.put_nowait(event)
                self.stats.queued += 1
                if self._q.qsize() >= self.batch_size:
                    self._wake.set()
                return
            except queue.Full:
                with contextlib.suppress(queue.Empty):
                    self._q.get_nowait()  # drop the oldest
                    self.stats.dropped += 1

    @contextlib.contextmanager
    def span(self, model: str, **fields: Any) -> Iterator[Span]:
        """Times a block and records it; call ``span.record(response)`` or ``span.set(...)`` inside. An exception raised in the
        block is recorded as an error event and re-raised."""
        s = Span(model, fields)
        start = time.perf_counter()
        try:
            yield s
        except BaseException as exc:
            s.fields.update(status="error", error_type=type(exc).__name__)
            s.fields.setdefault("prompt_tokens", 0)
            raise
        finally:
            s.fields["latency_ms"] = round((time.perf_counter() - start) * 1000, 1)
            if s.fields.get("status") == "error":
                s.fields.setdefault("completion_tokens", 0)
            self.track(model, **s.fields)

    # ── sending ──────────────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.flush_interval)
            self._wake.clear()
            self._drain(deadline=None)
        self._drain(deadline=time.monotonic() + 5)

    def _drain(self, deadline: float | None) -> None:
        while True:
            batch: list[dict[str, Any]] = []
            while len(batch) < self.batch_size:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                return
            self._send(batch)
            if deadline is not None and time.monotonic() > deadline:
                return

    def _send(self, batch: list[dict[str, Any]]) -> None:
        assert self._client is not None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._client.post("/v1/events", json={"events": batch})
                if r.status_code == 200:
                    body = r.json()
                    with self._lock:
                        self.stats.sent += body.get("accepted", 0)
                        self.stats.duplicates += body.get("duplicates", 0)
                        self.stats.rejected += body.get("rejected", 0)
                    return
                if r.status_code in (400, 401, 403, 413, 422):  # retrying cannot help
                    self.stats.rejected += len(batch)
                    self.stats.last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                    log.warning("tokenprofiler: batch rejected (%s)", self.stats.last_error)
                    return
                self.stats.last_error = f"HTTP {r.status_code}"
            except (httpx.HTTPError, ValueError) as exc:
                self.stats.last_error = type(exc).__name__
            if attempt < self.max_retries:
                time.sleep(min(0.2 * 2**attempt, 5))
        self.stats.failed_batches += 1
        self.stats.dropped += len(batch)
        log.warning(
            "tokenprofiler: could not deliver %d events (%s); dropped", len(batch), self.stats.last_error
        )

    def flush(self, timeout: float = 10.0) -> None:
        """Blocks until everything queued so far has been sent (or failed), up to ``timeout`` seconds."""
        if not self.enabled:
            return
        deadline = time.monotonic() + timeout
        self._wake.set()
        while time.monotonic() < deadline:
            if self._q.empty() and self._idle():
                return
            time.sleep(0.01)

    def _idle(self) -> bool:
        return (
            self.stats.sent + self.stats.duplicates + self.stats.rejected + self.stats.dropped
            >= self.stats.queued
        )

    def close(self) -> None:
        if not self.enabled or self._stop.is_set():
            return
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=8)
        if self._client:
            self._client.close()

    # ── provider helpers ─────────────────────────────────────────────────────
    def wrap_openai(self, client: Any, **defaults: Any) -> Any:
        from .wrappers import wrap_openai

        return wrap_openai(self, client, **defaults)

    def wrap_openai_async(self, client: Any, **defaults: Any) -> Any:
        from .wrappers import wrap_openai_async

        return wrap_openai_async(self, client, **defaults)

    def wrap_anthropic(self, client: Any, **defaults: Any) -> Any:
        from .wrappers import wrap_anthropic

        return wrap_anthropic(self, client, **defaults)


@dataclass
class Span:
    model: str
    fields: dict[str, Any] = field(default_factory=dict)

    def set(self, **fields: Any) -> None:
        self.fields.update(fields)

    def record(self, response: Any) -> None:
        """Fills usage and finish reason from an OpenAI-style response object."""
        self.fields.update(usage_from_openai(response))
        if (fr := finish_reason(response)) and "finish_reason" not in self.fields:
            self.fields["finish_reason"] = fr
