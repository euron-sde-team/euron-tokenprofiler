"""The profiling proxy: an OpenAI-compatible endpoint that forwards to your provider and records what it cost.

Point an OpenAI SDK's ``base_url`` at ``/proxy/v1`` and send your TokenProfiler key as the API key; the provider key goes in
``X-Upstream-Api-Key`` (or is configured on the server). Optional ``X-TP-*`` headers label the call. Recording is
best-effort and detached from the response: a failure to record can never fail, slow down or alter the caller's request.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send
from tokenprofiler_core.events import EventIn
from tokenprofiler_core.ingest import resolve
from tokenprofiler_core.tokens import count_messages, estimate, family_for_model

from ..auth import Principal, require
from ..errors import ApiError
from ..state import AppState, State
from .common import check_app

router = APIRouter(prefix="/proxy/v1", tags=["proxy"])
Ingest = Annotated[Principal, Depends(require("ingest"))]
_background: set[asyncio.Task[None]] = set()


def _labels(h: Request | dict[str, str], principal: Principal) -> dict[str, Any]:
    get = h.headers.get if isinstance(h, Request) else h.get
    app = get("x-tp-app") or (principal.apps[0] if len(principal.apps) == 1 else "default")
    tags = {}
    for part in (get("x-tp-tags") or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            tags[k.strip()] = v.strip()
    return {
        "app": app, "environment": get("x-tp-environment") or "production", "prompt_id": get("x-tp-prompt-id"), "user_id": get("x-tp-user"),
        "session_id": get("x-tp-session"), "tags": tags,
    }  # fmt: skip


def _usage(body: dict[str, Any]) -> tuple[int | None, int | None, int, int]:
    u = body.get("usage") or {}
    details = u.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or 0)
    reasoning = int((u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)
    return u.get("prompt_tokens"), u.get("completion_tokens"), cached, reasoning


async def _store(state: AppState, event: dict[str, Any]) -> None:
    try:
        e = EventIn.model_validate(event)
        stored = await asyncio.to_thread(resolve, e, state.prices)
        await asyncio.to_thread(state.usage.record, [stored])
        state.metrics.ingested.labels("proxy").inc()
    except Exception:
        state.metrics.record_failures.inc()


def _detach(state: AppState, event: dict[str, Any]) -> None:
    task = asyncio.ensure_future(_store(state, event))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _upstream(state: AppState, request: Request, path: str) -> tuple[str, dict[str, str]]:
    base = state.settings.upstream_base_url
    if not base:
        raise ApiError(503, "proxy_not_configured", "set TP_UPSTREAM_BASE_URL to use the profiling proxy")
    headers = {"Content-Type": "application/json"}
    key = request.headers.get("x-upstream-api-key") or state.settings.upstream_api_key
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return f"{base.rstrip('/')}{path}", headers


def _event(labels: dict[str, Any], model: str, body: dict[str, Any], resp: dict[str, Any] | None, *, latency_ms: float, ttft_ms: float | None, stream: bool,
           status: int, error_type: str | None, completion_text: str = "", endpoint: str = "chat") -> dict[str, Any]:  # fmt: skip
    prompt, completion, cached, reasoning = _usage(resp or {})
    ok = status < 400
    est_prompt = None
    if prompt is None:
        est_prompt = (
            count_messages(body.get("messages") or [], model).tokens
            if endpoint == "chat"
            else estimate(json.dumps(body.get("input", "")), family_for_model(model))
        )
    ev: dict[str, Any] = {
        "id": uuid.uuid4().hex, "model": (resp or {}).get("model") or model, "endpoint": endpoint, "latency_ms": round(latency_ms, 1), "stream": stream,
        "status": "ok" if ok else "error", "error_type": None if ok else (error_type or f"http_{status}"), "http_status": status if not ok else None,
        "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens"), "temperature": body.get("temperature"), "cached_tokens": cached,
        "reasoning_tokens": reasoning, **{k: v for k, v in labels.items() if v}, "tags": labels.get("tags") or {},
    }  # fmt: skip
    if ttft_ms is not None:
        ev["ttft_ms"] = round(ttft_ms, 1)
    choice = ((resp or {}).get("choices") or [{}])[0]
    if choice.get("finish_reason"):
        ev["finish_reason"] = choice["finish_reason"]
    if prompt is not None:
        ev["prompt_tokens"] = int(prompt)
    elif est_prompt is not None:
        ev["prompt_tokens"] = est_prompt
    if completion is not None:
        ev["completion_tokens"] = int(completion)
    elif ok:
        ev["completion_text"] = completion_text or " "
    else:
        ev["completion_tokens"] = 0
    if prompt is None or (completion is None and ok):
        ev["tags"] = {**ev["tags"], "usage": "estimated"}
    ev["cached_tokens"] = min(ev["cached_tokens"], ev.get("prompt_tokens", 0))
    return ev


class ClosingStreamingResponse(StreamingResponse):
    """Always closes its generator so the final event is recorded even when the client disconnects mid-stream."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.body_iterator.aclose()  # type: ignore[attr-defined]


