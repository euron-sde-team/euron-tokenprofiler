"""Month-end spend projection from the daily series."""

from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime

DAY_MS = 86_400_000


@dataclass(frozen=True, slots=True)
class Forecast:
    month_to_date: int  # micro-dollars
    run_rate_per_day: float  # trailing average of full days
    projected_month_total: int
    days_elapsed: float
    days_in_month: int
    trend_pct: float | None  # last 7 days vs the 7 before, as a ratio - 1
    confidence: str  # "low" with under a week of data, "medium" under two, else "high"


def month_bounds(now_ms: int) -> tuple[int, int, int]:
    """(start of the month in ms, start of the next month in ms, days in the month), UTC."""
    d = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    start = datetime(d.year, d.month, 1, tzinfo=UTC)
    days = calendar.monthrange(d.year, d.month)[1]
    return int(start.timestamp() * 1000), int(start.timestamp() * 1000) + days * DAY_MS, days


def project(daily: dict[int, int], now_ms: int) -> Forecast:
    """``daily`` maps day number (days since the epoch, UTC) to micro-dollars of spend.

    Projection = spend so far this month + the trailing run rate for the rest of the month. The run rate is the median
    of the last up-to-7 *completed* days (the median, so one spike does not inflate the month), falling back to the
    average of whatever there is.
    """
    start, _end, days_in_month = month_bounds(now_ms)
    today = now_ms // DAY_MS
    first_day = start // DAY_MS
    mtd = sum(v for d, v in daily.items() if first_day <= d <= today)
    elapsed = (now_ms - start) / DAY_MS
    completed = [daily.get(d, 0) for d in range(max(min(daily, default=today), today - 7), today)]
    history = sorted(d for d in daily if d < today)
    if completed and history:
        run_rate = float(statistics.median(completed))
    else:
        run_rate = (mtd / elapsed) if elapsed > 0 else 0.0
    remaining = max(0.0, days_in_month - elapsed)
    projected = round(mtd + run_rate * remaining)

    last7 = sum(daily.get(d, 0) for d in range(today - 7, today))
    prev7 = sum(daily.get(d, 0) for d in range(today - 14, today - 7))
    span = (today - history[0]) if history else 0
    trend = (
        (last7 / prev7 - 1) if prev7 > 0 and span >= 14 else None
    )  # a half-empty comparison window would invent growth
    confidence = "high" if span >= 14 else "medium" if span >= 7 else "low"
    return Forecast(
        mtd,
        run_rate,
        projected,
        round(elapsed, 2),
        days_in_month,
        None if trend is None else round(trend, 3),
        confidence,
    )
