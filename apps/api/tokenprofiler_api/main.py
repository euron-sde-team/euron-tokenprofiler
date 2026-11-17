"""Application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from tokenprofiler_core.events import now_ms
from tokenprofiler_core.pricing import ModelPrice, PriceBook
from tokenprofiler_core.store import DAY_MS, UsageStore

from . import __version__, alerts
from .auth import ApiKeyStore, RateLimiter
from .cache import TTLCache
from .db import Database
from .errors import ApiError, error_response
from .logging_config import configure_logging
from .metrics import ApiMetrics
from .routers import admin, analytics, ingest, insights, proxy
from .settings import DEV_PEPPER, ApiSettings, apply_aws_secrets, get_settings
from .state import AppState

log = logging.getLogger("tokenprofiler.api")

DESCRIPTION = """
**Euron TokenProfiler** tracks, analyzes and visualizes token usage and cost across models, prompts and applications.
Send usage with `POST /v1/events`, or route calls through the OpenAI-compatible `/proxy/v1`, then read it back through
the analytics, optimizer and budget endpoints. Authenticate with `Authorization: Bearer <key>` or `X-API-Key: <key>`.
"""
TAGS = [
    {"name": "ingest", "description": "Record usage events; estimate tokens and cost"},
    {"name": "proxy", "description": "OpenAI-compatible proxy that records every call"},
    {"name": "analytics", "description": "Spend, tokens, latency, prompts, models, anomalies"},
    {"name": "insights", "description": "Optimizer recommendations, budgets and alerts"},
    {"name": "admin", "description": "Pricing, the Prompt Lab, exports, reports, API keys"},
    {"name": "ops", "description": "Health and Prometheus metrics"},
]
HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "invalid",
    429: "rate_limited",
    503: "unavailable",
}


def make_s3(settings: ApiSettings) -> Any | None:
    if not settings.reports_bucket:
        return None
    import boto3

    return boto3.client(
        "s3", region_name=settings.aws_region or "us-east-1", endpoint_url=settings.aws_endpoint_url
    )


async def _retention(state: AppState) -> None:
    while True:
        cutoff = now_ms() - state.settings.retention_days * DAY_MS
        deleted = await asyncio.to_thread(state.usage.delete_before, cutoff)
        if deleted:
            log.info(
                "retention: deleted %d events older than %d days", deleted, state.settings.retention_days
            )
        await asyncio.sleep(24 * 3600)


def create_app(
    settings: ApiSettings | None = None, *, http_client: httpx.AsyncClient | None = None, s3_client: Any | None = None, secrets_client: Any | None = None,
    prices: PriceBook | None = None,
) -> FastAPI:  # fmt: skip
    cfg = apply_aws_secrets(settings or get_settings(), secrets_client)
    configure_logging(cfg.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(cfg.data_path / "control.db")
        usage = UsageStore(cfg.data_path / "usage.db")
        book = prices or (PriceBook.from_file(cfg.pricing_file) if cfg.pricing_file else PriceBook())
        for row in await db.query("SELECT body FROM price_overrides"):
            book.set(ModelPrice.model_validate_json(row["body"]))
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(10, read=cfg.upstream_timeout_s), follow_redirects=False
        )
        state = AppState(settings=cfg, db=db, usage=usage, keys=ApiKeyStore(db, cfg.api_key_pepper), prices=book, metrics=ApiMetrics(), limiter=RateLimiter(cfg.ingest_rate_limit_rpm), http=client, cache=TTLCache(cfg.cache_ttl_s), s3=s3_client if s3_client is not None else make_s3(cfg))  # fmt: skip
        app.state.tp = state
        app.state.keys = state.keys
        if cfg.api_key_pepper == DEV_PEPPER:
            log.warning(
                "TP_API_KEY_PEPPER is the development default: set a secret value before exposing this server"
            )
        if cfg.bootstrap_api_key:
            await state.keys.create("bootstrap", ["*"], key=cfg.bootstrap_api_key)
            log.info("bootstrap API key installed")
        tasks = [
            asyncio.create_task(alerts.loop(state), name="alerts"),
            asyncio.create_task(_retention(state), name="retention"),
        ]
        log.info(
            "ready: %d events stored, prices as of %s, proxy=%s",
            (await asyncio.to_thread(usage.bounds))["events"],
            book.as_of,
            "on" if cfg.upstream_base_url else "off",
        )
        yield
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if http_client is None:
            await client.aclose()
        usage.close()
        db.close()

    app = FastAPI(title="Euron TokenProfiler API", version=__version__, description=DESCRIPTION, openapi_tags=TAGS, lifespan=lifespan, license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"})  # fmt: skip
    app.add_middleware(
        CORSMiddleware, allow_origins=cfg.cors_origins, allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID", "X-Upstream-Api-Key", "X-TP-App", "X-TP-Environment", "X-TP-Prompt-Id", "X-TP-User", "X-TP-Session", "X-TP-Tags"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )  # fmt: skip

    @app.middleware("http")
    async def observe(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cfg.max_request_bytes:
            response: Response = error_response(
                413,
                "payload_too_large",
                f"request bodies are limited to {cfg.max_request_bytes} bytes",
                request,
            )
        else:
            response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = getattr(request.scope.get("route"), "path", request.url.path)
        quiet = route in ("/metrics", "/healthz", "/readyz")
        state: AppState | None = getattr(request.app.state, "tp", None)
        if state and not quiet:
            state.metrics.requests.labels(request.method, route, str(response.status_code)).inc()
            state.metrics.latency.labels(request.method, route).observe(elapsed)
        response.headers["X-Request-ID"] = request.state.request_id
        if not quiet:
            log.info("%s %s -> %s", request.method, request.url.path, response.status_code, extra={"request_id": request.state.request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "duration_ms": round(elapsed * 1000, 1)})  # fmt: skip
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, request, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return error_response(
            exc.status_code, HTTP_CODES.get(exc.status_code, "error"), str(exc.detail), request
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p not in ("body", "query"))
        return error_response(
            422, "invalid_request", f"{loc}: {first.get('msg', 'invalid request')}".strip(": "), request
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", extra={"request_id": getattr(request.state, "request_id", None)})
        return error_response(500, "internal", "internal server error", request)

    for r in (ingest.router, proxy.router, analytics.router, insights.router, admin.router):
        app.include_router(r)

    @app.get("/healthz", tags=["ops"], summary="Liveness")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", tags=["ops"], summary="Readiness: both databases usable")
    async def readyz(request: Request) -> dict[str, Any]:
        state: AppState = request.app.state.tp
        try:
            await asyncio.to_thread(state.db.ping)
            await asyncio.to_thread(state.usage.ping)
        except Exception as exc:
            raise HTTPException(503, "storage unavailable") from exc
        return {"status": "ready", "version": __version__, "proxy": bool(state.settings.upstream_base_url)}

    @app.get("/metrics", tags=["ops"], summary="Prometheus metrics")
    async def metrics_endpoint(request: Request) -> Response:
        return Response(request.app.state.tp.metrics.render(), media_type="text/plain; version=0.0.4")

    return app


__all__ = ["contextlib", "create_app"]
