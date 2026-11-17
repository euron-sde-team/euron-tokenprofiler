from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from tokenprofiler_core.events import EventIn


class EventBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: list[dict[str, Any]] = Field(
        min_length=1, description="Each item is validated on its own; one bad event does not reject the rest"
    )


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    errors: list[dict[str, Any]] = Field(
        default_factory=list, description="Index and reason for each rejected event (first 50)"
    )


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = "gpt-4o"
    text: str | None = Field(default=None, max_length=1_000_000)
    messages: list[dict[str, Any]] | None = Field(default=None, max_length=2000)
    expected_completion_tokens: int = Field(default=0, ge=0, le=10_000_000)
    requests: int = Field(default=1, ge=1, le=1_000_000_000)


class PriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = "custom"
    input: float = Field(ge=0, le=100_000, description="USD per 1M input tokens")
    output: float = Field(ge=0, le=100_000)
    cached_input: float | None = Field(default=None, ge=0, le=100_000)
    cache_write: float | None = Field(default=None, ge=0, le=100_000)
    context: int | None = Field(default=None, ge=1)
    batch_discount: float | None = Field(default=None, ge=0, le=1)
    cheaper: str | None = Field(default=None, max_length=120)


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(default_factory=lambda: ["ingest"], min_length=1)
    apps: list[str] = Field(default_factory=list, description="Limit the key to these apps; empty means all")


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=400_000)
    messages: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    model: str = "gpt-4o"
    expected_completion_tokens: int = Field(default=300, ge=0, le=1_000_000)
    requests_per_month: int = Field(default=10_000, ge=1, le=10_000_000_000)


__all__ = ["AnalyzeRequest", "EstimateRequest", "EventBatch", "EventIn", "IngestResult", "KeyIn", "PriceIn"]
