"""Shared query-parameter handling: time ranges, filters, and per-key app scoping."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import Depends, Query
from tokenprofiler_core.events import now_ms
from tokenprofiler_core.query import Filters
from tokenprofiler_core.store import DAY_MS, HOUR_MS

from ..auth import Principal, require
from ..errors import ApiError
from ..state import AppState, State

RANGES = {
    "1h": HOUR_MS,
    "6h": 6 * HOUR_MS,
    "24h": DAY_MS,
    "7d": 7 * DAY_MS,
    "14d": 14 * DAY_MS,
    "30d": 30 * DAY_MS,
    "90d": 90 * DAY_MS,
    "180d": 180 * DAY_MS,
    "365d": 365 * DAY_MS,
}
TAG_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]{0,31}):(.{1,64})$")
Read = Annotated[Principal, Depends(require("read"))]


def range_ms(name: str) -> int:
    if name not in RANGES:
        raise ApiError(422, "invalid_range", f"range must be one of {', '.join(RANGES)}")
    return RANGES[name]


def query_filters(
    state: State, principal: Read,
    range: Annotated[str, Query(description="Look-back window: 1h 6h 24h 7d 14d 30d 90d 180d 365d")] = "30d",
    since: Annotated[int | None, Query(description="Epoch ms; overrides range")] = None,
    until: Annotated[int | None, Query(description="Epoch ms, exclusive")] = None,
    app: str | None = None, environment: str | None = None, model: str | None = None, provider: str | None = None,
    endpoint: str | None = None, prompt_id: str | None = None, user_id: str | None = None, status: str | None = None,
    tag: Annotated[list[str] | None, Query(description="Repeatable key:value tag filter")] = None,
) -> Filters:  # fmt: skip
    end = until if until is not None else None
    start = since if since is not None else now_ms() - range_ms(range)
    eq = {
        k: v
        for k, v in (
            ("app", app),
            ("environment", environment),
            ("model", model),
            ("provider", provider),
            ("endpoint", endpoint),
            ("prompt_id", prompt_id),
            ("user_id", user_id),
            ("status", status),
        )
        if v
    }
    tags: dict[str, str] = {}
    for t in tag or []:
        m = TAG_RE.match(t)
        if not m:
            raise ApiError(422, "invalid_tag", f"tag filters look like key:value, got {t!r}")
        tags[m.group(1)] = m.group(2)
    f = Filters(since=start, until=end, eq=eq, tags=tags)
    if principal.apps:
        if app and app not in principal.apps:
            raise ApiError(403, "app_not_allowed", f"this API key may not read app {app!r}")
        f.within["app"] = list(principal.apps)
    return f


Scoped = Annotated[Filters, Depends(query_filters)]


def check_app(principal: Principal, app: str) -> None:
    if not principal.may_use_app(app):
        raise ApiError(403, "app_not_allowed", f"this API key may not use app {app!r}")


def scoped(state: AppState, principal: Principal, f: Filters) -> Filters:
    return state.scoped(principal, f)
