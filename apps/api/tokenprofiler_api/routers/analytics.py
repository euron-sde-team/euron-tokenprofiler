"""Read-side analytics: overview, usage breakdowns, time series, events, prompts, model comparison, anomalies."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from tokenprofiler_core import anomaly, forecast
from tokenprofiler_core.events import now_ms
from tokenprofiler_core.pricing import Usage
from tokenprofiler_core.query import METRICS, Filters, QueryError
from tokenprofiler_core.store import DAY_MS

from ..errors import ApiError
from ..state import State
from .common import Read, Scoped

router = APIRouter(prefix="/api", tags=["analytics"])
STD = ["requests", "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens", "cost", "errors"]


def _q(fn: Any, *a: Any, **kw: Any) -> Any:
    return asyncio.to_thread(fn, *a, **kw)


def _delta(now: float, prev: float) -> float | None:
    return None if not prev else round(now / prev - 1, 4)


@router.get("/overview", summary="The headline numbers: spend, trend, forecast, top spenders")
async def overview(request: Request, state: State, principal: Read, f: Scoped) -> dict[str, Any]:
    result: dict[str, Any] = await state.cache.get_or_compute(
        state.cache_key(request, principal), lambda: _overview(state, f)
    )
    return result


async def _overview(state: State, f: Filters) -> dict[str, Any]:
    now = now_ms()
    span = max(DAY_MS, (f.until or now) - (f.since or now - 30 * DAY_MS))
    cur = f
    prev = Filters(
        since=(f.since or now) - span, until=f.since, eq=dict(f.eq), tags=dict(f.tags), within=dict(f.within)
    )
    metrics = [
        *STD,
        "saved",
        "estimated",
        "unpriced",
        "avg_latency_ms",
        "cost_input",
        "cost_output",
        "cache_hits",
    ]
    totals, before, daily, models, apps, prompts, lat = await asyncio.gather(
        _q(state.usage.aggregate, cur, None, metrics), _q(state.usage.aggregate, prev, None, ["requests", "cost", "total_tokens", "errors"]),
        _q(state.usage.aggregate, Filters(since=now - 60 * DAY_MS, eq=dict(f.eq), tags=dict(f.tags), within=dict(f.within)), ["day"], ["cost"], order_by="day", desc=False, limit=100),
        _q(state.usage.aggregate, cur, ["model"], ["requests", "cost", "total_tokens"], limit=6),
        _q(state.usage.aggregate, cur, ["app"], ["requests", "cost", "total_tokens"], limit=6),
        _q(state.usage.aggregate, cur, ["prompt_id"], ["requests", "cost", "total_tokens"], limit=6),
        _q(state.usage.latency_percentiles, cur),
    )  # fmt: skip
    t, b = totals[0], before[0]
    fc = forecast.project({int(r["day"]): int(r["cost"]) for r in daily}, now)
    series = [(int(r["day"]), float(r["cost"])) for r in daily if r["day"] < now // DAY_MS]
    anomalies = anomaly.detect(series, floor=50_000)
    today = now // DAY_MS
    return {
        "range": {"since": f.since, "until": f.until or now}, "totals": t,
        "previous": b, "change": {k: _delta(t[k], b[k]) for k in ("requests", "cost", "total_tokens", "errors")},
        "cost_per_1k_requests_micro": round(t["cost"] / t["requests"] * 1000) if t["requests"] else 0,
        "error_rate": round(t["errors"] / t["requests"], 4) if t["requests"] else 0.0, "latency": lat.get("all", {}),
        "forecast": {
            "month_to_date_micro": fc.month_to_date, "projected_month_micro": fc.projected_month_total, "run_rate_per_day_micro": round(fc.run_rate_per_day),
            "trend_pct": fc.trend_pct, "confidence": fc.confidence, "days_in_month": fc.days_in_month,
        },
        "today_micro": next((int(r["cost"]) for r in daily if r["day"] == today), 0),
        "top_models": models, "top_apps": apps, "top_prompts": prompts,
        "recent_anomalies": [{"day": a.day, "value_micro": a.value, "expected_micro": a.expected, "ratio": round(a.ratio, 2), "direction": a.direction} for a in anomalies[-3:]],
        "bounds": await _q(state.usage.bounds), "prices_as_of": state.prices.as_of,
    }  # fmt: skip


@router.get("/usage/summary", summary="Metrics grouped by up to three dimensions")
async def summary(
    state: State, principal: Read, f: Scoped, group_by: Annotated[list[str] | None, Query()] = None, metrics: Annotated[list[str] | None, Query()] = None,
    order_by: str = "cost", desc: bool = True, limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> dict[str, Any]:  # fmt: skip
    try:
        rows = await _q(
            state.usage.aggregate,
            f,
            group_by or [],
            metrics or STD,
            order_by=order_by,
            desc=desc,
            limit=limit,
        )
    except QueryError as exc:
        raise ApiError(422, "invalid_query", str(exc)) from exc
    return {"group_by": group_by or [], "metrics": metrics or STD, "data": rows}


@router.get("/usage/timeseries", summary="Metrics over time, optionally split by one dimension")
async def timeseries(
    state: State, principal: Read, f: Scoped, bucket: str = "day", group_by: str | None = None, metrics: Annotated[list[str] | None, Query()] = None,
    top: Annotated[int, Query(ge=1, le=12)] = 6,
) -> dict[str, Any]:  # fmt: skip
    try:
        rows = await _q(
            state.usage.timeseries,
            f,
            bucket,
            metrics or ["requests", "cost", "total_tokens"],
            group_by,
            top=top,
        )
    except QueryError as exc:
        raise ApiError(422, "invalid_query", str(exc)) from exc
    return {"bucket": bucket, "group_by": group_by, "data": rows}


@router.get("/usage/metrics", summary="The names of the available metrics and dimensions")
async def names(_: Read) -> dict[str, list[str]]:
    from tokenprofiler_core.query import DIMENSIONS

    return {"metrics": sorted(METRICS), "dimensions": [*sorted(DIMENSIONS), "tag:<name>"]}


@router.get("/dimensions/{dimension}", summary="Distinct values of a dimension, for filter menus")
async def dimension(dimension: str, state: State, principal: Read, f: Scoped) -> dict[str, Any]:
    try:
        return {"dimension": dimension, "values": await _q(state.usage.distinct, dimension, f)}
    except QueryError as exc:
        raise ApiError(422, "invalid_query", str(exc)) from exc


@router.get("/latency", summary="Latency percentiles, optionally per dimension")
async def latency(state: State, principal: Read, f: Scoped, group_by: str | None = None) -> dict[str, Any]:
    try:
        return {"group_by": group_by, "data": await _q(state.usage.latency_percentiles, f, group_by)}
    except QueryError as exc:
        raise ApiError(422, "invalid_query", str(exc)) from exc


@router.get("/events", summary="Recent events, newest first, with cursor pagination")
async def events(
    state: State,
    principal: Read,
    f: Scoped,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    cursor: str | None = None,
) -> dict[str, Any]:
    before = None
    if cursor:
        try:
            ts, _, eid = cursor.partition(":")
            before = (int(ts), eid)
        except ValueError as exc:
            raise ApiError(422, "invalid_cursor", "the cursor is not valid") from exc
    rows = await _q(state.usage.list_events, f, limit, before)
    nxt = f"{rows[-1]['ts']}:{rows[-1]['id']}" if len(rows) == limit else None
    return {"data": rows, "next_cursor": nxt}


@router.get("/events/{event_id}", summary="One event")
async def event(event_id: str, state: State, principal: Read) -> dict[str, Any]:
    e = await _q(state.usage.get_event, event_id)
    if e is None or not principal.may_use_app(e["app"]):
        raise ApiError(
            404, "event_not_found", "no such event"
        )  # a hidden app's event is indistinguishable from a missing one
    return dict(e)


@router.get("/prompts", summary="Prompt templates ranked by spend")
async def prompts(
    state: State, principal: Read, f: Scoped, limit: Annotated[int, Query(ge=1, le=500)] = 50
) -> dict[str, Any]:
    rows = await _q(
        state.usage.aggregate,
        f,
        ["prompt_id", "app", "model"],
        [*STD, "avg_latency_ms", "max_prompt_tokens"],
        limit=limit,
    )
    for r in rows:
        n = max(1, r["requests"])
        r["avg_prompt_tokens"], r["avg_completion_tokens"] = (
            round(r["prompt_tokens"] / n),
            round(r["completion_tokens"] / n),
        )
        r["cost_per_request_micro"] = round(r["cost"] / n)
        r["cached_share"] = round(r["cached_tokens"] / r["prompt_tokens"], 4) if r["prompt_tokens"] else 0.0
    return {"data": rows}


@router.get("/prompts/{prompt_id:path}", summary="One prompt template: model mix, trend, latency, errors")
async def prompt_detail(prompt_id: str, state: State, principal: Read, f: Scoped) -> dict[str, Any]:
    pf = f.narrowed(prompt_id=prompt_id)
    totals, models, trend, lat, errors = await asyncio.gather(
        _q(state.usage.aggregate, pf, None, [*STD, "avg_latency_ms", "max_prompt_tokens", "saved"]),
        _q(state.usage.aggregate, pf, ["model"], ["requests", "cost", "prompt_tokens", "completion_tokens"]),
        _q(state.usage.timeseries, pf, "day", ["requests", "cost", "prompt_tokens"]),
        _q(state.usage.latency_percentiles, pf), _q(state.usage.aggregate, pf, ["error_type"], ["requests", "cost"], limit=10),
    )  # fmt: skip
    if not totals[0]["requests"]:
        raise ApiError(404, "prompt_not_found", f"no events for prompt {prompt_id!r} in this range")
    return {
        "prompt_id": prompt_id,
        "totals": totals[0],
        "models": models,
        "trend": trend,
        "latency": lat.get("all", {}),
        "errors": [e for e in errors if e["error_type"] != "(none)"],
    }


@router.get(
    "/models/compare",
    summary="What this traffic would have cost on every other model (cost only: quality differs)",
)
async def compare_models(
    request: Request, state: State, principal: Read, f: Scoped, endpoint: str = "chat"
) -> dict[str, Any]:
    result: dict[str, Any] = await state.cache.get_or_compute(
        state.cache_key(request, principal), lambda: _compare(state, f, endpoint)
    )
    return result


async def _compare(state: State, f: Filters, endpoint: str) -> dict[str, Any]:
    rows = await _q(
        state.usage.aggregate,
        f.narrowed(endpoint=endpoint),
        ["model"],
        [
            "requests",
            "prompt_tokens",
            "cached_tokens",
            "cache_write_tokens",
            "completion_tokens",
            "cost",
            "max_prompt_tokens",
        ],
        limit=200,
    )
    actual = sum(r["cost"] for r in rows)
    usage = Usage(
        sum(r["prompt_tokens"] for r in rows),
        sum(r["completion_tokens"] for r in rows),
        sum(r["cached_tokens"] for r in rows),
        sum(r["cache_write_tokens"] for r in rows),
    )
    biggest = max((r["max_prompt_tokens"] for r in rows), default=0)
    out = []
    for p in state.prices.all():
        if (
            p.provider in ("self-hosted", "test")
            or (endpoint != "embedding" and p.id.startswith("text-embedding"))
            or (endpoint == "embedding" and not p.id.startswith("text-embedding"))
        ):
            continue
        c = state.prices.cost(p.id, usage).total
        out.append({"model": p.id, "provider": p.provider, "tier": p.tier, "cost_micro": c, "delta_micro": c - actual, "delta_pct": round(c / actual - 1, 4) if actual else None,
                    "fits_largest_prompt": p.context is None or biggest <= p.context})  # fmt: skip
    out.sort(key=lambda r: r["cost_micro"])
    return {"actual_cost_micro": actual, "requests": sum(r["requests"] for r in rows), "tokens": {"prompt": usage.prompt_tokens, "completion": usage.completion_tokens, "cached": usage.cached_tokens},
            "current_models": [{"model": r["model"], "cost_micro": r["cost"], "requests": r["requests"]} for r in rows], "candidates": out,
            "note": "Cost only. A cheaper model may answer differently: evaluate quality before switching."}  # fmt: skip


@router.get("/anomalies", summary="Days whose spend departs from the recent baseline")
async def anomalies(
    state: State,
    principal: Read,
    f: Scoped,
    group_by: str = "app",
    threshold: Annotated[float, Query(ge=2, le=10)] = 3.5,
) -> dict[str, Any]:
    now = now_ms()
    f2 = Filters(since=now - 60 * DAY_MS, eq=dict(f.eq), tags=dict(f.tags), within=dict(f.within))
    today = now // DAY_MS
    try:
        overall = await _q(
            state.usage.aggregate, f2, ["day"], ["cost"], order_by="day", desc=False, limit=100
        )
        per = await _q(
            state.usage.aggregate, f2, ["day", group_by], ["cost"], order_by="day", desc=False, limit=10_000
        )
    except QueryError as exc:
        raise ApiError(422, "invalid_query", str(exc)) from exc
    out: list[dict[str, Any]] = []
    series = {"(all)": [(int(r["day"]), float(r["cost"])) for r in overall]}
    for r in per:
        series.setdefault(str(r[group_by]), []).append((int(r["day"]), float(r["cost"])))
    for name, pts in series.items():
        full = {d: v for d, v in pts if d < today}  # today is incomplete, never judge it
        filled = [(d, full.get(d, 0.0)) for d in range(min(full, default=today), today)]
        for a in anomaly.detect(filled, threshold=threshold, floor=50_000):
            out.append(
                {
                    "scope": name,
                    "day": a.day,
                    "value_micro": a.value,
                    "expected_micro": round(a.expected),
                    "ratio": round(a.ratio, 2),
                    "score": a.score,
                    "direction": a.direction,
                }
            )
    out.sort(key=lambda r: (-r["day"], -abs(r["score"])))
    return {"group_by": group_by, "data": out[:100]}
