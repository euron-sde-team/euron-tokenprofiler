"""The usage event: one LLM call, as reported by an SDK, the proxy or a direct POST."""

from __future__ import annotations

import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MAX_TOKENS_PER_FIELD = 50_000_000
MAX_TEXT_BYTES = 1_000_000
MAX_FUTURE_MS = 3_600_000  # clocks drift, but an event an hour ahead is a bug
Endpoint = Literal["chat", "completion", "embedding", "image", "audio", "other"]


def now_ms() -> int:
    return int(time.time() * 1000)


class EventIn(BaseModel):
    """What a client sends. Unknown fields are rejected so typos are found at once instead of silently dropped."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, description="Idempotency key. Re-sending the same id is a no-op.")
    ts: int | None = Field(default=None, description="Epoch milliseconds. Defaults to now.")
    model: str = Field(min_length=1, max_length=120)
    provider: str | None = Field(default=None, max_length=40)
    app: str = "default"
    environment: str = "production"
    endpoint: Endpoint = "chat"

    prompt_tokens: int | None = Field(
        default=None, ge=0, le=MAX_TOKENS_PER_FIELD, description="Total input tokens, cached included"
    )
    completion_tokens: int | None = Field(
        default=None, ge=0, le=MAX_TOKENS_PER_FIELD, description="Output tokens, reasoning included"
    )
    cached_tokens: int = Field(
        default=0,
        ge=0,
        le=MAX_TOKENS_PER_FIELD,
        description="Subset of the prompt read from the provider's prompt cache",
    )
    cache_write_tokens: int = Field(default=0, ge=0, le=MAX_TOKENS_PER_FIELD)
    reasoning_tokens: int = Field(
        default=0,
        ge=0,
        le=MAX_TOKENS_PER_FIELD,
        description="Informational: already counted in completion_tokens",
    )
    prompt_text: str | None = Field(
        default=None, description="Used only to estimate prompt_tokens when they are missing; never stored"
    )
    completion_text: str | None = Field(default=None, description="Same, for completion_tokens")

    latency_ms: float | None = Field(default=None, ge=0, le=3_600_000)
    ttft_ms: float | None = Field(default=None, ge=0, le=3_600_000)
    status: Literal["ok", "error"] = "ok"
    error_type: str | None = Field(default=None, max_length=64)
    http_status: int | None = Field(default=None, ge=100, le=599)
    finish_reason: str | None = Field(default=None, max_length=32)
    stream: bool = False
    batch: bool = Field(default=False, description="Sent through the provider's batch API (discounted)")
    cache_hit: bool = Field(
        default=False,
        description="Answered by an application cache: no provider call; tokens are the spend avoided",
    )
    max_tokens: int | None = Field(default=None, ge=1, le=MAX_TOKENS_PER_FIELD)
    temperature: float | None = Field(default=None, ge=0, le=10)

    prompt_id: str | None = Field(default=None, description="A stable name for the prompt template")
    prompt_hash: str | None = Field(default=None, max_length=64, pattern=r"^[0-9a-f]{8,64}$")
    user_id: str | None = Field(default=None, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str | None) -> str | None:
        if v is not None and not ID_RE.match(v):
            raise ValueError("id must be 1-128 characters: letters, digits, . _ : -")
        return v

    @field_validator("app", "environment")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("must be 1-64 characters: letters, digits, . _ : -")
        return v

    @field_validator("prompt_id")
    @classmethod
    def _prompt_id(cls, v: str | None) -> str | None:
        if v is not None and (not v.strip() or len(v) > 128):
            raise ValueError("prompt_id must be 1-128 characters")
        return v

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 10:
            raise ValueError("at most 10 tags")
        for k, val in v.items():
            if not re.match(r"^[A-Za-z][A-Za-z0-9_]{0,31}$", k):
                raise ValueError(f"invalid tag name {k!r}: letters, digits, _; at most 32 characters")
            if len(val) > 64:
                raise ValueError(f"tag {k!r} is longer than 64 characters")
        return v

    @field_validator("prompt_text", "completion_text")
    @classmethod
    def _text(cls, v: str | None) -> str | None:
        if v is not None and len(v.encode("utf-8", "ignore")) > MAX_TEXT_BYTES:
            raise ValueError("text is limited to 1 MB")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> EventIn:
        if self.ts is not None and self.ts > now_ms() + MAX_FUTURE_MS:
            raise ValueError("ts is in the future")
        if self.ts is not None and self.ts < 946_684_800_000:
            raise ValueError("ts must be epoch milliseconds (looks like seconds)")
        if (
            self.prompt_tokens is not None
            and self.cached_tokens + self.cache_write_tokens > self.prompt_tokens
        ):
            raise ValueError("cached_tokens + cache_write_tokens cannot exceed prompt_tokens")
        if self.status == "ok" and self.http_status is not None and self.http_status >= 400:
            raise ValueError("status is ok but http_status is an error")
        return self