@router.post("/chat/completions", summary="OpenAI-compatible chat completions, recorded")
async def chat(request: Request, state: State, principal: Ingest) -> Response:
    if not state.limiter.allow(f"proxy:{principal.prefix}"):
        raise ApiError(429, "rate_limited", "rate limit exceeded for this key")
    raw = await request.body()
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_json", "the request body is not valid JSON") from exc
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("model"), str)
        or not isinstance(body.get("messages"), list)
    ):
        raise ApiError(422, "invalid_request", "model (string) and messages (list) are required")
    labels = _labels(request, principal)
    check_app(principal, labels["app"])
    url, headers = _upstream(state, request, "/chat/completions")
    model: str = body["model"]
    wants_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    start = time.perf_counter()

    if not body.get("stream"):
        try:
            r = await state.http.post(
                url, content=raw, headers=headers, timeout=state.settings.upstream_timeout_s
            )
        except httpx.HTTPError as exc:
            lat = (time.perf_counter() - start) * 1000
            _detach(
                state,
                _event(
                    labels,
                    model,
                    body,
                    None,
                    latency_ms=lat,
                    ttft_ms=None,
                    stream=False,
                    status=502,
                    error_type=type(exc).__name__,
                ),
            )
            state.metrics.proxied.labels("502").inc()
            raise ApiError(
                502, "upstream_unreachable", f"the provider could not be reached ({type(exc).__name__})"
            ) from exc
        lat = (time.perf_counter() - start) * 1000
        try:
            payload = r.json()
        except ValueError:
            payload = None
        err = ((payload or {}).get("error") or {}).get("type") if isinstance(payload, dict) else None
        text = ""
        if isinstance(payload, dict) and r.status_code < 400:
            text = str(((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
        _detach(
            state,
            _event(
                labels,
                model,
                body,
                payload if isinstance(payload, dict) else None,
                latency_ms=lat,
                ttft_ms=None,
                stream=False,
                status=r.status_code,
                error_type=err,
                completion_text=text,
            ),
        )
        state.metrics.proxied.labels(str(r.status_code)).inc()
        return Response(
            r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json")
        )

    fwd = {
        **body,
        "stream_options": {**(body.get("stream_options") or {}), "include_usage": True},
    }  # always ask, so usage is exact
    upstream_req = state.http.build_request(
        "POST", url, json=fwd, headers=headers, timeout=state.settings.upstream_timeout_s
    )
    try:
        r = await state.http.send(upstream_req, stream=True)
    except httpx.HTTPError as exc:
        lat = (time.perf_counter() - start) * 1000
        _detach(
            state,
            _event(
                labels,
                model,
                body,
                None,
                latency_ms=lat,
                ttft_ms=None,
                stream=True,
                status=502,
                error_type=type(exc).__name__,
            ),
        )
        state.metrics.proxied.labels("502").inc()
        raise ApiError(
            502, "upstream_unreachable", f"the provider could not be reached ({type(exc).__name__})"
        ) from exc
    if r.status_code >= 400:
        content = await r.aread()
        await r.aclose()
        try:
            payload = json.loads(content)
        except ValueError:
            payload = None
        err = ((payload or {}).get("error") or {}).get("type") if isinstance(payload, dict) else None
        _detach(
            state,
            _event(
                labels,
                model,
                body,
                None,
                latency_ms=(time.perf_counter() - start) * 1000,
                ttft_ms=None,
                stream=True,
                status=r.status_code,
                error_type=err,
            ),
        )
        state.metrics.proxied.labels(str(r.status_code)).inc()
        return Response(
            content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json")
        )

    async def events() -> AsyncIterator[bytes]:
        ttft: float | None = None
        usage_chunk: dict[str, Any] | None = None
        finish: str | None = None
        text: list[str] = []
        model_seen = model
        status, error_type = 200, None
        buffer = b""
        try:
            async for chunk in r.aiter_raw():
                buffer += chunk
                out = b""
                while b"\n\n" in buffer:
                    block, buffer = buffer.split(b"\n\n", 1)
                    line = block.decode("utf-8", "replace").strip()
                    data = line[5:].strip() if line.startswith("data:") else None
                    keep = True
                    if data and data != "[DONE]":
                        try:
                            obj = json.loads(data)
                        except ValueError:
                            obj = None
                        if isinstance(obj, dict):
                            model_seen = obj.get("model") or model_seen
                            if obj.get("usage"):
                                usage_chunk = obj
                                keep = wants_usage or bool(obj.get("choices"))
                            for ch in obj.get("choices") or []:
                                piece = (ch.get("delta") or {}).get("content")
                                if piece:
                                    if ttft is None:
                                        ttft = (time.perf_counter() - start) * 1000
                                    text.append(piece)
                                finish = ch.get("finish_reason") or finish
                    if keep:
                        out += block + b"\n\n"
                if out:
                    yield out
            if buffer:
                yield buffer
        except httpx.HTTPError as exc:
            status, error_type = 502, type(exc).__name__
            yield f"data: {json.dumps({'error': {'message': 'the provider dropped the connection', 'type': 'upstream_error', 'code': 'upstream_disconnected'}})}\n\ndata: [DONE]\n\n".encode()
        finally:
            lat = (time.perf_counter() - start) * 1000
            resp_obj = {**(usage_chunk or {}), "model": model_seen, "choices": [{"finish_reason": finish}]}
            _detach(
                state,
                _event(
                    labels,
                    model,
                    body,
                    resp_obj,
                    latency_ms=lat,
                    ttft_ms=ttft,
                    stream=True,
                    status=status,
                    error_type=error_type,
                    completion_text="".join(text),
                ),
            )
            state.metrics.proxied.labels(str(status)).inc()
            await r.aclose()

    return ClosingStreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/embeddings", summary="OpenAI-compatible embeddings, recorded")
async def embeddings(request: Request, state: State, principal: Ingest) -> Response:
    raw = await request.body()
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_json", "the request body is not valid JSON") from exc
    if not isinstance(body, dict) or not isinstance(body.get("model"), str):
        raise ApiError(422, "invalid_request", "model (string) is required")
    labels = _labels(request, principal)
    check_app(principal, labels["app"])
    url, headers = _upstream(state, request, "/embeddings")
    start = time.perf_counter()
    try:
        r = await state.http.post(
            url, content=raw, headers=headers, timeout=state.settings.upstream_timeout_s
        )
    except httpx.HTTPError as exc:
        raise ApiError(
            502, "upstream_unreachable", f"the provider could not be reached ({type(exc).__name__})"
        ) from exc
    try:
        payload = r.json()
    except ValueError:
        payload = None
    resp = payload if isinstance(payload, dict) else None
    if resp is not None and r.status_code < 400:
        u = resp.get("usage") or {}
        resp = {
            **resp,
            "usage": {"prompt_tokens": u.get("prompt_tokens", u.get("total_tokens")), "completion_tokens": 0},
        }
    _detach(
        state,
        _event(
            labels,
            body["model"],
            body,
            resp,
            latency_ms=(time.perf_counter() - start) * 1000,
            ttft_ms=None,
            stream=False,
            status=r.status_code,
            error_type=None,
            endpoint="embedding",
        ),
    )
    state.metrics.proxied.labels(str(r.status_code)).inc()
    return Response(
        r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json")
    )


@router.get("/models", summary="Pass-through model list")
async def models(request: Request, state: State, _: Ingest) -> Response:
    url, headers = _upstream(state, request, "/models")
    try:
        r = await state.http.get(url, headers=headers, timeout=15)
    except httpx.HTTPError as exc:
        raise ApiError(
            502, "upstream_unreachable", f"the provider could not be reached ({type(exc).__name__})"
        ) from exc
    return JSONResponse(
        r.json()
        if r.headers.get("content-type", "").startswith("application/json")
        else {"error": "bad upstream response"},
        status_code=r.status_code,
    )
