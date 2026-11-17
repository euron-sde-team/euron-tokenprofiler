"""Optimizer recommendations, alerts, budgets."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from tokenprofiler_core import recommend
from tokenprofiler_core.budgets import BudgetIn
from tokenprofiler_core.events import now_ms

from .. import alerts
from ..auth import Principal, require
from ..errors import ApiError
from ..state import State
from ..webhooks import WebhookError, check_url
from .common import Read, Scoped

router = APIRouter(prefix="/api", tags=["insights"])
BudgetWriter = Annotated[Principal, Depends(require("budgets:write"))]


@router.get("/recommendations", summary="Specific, quantified ways to cut spend, from your real usage")
async def recommendations(
    request: Request,
    state: State,
    principal: Read,
    f: Scoped,
    include_dismissed: bool = False,
    window_days: Annotated[int, Query(ge=3, le=180)] = 30,
) -> dict[str, Any]:
    import asyncio

    async def compute() -> dict[str, Any]:
        out: dict[str, Any] = await asyncio.to_thread(
            recommend.analyze,
            state.usage,
            state.prices,
            now_ms=now_ms(),
            settings=recommend.Settings(window_days=window_days),
            base=f,
        )
        return out

    result = await state.cache.get_or_compute(state.cache_key(request, principal), compute)
    dismissed = {r["rec_id"] for r in await state.db.query("SELECT rec_id FROM dismissed")}
    recs = [{**asdict(r), "dismissed": r.id in dismissed} for r in result["recommendations"]]
    if not include_dismissed:
        recs = [r for r in recs if not r["dismissed"]]
    return {
        **{k: v for k, v in result.items() if k != "recommendations"},
        "recommendations": recs,
        "dismissed_count": len(dismissed),
    }


@router.post(
    "/recommendations/{rec_id}/dismiss",
    summary="Hide a recommendation you have decided against",
    status_code=204,
)
async def dismiss(
    rec_id: str, state: State, principal: Annotated[Principal, Depends(require("budgets:write"))]
) -> None:
    if not rec_id.isalnum() or len(rec_id) > 32:
        raise ApiError(422, "invalid_id", "not a recommendation id")
    await state.db.execute(
        "INSERT INTO dismissed (rec_id, ts) VALUES (?, ?) ON CONFLICT(rec_id) DO NOTHING", rec_id, time.time()
    )


@router.delete(
    "/recommendations/{rec_id}/dismiss", summary="Bring a dismissed recommendation back", status_code=204
)
async def restore(rec_id: str, state: State, principal: BudgetWriter) -> None:
    await state.db.execute("DELETE FROM dismissed WHERE rec_id = ?", rec_id)


# ── alerts ───────────────────────────────────────────────────────────────────────────────────────────────────
def _alert_visible(principal: Principal, row: Any, detail: dict[str, Any], visible_budgets: set[int]) -> bool:
    if not principal.apps:
        return True
    if row["budget_id"] is not None:
        return int(row["budget_id"]) in visible_budgets
    return detail.get("app") in principal.apps


@router.get("/alerts", summary="Budget and anomaly alerts, newest first")
async def list_alerts(
    state: State,
    principal: Read,
    include_acknowledged: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    if include_acknowledged:
        rows = await state.db.query("SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", limit * 4)
    else:
        rows = await state.db.query(
            "SELECT * FROM alerts WHERE acknowledged = 0 ORDER BY ts DESC LIMIT ?", limit * 4
        )
    visible = {
        int(r["id"]) for r in await state.db.query("SELECT * FROM budgets") if _budget_visible(principal, r)
    }
    out = []
    for r in rows:
        detail = json.loads(r["detail"])
        if _alert_visible(principal, r, detail, visible):
            out.append(
                {
                    **dict(r),
                    "detail": detail,
                    "acknowledged": bool(r["acknowledged"]),
                    "delivered": bool(r["delivered"]),
                }
            )
    return {"data": out[:limit]}


@router.post("/alerts/{alert_id}/ack", summary="Acknowledge an alert", status_code=204)
async def ack(alert_id: int, state: State, principal: BudgetWriter) -> None:
    if not await state.db.execute("UPDATE alerts SET acknowledged = 1 WHERE id = ?", alert_id):
        raise ApiError(404, "alert_not_found", "no such alert")


@router.post("/alerts/evaluate", summary="Evaluate budgets and anomalies now")
async def evaluate_now(state: State, principal: BudgetWriter) -> dict[str, int]:
    return {"raised": await alerts.evaluate_once(state, now_ms())}


# ── budgets ──────────────────────────────────────────────────────────────────────────────────────────────────
async def _public(state: State, row: Any) -> dict[str, Any]:
    b, st, fc = await alerts.budget_status(state, row, now_ms())
    return {"id": row["id"], "name": b.name, "limit_micro": row["limit_micro"], "scope": b.scope, "warn_at": b.warn_at, "webhook_url": b.webhook_url,
            "status": st.model_dump(), "forecast_confidence": fc.confidence, "created_at": row["created_at"]}  # fmt: skip


def _check_scope(principal: Principal, b: BudgetIn) -> None:
    if principal.apps and b.scope.get("app") not in principal.apps:
        raise ApiError(
            403,
            "app_not_allowed",
            f"this API key may only manage budgets scoped to its apps: {', '.join(principal.apps)}",
        )


async def _own(state: State, principal: Principal, budget_id: int) -> None:
    rows = await state.db.query("SELECT * FROM budgets WHERE id = ?", budget_id)
    if rows and not _budget_visible(principal, rows[0]):
        raise ApiError(404, "budget_not_found", "no such budget")  # indistinguishable from a missing one


def _check_hook(state: State, b: BudgetIn) -> None:
    if b.webhook_url:
        try:
            check_url(b.webhook_url, allow_private=state.settings.allow_private_webhooks, lookup=False)
        except WebhookError as exc:
            raise ApiError(422, "invalid_webhook", str(exc)) from exc


def _budget_visible(principal: Principal, row: Any) -> bool:
    """A key limited to some apps sees only budgets scoped to those apps. An unscoped budget reveals total spend across every
    app, so it is hidden from restricted keys."""
    if not principal.apps:
        return True
    return json.loads(row["scope"]).get("app") in principal.apps


@router.get("/budgets", summary="Budgets with live status and forecast")
async def list_budgets(request: Request, state: State, principal: Read) -> dict[str, Any]:
    async def compute() -> dict[str, Any]:
        rows = [
            r
            for r in await state.db.query("SELECT * FROM budgets ORDER BY id")
            if _budget_visible(principal, r)
        ]
        return {"data": [await _public(state, r) for r in rows]}

    result: dict[str, Any] = await state.cache.get_or_compute(state.cache_key(request, principal), compute)
    return result


@router.post("/budgets", summary="Create a monthly budget", status_code=201)
async def create_budget(b: BudgetIn, state: State, principal: BudgetWriter) -> dict[str, Any]:
    _check_hook(state, b)
    _check_scope(principal, b)
    now = time.time()
    state.cache.clear()
    bid = await state.db.insert("INSERT INTO budgets (name, limit_micro, scope, warn_at, webhook_url, created_at, updated_at) VALUES (?,?,?,?,?,?,?)", b.name, round(b.limit_usd * 1e6), json.dumps(b.scope), b.warn_at, b.webhook_url, now, now)  # fmt: skip
    state.cache.clear()  # again after the write: a list computed while it was happening must not outlive it
    return await _public(state, (await state.db.query("SELECT * FROM budgets WHERE id = ?", bid))[0])


@router.put("/budgets/{budget_id}", summary="Replace a budget")
async def update_budget(budget_id: int, b: BudgetIn, state: State, principal: BudgetWriter) -> dict[str, Any]:
    _check_hook(state, b)
    _check_scope(principal, b)
    await _own(state, principal, budget_id)
    state.cache.clear()
    n = await state.db.execute("UPDATE budgets SET name=?, limit_micro=?, scope=?, warn_at=?, webhook_url=?, updated_at=? WHERE id=?", b.name, round(b.limit_usd * 1e6), json.dumps(b.scope), b.warn_at, b.webhook_url, time.time(), budget_id)  # fmt: skip
    if not n:
        raise ApiError(404, "budget_not_found", "no such budget")
    state.cache.clear()
    return await _public(state, (await state.db.query("SELECT * FROM budgets WHERE id = ?", budget_id))[0])


@router.delete("/budgets/{budget_id}", summary="Delete a budget", status_code=204)
async def delete_budget(budget_id: int, state: State, principal: BudgetWriter) -> Response:
    await _own(state, principal, budget_id)
    state.cache.clear()
    if not await state.db.execute("DELETE FROM budgets WHERE id = ?", budget_id):
        raise ApiError(404, "budget_not_found", "no such budget")
    state.cache.clear()
    return Response(status_code=204)
