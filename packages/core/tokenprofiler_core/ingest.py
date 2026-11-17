"""Turns a client's :class:`EventIn` into a stored event: settles the token counts, prices it, normalizes names."""

from __future__ import annotations

import hashlib
import re
import uuid

from .events import EventIn, now_ms
from .pricing import PriceBook, Usage, normalize_model
from .store import StoredEvent
from .tokens import estimate, family_for_model

_WS = re.compile(r"\s+")
_NUM = re.compile(r"\b\d[\d.,:/-]*\b")
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_QUOTED = re.compile(r"\"[^\"\n]{0,200}\"|'[^'\n]{0,200}'")


class IngestError(ValueError):
    """An event cannot be stored (for example it carries no token information at all)."""


def fingerprint(text: str) -> str:
    """A stable hash of the prompt's exact text (whitespace-normalized): equal hash = the same prompt was sent again."""
    return hashlib.sha256(_WS.sub(" ", text).strip().encode("utf-8", "ignore")).hexdigest()[:32]


def template_hash(text: str) -> str:
    """A hash of the prompt's *shape*: numbers, UUIDs and quoted strings are masked, so the same template filled with
    different values maps to one id. Used to group calls when the client gave no ``prompt_id``."""
    masked = _QUOTED.sub('"?"', _UUID.sub("<id>", text))
    masked = _NUM.sub("<n>", masked)
    return hashlib.sha256(_WS.sub(" ", masked).strip().lower().encode("utf-8", "ignore")).hexdigest()[:16]


def resolve(e: EventIn, prices: PriceBook, *, derive_prompt_id: bool = True) -> StoredEvent:
    """Settles an incoming event. Reported token counts always win; text is only used to fill what is missing."""
    family = family_for_model(e.model)
    estimated = False
    prompt = e.prompt_tokens
    completion = e.completion_tokens
    if prompt is None:
        if e.prompt_text is None:
            raise IngestError("provide prompt_tokens, or prompt_text so they can be estimated")
        prompt, estimated = estimate(e.prompt_text, family), True
    if completion is None:
        if e.completion_text is not None:
            completion, estimated = estimate(e.completion_text, family), True
        elif e.status == "error":
            completion = 0  # a failed call produced no output
        else:
            raise IngestError("provide completion_tokens, or completion_text so they can be estimated")
    if (
        e.cached_tokens + e.cache_write_tokens > prompt
    ):  # an estimate can come out below the reported cache reads
        prompt = e.cached_tokens + e.cache_write_tokens
    usage = Usage(prompt, completion, e.cached_tokens, e.cache_write_tokens)
    cost = prices.cost(e.model, usage, batch=e.batch)
    price = prices.resolve(e.model)

    spend, saved = cost, 0
    if e.cache_hit:  # served by an application cache: nothing was spent, and this is what it would have cost
        saved = cost.total
        spend = type(cost)(priced=cost.priced)

    prompt_id = e.prompt_id
    prompt_hash = e.prompt_hash
    if e.prompt_text is not None:
        prompt_hash = prompt_hash or fingerprint(e.prompt_text)
        if prompt_id is None and derive_prompt_id:
            prompt_id = f"auto:{template_hash(e.prompt_text)}"
    return StoredEvent(
        id=e.id or uuid.uuid4().hex, ts=e.ts if e.ts is not None else now_ms(), provider=e.provider or (price.provider if price else "unknown"),
        model=price.id if price else normalize_model(e.model), model_raw=e.model, app=e.app, environment=e.environment,
        endpoint=e.endpoint, prompt_tokens=prompt, completion_tokens=completion, cached_tokens=e.cached_tokens,
        cache_write_tokens=e.cache_write_tokens, reasoning_tokens=min(e.reasoning_tokens, completion), estimated=estimated,
        cost_input=spend.input, cost_cached=spend.cached, cost_cache_write=spend.cache_write, cost_output=spend.output,
        cost_total=spend.total, priced=cost.priced, saved_micro=saved, latency_ms=e.latency_ms, ttft_ms=e.ttft_ms,
        status=e.status, error_type=e.error_type, http_status=e.http_status, finish_reason=e.finish_reason, stream=e.stream,
        batch=e.batch, cache_hit=e.cache_hit, max_tokens=e.max_tokens, temperature=e.temperature, prompt_id=prompt_id,
        prompt_hash=prompt_hash, user_id=e.user_id, session_id=e.session_id, tags=dict(e.tags), ingested_at=now_ms(),
    )  # fmt: skip
