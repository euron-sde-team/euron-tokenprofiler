"""Event ingestion: the endpoint SDKs and anything else post usage to."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from tokenprofiler_core.events import EventIn
from tokenprofiler_core.ingest import IngestError, resolve
from tokenprofiler_core.pricing import Usage
from tokenprofiler_core.prompts import price_table
from tokenprofiler_core.store import StoredEvent
from tokenprofiler_core.tokens import count, count_messages

from ..auth import Principal, require
from ..errors import ApiError
from ..schemas import EstimateRequest, EventBatch, IngestResult
from ..state import AppState, State
from .common import check_app

router = APIRouter(tags=["ingest"])
Ingest = Annotated[Principal, Depends(require("ingest"))]
Reader = Annotated[Principal, Depends(require("read"))]


def build_events(
    state: AppState, principal: Principal, raw: list[dict[str, Any]]
) -> tuple[list[StoredEvent], list[dict[str, Any]]]:
    """Validates and resolves each event on its own. Returns (good events, errors by index)."""
    good: list[StoredEvent] = []
    errors: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        try:
            e = EventIn.model_validate(item)
            check_app(principal, e.app)
            good.append(resolve(e, state.prices, derive_prompt_id=state.settings.derive_prompt_ids))
        except ValidationError as exc:
            first = exc.errors()[0]
            errors.append(
                {"index": i, "error": f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}".strip(": ")}
            )
        except IngestError as exc:
            errors.append({"index": i, "error": str(exc)})
        except ApiError as exc:
            errors.append({"index": i, "error": exc.message})
    return good, errors


@router.post("/v1/events", summary="Record LLM usage events (batch)", response_model=IngestResult)
async def post_events(body: EventBatch, state: State, principal: Ingest) -> IngestResult:
    if len(body.events) > state.settings.max_batch_events:
        raise ApiError(
            413, "batch_too_large", f"send at most {state.settings.max_batch_events} events per request"
        )
    if not state.limiter.allow(f"ingest:{principal.prefix}"):
        raise ApiError(429, "rate_limited", "ingest rate limit exceeded for this key")
    good, errors = await asyncio.to_thread(build_events, state, principal, body.events)
    inserted, dups = (await asyncio.to_thread(state.usage.record, good)) if good else (0, 0)
    state.metrics.ingested.labels("api").inc(inserted)
    state.metrics.duplicates.inc(dups)
    state.metrics.rejected.inc(len(errors))
    if not good and errors:
        raise ApiError(422, "all_events_invalid", "no event in the batch was valid", errors=errors[:50])
    return IngestResult(accepted=inserted, duplicates=dups, rejected=len(errors), errors=errors[:50])


@router.post("/api/estimate", summary="Estimate tokens and cost for a prompt, without storing anything")
async def estimate(req: EstimateRequest, state: State, _: Reader) -> dict[str, Any]:
    if (req.text is None) == (req.messages is None):
        raise ApiError(422, "invalid_request", "provide exactly one of text or messages")
    tc = (
        count(req.text or "", req.model)
        if req.text is not None
        else count_messages(req.messages or [], req.model)
    )
    cost = state.prices.cost(req.model, Usage(tc.tokens, req.expected_completion_tokens))
    return {
        "model": req.model, "prompt_tokens": tc.tokens, "estimated": True, "tokenizer_family": tc.family, "priced": cost.priced,
        "cost_per_request_micro": cost.total, "cost_total_micro": cost.total * req.requests,
        "alternatives": price_table(tc.tokens, req.expected_completion_tokens, state.prices, requests=req.requests)[:8],
        "note": "Token counts are estimates (about 4-6% off the real tokenizer for OpenAI families, less certain for others).",
    }  # fmt: skip
