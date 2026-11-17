"""Model prices and exact cost calculation.

Prices are USD per million tokens (so tokens x price is micro-dollars). The built-in book (``prices.json``) holds
approximate list prices and is meant to be overridden: a price file, or ``PriceBook.set``. A model with no price is
reported as *unpriced*, never silently costed at zero or at a guess.

Token accounting convention used throughout TokenProfiler (and the reason adapters exist): ``prompt_tokens`` is the
**total** input, ``cached_tokens`` (cache reads) and ``cache_write_tokens`` are subsets of it, and ``completion_tokens``
includes any reasoning tokens (providers bill them as output).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

_DATE_SUFFIX = re.compile(r"(-\d{4}-\d{2}-\d{2}|-\d{2}-\d{2}|-\d{8}|-\d{6}|-latest|-preview|:\d+)$")
_PROVIDER_PREFIX = re.compile(
    r"^(openai|anthropic|google|vertex_ai|bedrock|azure|mistral|deepseek|meta|together|groq)[/.:]"
)


def normalize_model(name: str) -> str:
    """ "anthropic.claude-3-5-sonnet-20241022-v2:0" -> "claude-3-5-sonnet": the part prices are keyed by."""
    m = name.strip().lower()
    m = _PROVIDER_PREFIX.sub("", m)
    m = re.sub(r"-v\d+(:\d+)?$", "", m)
    for _ in range(3):  # strip stacked suffixes such as "-preview-2025-01-01"
        stripped = _DATE_SUFFIX.sub("", m)
        if stripped == m:
            break
        m = stripped
    return m


class ModelPrice(BaseModel):
    id: str
    provider: str
    input: float = Field(ge=0, description="USD per 1M input tokens")
    output: float = Field(ge=0, description="USD per 1M output tokens")
    cached_input: float | None = Field(
        default=None, ge=0, description="USD per 1M cached (read) input tokens"
    )
    cache_write: float | None = Field(
        default=None, ge=0, description="USD per 1M tokens written to the prompt cache"
    )
    context: int | None = None
    tier: str = "mid"
    cheaper: str | None = Field(
        default=None, description="The model to try when this one is more than the task needs"
    )
    batch_discount: float | None = Field(
        default=None, ge=0, le=1, description="Fraction off for the batch API"
    )


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.prompt_tokens, self.completion_tokens, self.cached_tokens, self.cache_write_tokens) < 0:
            raise ValueError("token counts cannot be negative")
        if self.cached_tokens + self.cache_write_tokens > self.prompt_tokens:
            raise ValueError("cached and cache-write tokens are subsets of the prompt tokens")

    @property
    def uncached_prompt_tokens(self) -> int:
        return self.prompt_tokens - self.cached_tokens - self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class Cost:
    """Micro-dollars by component. ``priced`` is False when the model has no price (every component is then 0)."""

    input: int = 0
    cached: int = 0
    cache_write: int = 0
    output: int = 0
    priced: bool = True

    @property
    def total(self) -> int:
        return self.input + self.cached + self.cache_write + self.output

    @property
    def input_total(self) -> int:
        return self.input + self.cached + self.cache_write


def half_up(x: float) -> int:
    """Round half up. SQLite's ROUND() rounds half away from zero, which is the same for non-negative numbers, so a cost
    computed here and one recomputed in SQL (re-pricing) are always identical. Python's round() is banker's rounding."""
    return math.floor(x + 0.5)


@cache
def _builtin() -> tuple[str, list[ModelPrice]]:
    raw = json.loads((Path(__file__).parent / "prices.json").read_text(encoding="utf-8"))
    return str(raw["as_of"]), [ModelPrice.model_validate(m) for m in raw["models"]]


class PriceBook:
    def __init__(self, extra: dict[str, ModelPrice] | None = None) -> None:
        self.as_of, builtin = _builtin()
        self._prices: dict[str, ModelPrice] = {p.id: p for p in builtin}
        self.overridden: set[str] = set()
        for k, v in (extra or {}).items():
            self.set(v if k == v.id else v.model_copy(update={"id": k}))

    @classmethod
    def from_file(cls, path: str | Path) -> PriceBook:
        """A JSON object of ``{"model-id": {"input": 1.0, "output": 3.0, ...}}`` layered over the built-in prices."""
        raw: dict[str, dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))
        book = cls()
        for model_id, fields in raw.items():
            book.set(
                ModelPrice.model_validate({"provider": "custom", **fields, "id": normalize_model(model_id)})
            )
        return book

    def set(self, price: ModelPrice) -> None:
        self._prices[price.id] = price
        self.overridden.add(price.id)

    def remove(self, model_id: str) -> bool:
        """Removes an override; a built-in price comes back. Returns whether an override existed."""
        had = model_id in self.overridden
        self.overridden.discard(model_id)
        if had:
            builtin = {p.id: p for p in _builtin()[1]}
            if model_id in builtin:
                self._prices[model_id] = builtin[model_id]
            else:
                self._prices.pop(model_id, None)
        return had

    def all(self) -> list[ModelPrice]:
        return sorted(self._prices.values(), key=lambda p: (p.provider, p.id))

    def resolve(self, model: str) -> ModelPrice | None:
        """The price entry for a model name: exact match on the normalized name, else the longest matching prefix."""
        m = normalize_model(model)
        if m in self._prices:
            return self._prices[m]
        best = max((k for k in self._prices if m.startswith(k)), key=len, default=None)
        return self._prices[best] if best else None

    def cost(self, model: str, usage: Usage, *, batch: bool = False) -> Cost:
        p = self.resolve(model)
        if p is None:
            return Cost(priced=False)
        cached_rate = p.cached_input if p.cached_input is not None else p.input
        write_rate = p.cache_write if p.cache_write is not None else p.input
        scale = 1 - (p.batch_discount or 0) if batch else 1.0
        return Cost(
            input=half_up(usage.uncached_prompt_tokens * p.input * scale),
            cached=half_up(usage.cached_tokens * cached_rate * scale),
            cache_write=half_up(usage.cache_write_tokens * write_rate * scale),
            output=half_up(usage.completion_tokens * p.output * scale),
        )
