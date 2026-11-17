"""Alert evaluation: budget thresholds and spend anomalies. Idempotent: every alert has a dedupe key, so evaluating again
(or on a second replica) raises each one at most once. Delivery to a webhook is best-effort and retried on the next pass."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from tokenprofiler_core import anomaly, forecast
from tokenprofiler_core.budgets import BudgetIn, BudgetStatus, evaluate
from tokenprofiler_core.money import format_usd
from tokenprofiler_core.query import Filters
from tokenprofiler_core.store import DAY_MS

from .state import AppState
from .webhooks import WebhookError, check_url

log = logging.getLogger("tokenprofiler.alerts")


async def budget_status(
    state: AppState, row: Any, now: int
) -> tuple[BudgetIn, BudgetStatus, forecast.Forecast]:
    scope = json.loads(row["scope"])
    b = BudgetIn(
        name=row["name"],
        limit_usd=row["limit_micro"] / 1e6,
        scope=scope,
        warn_at=row["warn_at"],
        webhook_url=row["webhook_url"],
    )
    start, end, _ = forecast.month_bounds(now)
    f = b.filters(
        start - 14 * DAY_MS, end
    )  # two weeks before the month, so the run rate has history on the 1st
    daily = await asyncio.to_thread(
        state.usage.aggregate, f, ["day"], ["cost"], order_by="day", desc=False, limit=100
    )
    fc = forecast.project({int(r["day"]): int(r["cost"]) for r in daily}, now)
    return (
        b,
        evaluate(
            int(row["limit_micro"]),
            b.warn_at,
            fc.month_to_date,
            fc.projected_month_total,
            fc.run_rate_per_day,
        ),
        fc,
    )


async def _raise(
    state: AppState,
    *,
    kind: str,
    severity: str,
    dedupe: str,
    title: str,
    detail: dict[str, Any],
    budget_id: int | None,
    webhook: str | None,
) -> bool:
    """Stores an alert if it is new. Returns True when it was new."""
    inserted = await state.db.execute(
        "INSERT INTO alerts (ts, kind, severity, dedupe, title, detail, budget_id) VALUES (?,?,?,?,?,?,?) ON CONFLICT(dedupe) DO NOTHING",
        time.time(), kind, severity, dedupe, title, json.dumps(detail), budget_id,
    )  # fmt: skip
    if inserted:
        state.metrics.alerts.labels(kind).inc()
        log.warning("alert: %s", title)
        if webhook:
            await deliver(
                state, dedupe, webhook, {"kind": kind, "severity": severity, "title": title, "detail": detail}
            )
    return bool(inserted)


async def deliver(state: AppState, dedupe: str, url: str, payload: dict[str, Any]) -> bool:
    try:
        check_url(
            url, allow_private=state.settings.allow_private_webhooks
        )  # re-resolved now: DNS may have changed since the budget was saved
        r = await state.http.post(url, json=payload, timeout=5)
        ok = r.status_code < 300
    except (WebhookError, httpx.HTTPError) as exc:
        log.warning("webhook delivery failed: %s", exc)
        ok = False
    if ok:
        await state.db.execute("UPDATE alerts SET delivered = 1 WHERE dedupe = ?", dedupe)
    return ok


async def evaluate_once(state: AppState, now: int) -> int:
    """One pass over budgets and anomalies. Returns the number of new alerts."""
    raised = 0
    month = time.strftime("%Y-%m", time.gmtime(now / 1000))
    for row in await state.db.query("SELECT * FROM budgets"):
        b, st, fc = await budget_status(state, row, now)
        if st.status == "ok":
            continue
        sev = {"warning": "warning", "projected_overrun": "warning", "exceeded": "critical"}[st.status]
        title = {
            "warning": f"Budget '{b.name}' is at {st.used_pct:.0f}% ({format_usd(st.spent_micro)} of {format_usd(st.limit_micro)})",
            "projected_overrun": f"Budget '{b.name}' is on track to reach {format_usd(st.projected_micro)}, over its {format_usd(st.limit_micro)} limit",
            "exceeded": f"Budget '{b.name}' is exceeded: {format_usd(st.spent_micro)} of {format_usd(st.limit_micro)}",
        }[st.status]
        if st.status == "projected_overrun" and fc.confidence == "low":
            continue  # under a week of data: a projection would be noise
        detail = {"budget_id": row["id"], **st.model_dump()}
        raised += await _raise(
            state,
            kind="budget",
            severity=sev,
            dedupe=f"budget:{row['id']}:{month}:{st.status}",
            title=title,
            detail=detail,
            budget_id=row["id"],
            webhook=b.webhook_url,
        )
    today = now // DAY_MS
    f = Filters(since=now - 45 * DAY_MS)
    per = await asyncio.to_thread(
        state.usage.aggregate, f, ["day", "app"], ["cost"], order_by="day", desc=False, limit=10_000
    )
    series: dict[str, dict[int, float]] = {}
    for r in per:
        series.setdefault(str(r["app"]), {})[int(r["day"])] = float(r["cost"])
    for app, days in series.items():
        pts = [(d, days.get(d, 0.0)) for d in range(min(days), today)]  # completed days only
        for a in anomaly.detect(pts, floor=100_000)[-2:]:
            if a.direction != "spike" or a.day < today - 2:
                continue
            title = f"Spend spike in {app}: {format_usd(int(a.value))} vs about {format_usd(int(a.expected))} usual ({a.ratio:.1f}x)"
            raised += await _raise(
                state,
                kind="anomaly",
                severity="warning",
                dedupe=f"anomaly:{app}:{a.day}",
                title=title,
                detail={
                    "app": app,
                    "day": a.day,
                    "value_micro": a.value,
                    "expected_micro": a.expected,
                    "ratio": round(a.ratio, 2),
                },
                budget_id=None,
                webhook=None,
            )
    return raised


async def loop(state: AppState) -> None:
    while True:
        await asyncio.sleep(state.settings.alert_interval_s)
        try:
            await evaluate_once(state, int(time.time() * 1000))
        except Exception:
            log.exception("alert evaluation failed")
