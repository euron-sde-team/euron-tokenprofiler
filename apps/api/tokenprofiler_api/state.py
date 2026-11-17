"""The application's shared services, built once at startup and reached through ``app.state.tp``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

import httpx
from fastapi import Depends, Request
from tokenprofiler_core.pricing import PriceBook
from tokenprofiler_core.query import Filters
from tokenprofiler_core.store import UsageStore

from .auth import ApiKeyStore, Principal, RateLimiter
from .db import Database
from .metrics import ApiMetrics
from .settings import ApiSettings


@dataclass
class AppState:
    settings: ApiSettings
    db: Database
    usage: UsageStore
    keys: ApiKeyStore
    prices: PriceBook
    metrics: ApiMetrics
    limiter: RateLimiter
    http: httpx.AsyncClient
    s3: Any | None = None

    def scoped(self, principal: Principal, f: Filters) -> Filters:
        """Narrows filters to the apps this key may see. A key with no app list sees everything."""
        if principal.apps:
            f.within["app"] = [a for a in principal.apps if "app" not in f.eq or f.eq["app"] == a]
        return f


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.tp
    return state


State = Annotated[AppState, Depends(get_state)]
