"""Pricing, the Prompt Lab, exports and reports, API keys, system info."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response, StreamingResponse
from tokenprofiler_core.events import now_ms
from tokenprofiler_core.pricing import ModelPrice, Usage, normalize_model
from tokenprofiler_core.prompts import analyze, price_table
from tokenprofiler_core.query import Filters

from .. import __version__
from ..auth import ALL_SCOPES, ApiKeyCreated, ApiKeyRecord, Principal, require
from ..errors import ApiError
from ..schemas import AnalyzeRequest, KeyIn, PriceIn
from ..state import AppState, State
from .common import Read, Scoped

router = APIRouter(prefix="/api", tags=["admin"])
PriceWriter = Annotated[Principal, Depends(require("pricing:write"))]
Admin = Annotated[Principal, Depends(require("admin"))]


# ── pricing ──────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/pricing", summary="The price book (built-in prices plus your overrides)")
async def list_prices(state: State, _: Read) -> dict[str, Any]:
    return {
        "as_of": state.prices.as_of,
        "unit": "USD per 1M tokens",
        "data": [
            {**p.model_dump(), "overridden": p.id in state.prices.overridden} for p in state.prices.all()
        ],
    }


@router.get("/pricing/unpriced", summary="Models seen in your traffic that have no price")
async def unpriced(state: State, principal: Read, f: Scoped) -> dict[str, Any]:
    rows = await asyncio.to_thread(
        state.usage.aggregate, f.narrowed(), ["model"], ["requests", "unpriced", "total_tokens"], limit=500
    )
    return {"data": [r for r in rows if r["unpriced"]]}


@router.put("/pricing/{model}", summary="Set or override a model's price")
async def set_price(model: str, body: PriceIn, state: State, principal: PriceWriter) -> dict[str, Any]:
    mid = normalize_model(model)
    if not mid or len(mid) > 120:
        raise ApiError(422, "invalid_model", "model must be 1-120 characters")
    price = ModelPrice(id=mid, **body.model_dump())
    state.prices.set(price)
    await state.db.execute(
        "INSERT INTO price_overrides (model, body, updated_at) VALUES (?,?,?) ON CONFLICT(model) DO UPDATE SET body=excluded.body, updated_at=excluded.updated_at",
        mid,
        price.model_dump_json(),
        time.time(),
    )
    return {
        **price.model_dump(),
        "overridden": True,
        "note": "Past events keep their old cost until you POST /api/pricing/reprice.",
    }


@router.delete(
    "/pricing/{model}", summary="Remove an override (a built-in price comes back)", status_code=204
)
async def delete_price(model: str, state: State, principal: PriceWriter) -> Response:
    mid = normalize_model(model)
    if not state.prices.remove(mid):
        raise ApiError(404, "override_not_found", f"{mid!r} has no override")
    await state.db.execute("DELETE FROM price_overrides WHERE model = ?", mid)
    return Response(status_code=204)


@router.post("/pricing/reprice", summary="Recompute stored costs for a model from its current price")
async def reprice(body: dict[str, str], state: State, principal: PriceWriter) -> dict[str, Any]:
    mid = normalize_model(body.get("model", ""))
    p = state.prices.resolve(mid)
    if p is None or p.id != mid:
        raise ApiError(404, "price_not_found", f"{mid!r} has no price: set one first")
    n = await asyncio.to_thread(
        state.usage.reprice, mid, input=p.input, output=p.output, cached=p.cached_input if p.cached_input is not None else p.input,
        cache_write=p.cache_write if p.cache_write is not None else p.input, batch_discount=p.batch_discount,
    )  # fmt: skip
    return {"model": mid, "events_updated": n}


# ── prompt lab ───────────────────────────────────────────────────────────────────────────────────────────────
@router.post(
    "/prompts/analyze", summary="Prompt Lab: token breakdown, waste, and what it costs on each model"
)
async def analyze_prompt(req: AnalyzeRequest, state: State, _: Read) -> dict[str, Any]:
    if (req.text is None) == (req.messages is None):
        raise ApiError(422, "invalid_request", "provide exactly one of text or messages")
    a = await asyncio.to_thread(analyze, messages=req.messages, text=req.text, model=req.model)
    table = price_table(
        a.total_tokens, req.expected_completion_tokens, state.prices, requests=req.requests_per_month
    )
    current = state.prices.cost(
        req.model,
        Usage(a.total_tokens, req.expected_completion_tokens),
    )
    saved = state.prices.cost(
        req.model,
        Usage(a.optimized_tokens, req.expected_completion_tokens),
    )
    return {
        "model": req.model, "tokenizer_family": a.family, "estimated": a.estimated, "total_tokens": a.total_tokens, "optimized_tokens": a.optimized_tokens,
        "tokens_saved": a.tokens_saved, "saved_pct": a.saved_pct, "optimized_text": a.optimized_text,
        "segments": [asdict_seg(s) for s in a.segments], "findings": [asdict_find(f) for f in a.findings],
        "cost_per_request_micro": current.total, "optimized_cost_per_request_micro": saved.total,
        "monthly_savings_micro": (current.total - saved.total) * req.requests_per_month, "price_table": table[:12], "priced": current.priced,
    }  # fmt: skip


def asdict_seg(s: Any) -> dict[str, Any]:
    return {"role": s.role, "tokens": s.tokens, "share": s.share, "chars": s.chars}


def asdict_find(f: Any) -> dict[str, Any]:
    return {
        "kind": f.kind,
        "severity": f.severity,
        "title": f.title,
        "detail": f.detail,
        "tokens_saved": f.tokens_saved,
        "auto_fixable": f.auto_fixable,
        "examples": f.examples,
    }


# ── export and reports ───────────────────────────────────────────────────────────────────────────────────────
EXPORT_COLUMNS = ("id", "ts", "app", "environment", "provider", "model", "endpoint", "prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens", "cost_total", "saved_micro", "status", "error_type", "latency_ms", "prompt_id", "user_id", "estimated", "priced")  # fmt: skip


def _rows(state: AppState, f: Filters, limit: int) -> Iterator[dict[str, Any]]:
    cursor = None
    sent = 0
    while sent < limit:
        page = state.usage.list_events(f, min(1000, limit - sent), cursor)
        if not page:
            return
        yield from page
        sent += len(page)
        cursor = (page[-1]["ts"], page[-1]["id"])


def _csv_cell(v: Any) -> Any:
    """Neutralizes spreadsheet formula injection: a text cell that starts with = + - @ is prefixed with a quote."""
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@router.get("/export/events", summary="Download events as CSV or JSON Lines")
async def export_events(
    state: State,
    principal: Read,
    f: Scoped,
    format: Annotated[str, Query(pattern="^(csv|jsonl)$")] = "csv",
    limit: Annotated[int, Query(ge=1, le=1_000_000)] = 100_000,
) -> StreamingResponse:
    def gen() -> Iterator[str]:
        if format == "csv":
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(EXPORT_COLUMNS)
            yield buf.getvalue()
            for r in _rows(state, f, limit):
                buf.seek(0)
                buf.truncate()
                w.writerow([_csv_cell(r.get(c)) for c in EXPORT_COLUMNS])
                yield buf.getvalue()
        else:
            for r in _rows(state, f, limit):
                yield json.dumps({c: r.get(c) for c in EXPORT_COLUMNS}) + "\n"

    media = "text/csv" if format == "csv" else "application/x-ndjson"
    return StreamingResponse(
        gen(),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="tokenprofiler-events.{format}"'},
    )


@router.post("/reports/archive", summary="Archive a usage report (JSON) to the S3 reports bucket")
async def archive_report(
    state: State, principal: PriceWriter, f: Scoped, label: str = "manual"
) -> dict[str, Any]:
    if state.s3 is None or not state.settings.reports_bucket:
        raise ApiError(
            503, "reports_not_configured", "set TP_REPORTS_BUCKET (and AWS credentials) to archive reports"
        )
    rows, totals = await asyncio.gather(
        asyncio.to_thread(state.usage.aggregate, f, ["day", "app", "model"], ["requests", "prompt_tokens", "completion_tokens", "cached_tokens", "cost", "errors", "saved"], order_by="day", desc=False, limit=10_000),
        asyncio.to_thread(state.usage.aggregate, f, None, ["requests", "total_tokens", "cost", "errors", "saved"]),
    )  # fmt: skip
    day = time.strftime("%Y-%m-%d", time.gmtime())
    safe = "".join(c for c in label if c.isalnum() or c in "-_")[:40] or "manual"
    key = f"reports/{day}/usage-{safe}-{int(time.time())}.json"
    body = json.dumps(
        {
            "generated_at": now_ms(),
            "prices_as_of": state.prices.as_of,
            "filters": {"since": f.since, "until": f.until, "eq": f.eq},
            "totals": totals[0],
            "rows": rows,
        },
        indent=1,
    ).encode()
    try:
        await asyncio.to_thread(
            state.s3.put_object,
            Bucket=state.settings.reports_bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
    except Exception as exc:
        raise ApiError(
            502, "reports_unavailable", f"the reports bucket could not be written ({type(exc).__name__})"
        ) from exc
    await state.db.execute(
        "INSERT INTO reports (ts, period, bucket, key, bytes) VALUES (?,?,?,?,?)",
        time.time(),
        safe,
        state.settings.reports_bucket,
        key,
        len(body),
    )
    return {"bucket": state.settings.reports_bucket, "key": key, "bytes": len(body), "rows": len(rows)}


@router.get("/reports", summary="Archived reports")
async def list_reports(state: State, _: Read) -> dict[str, Any]:
    rows = await state.db.query("SELECT * FROM reports ORDER BY ts DESC LIMIT 100")
    return {"data": [dict(r) for r in rows]}


# ── keys and system ──────────────────────────────────────────────────────────────────────────────────────────
@router.get("/keys", summary="API keys (never the secrets)")
async def list_keys(state: State, _: Admin) -> dict[str, Any]:
    return {"data": [k.model_dump() for k in await state.keys.list()], "scopes": ["*", *ALL_SCOPES]}


@router.post("/keys", summary="Create an API key. The secret is shown once.", status_code=201)
async def create_key(body: KeyIn, state: State, _: Admin) -> ApiKeyCreated:
    try:
        return await state.keys.create(body.name, body.scopes, apps=body.apps)
    except ValueError as exc:
        raise ApiError(422, "invalid_scope", str(exc)) from exc


@router.delete("/keys/{prefix}", summary="Revoke an API key", status_code=204)
async def revoke_key(prefix: str, state: State, principal: Admin) -> Response:
    if prefix == principal.prefix:
        raise ApiError(409, "cannot_revoke_self", "revoke the key you are using from another key")
    if not await state.keys.revoke(prefix):
        raise ApiError(404, "key_not_found", f"no key with prefix {prefix!r}")
    return Response(status_code=204)


@router.get("/system", summary="Version, storage and feature flags")
async def system(state: State, _: Read) -> dict[str, Any]:
    s = state.settings
    return {
        "version": __version__, "bounds": await asyncio.to_thread(state.usage.bounds), "prices_as_of": state.prices.as_of, "price_overrides": sorted(state.prices.overridden),
        "retention_days": s.retention_days, "limits": {"max_batch_events": s.max_batch_events, "max_request_bytes": s.max_request_bytes, "ingest_rate_limit_rpm": s.ingest_rate_limit_rpm},
        "features": {"proxy": bool(s.upstream_base_url), "reports": bool(s.reports_bucket and state.s3), "reports_bucket": s.reports_bucket, "derive_prompt_ids": s.derive_prompt_ids},
        "proxy_upstream": s.upstream_base_url,
    }  # fmt: skip


__all__ = ["ApiKeyRecord", "router"]
