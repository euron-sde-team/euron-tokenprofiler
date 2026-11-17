"""Filters, dimensions and metrics for analytics queries. Names are whitelisted so SQL is never built from user text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

DIMENSIONS: dict[str, str] = {
    "app": "app", "environment": "environment", "model": "model", "provider": "provider", "endpoint": "endpoint",
    "prompt_id": "COALESCE(prompt_id, '(none)')", "user_id": "COALESCE(user_id, '(none)')", "status": "status",
    "error_type": "COALESCE(error_type, '(none)')", "finish_reason": "COALESCE(finish_reason, '(none)')",
    "day": "day", "hour": "hour", "session_id": "COALESCE(session_id, '(none)')",
    "stream": "stream", "batch": "batch", "cache_hit": "cache_hit",
}  # fmt: skip
TAG_DIM = re.compile(r"^tag:([A-Za-z][A-Za-z0-9_]{0,31})$")
EQ_FILTERS = (
    "app",
    "environment",
    "model",
    "provider",
    "endpoint",
    "prompt_id",
    "user_id",
    "status",
    "error_type",
    "session_id",
    "finish_reason",
)

# Metric name -> SQL aggregate over the events table.
METRICS: dict[str, str] = {
    "requests": "COUNT(*)",
    "errors": "SUM(status = 'error')",
    "prompt_tokens": "SUM(prompt_tokens)",
    "completion_tokens": "SUM(completion_tokens)",
    "cached_tokens": "SUM(cached_tokens)",
    "total_tokens": "SUM(prompt_tokens + completion_tokens)",
    "cost": "SUM(cost_total)",
    "cost_input": "SUM(cost_input + cost_cached + cost_cache_write)",
    "cost_output": "SUM(cost_output)",
    "error_cost": "SUM(CASE WHEN status = 'error' THEN cost_total ELSE 0 END)",
    "saved": "SUM(saved_micro)",
    "unpriced": "SUM(priced = 0)",
    "estimated": "SUM(estimated = 1)",
    "avg_latency_ms": "AVG(latency_ms)",
    "max_latency_ms": "MAX(latency_ms)",
}
ORDERABLE = (*METRICS, *DIMENSIONS)


class QueryError(ValueError):
    """A query named something that does not exist."""


@dataclass(slots=True)
class Filters:
    since: int | None = None  # epoch ms, inclusive
    until: int | None = None  # epoch ms, exclusive
    eq: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)

    def where(self) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if self.since is not None:
            clauses.append("ts >= ?")
            params.append(self.since)
        if self.until is not None:
            clauses.append("ts < ?")
            params.append(self.until)
        for k, v in self.eq.items():
            if k not in EQ_FILTERS:
                raise QueryError(f"cannot filter by {k!r}")
            clauses.append(f"{k} = ?")
            params.append(v)
        for k, v in self.tags.items():
            if not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,31}$", k):
                raise QueryError(f"invalid tag name {k!r}")
            clauses.append("json_extract(tags, ?) = ?")
            params.extend([f"$.{k}", v])
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def dimension_sql(name: str) -> tuple[str, list[object]]:
    """The SQL expression for a group-by dimension, plus its bind parameters (tags are parameterized)."""
    if name in DIMENSIONS:
        return DIMENSIONS[name], []
    m = TAG_DIM.match(name)
    if m:
        return "COALESCE(json_extract(tags, ?), '(none)')", [f"$.{m.group(1)}"]
    raise QueryError(f"unknown dimension {name!r}")
