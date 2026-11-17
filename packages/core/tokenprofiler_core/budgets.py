"""Budgets: a monthly spend limit for a slice of traffic (an app, an environment, a model, a tag, or everything)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .query import EQ_FILTERS, Filters

Status = Literal["ok", "warning", "projected_overrun", "exceeded"]
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,63}$")


class BudgetIn(BaseModel):
    name: str
    limit_usd: float = Field(gt=0, le=100_000_000, description="Monthly limit in USD")
    scope: dict[str, str] = Field(
        default_factory=dict,
        description="Filter the budget applies to, e.g. {'app': 'support-bot'}; empty = everything",
    )
    warn_at: float = Field(default=0.8, gt=0, lt=1, description="Fraction of the limit that raises a warning")
    webhook_url: str | None = Field(
        default=None, max_length=500, description="POSTed to when the budget crosses a threshold"
    )

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not NAME_RE.match(v):
            raise ValueError("name must be 1-64 characters: letters, digits, spaces, . _ : -")
        return v

    @field_validator("scope")
    @classmethod
    def _scope(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > 4:
            raise ValueError("a budget scope has at most 4 fields")
        for k, val in v.items():
            if k not in EQ_FILTERS and not k.startswith("tag:"):
                raise ValueError(f"cannot scope a budget by {k!r}")
            if not val or len(val) > 128:
                raise ValueError(f"scope value for {k!r} must be 1-128 characters")
        return v

    def filters(self, since: int, until: int) -> Filters:
        tags = {k[4:]: val for k, val in self.scope.items() if k.startswith("tag:")}
        eq = {k: val for k, val in self.scope.items() if not k.startswith("tag:")}
        return Filters(since=since, until=until, eq=eq, tags=tags)


class BudgetStatus(BaseModel):
    status: Status
    spent_micro: int
    limit_micro: int
    used_pct: float
    projected_micro: int
    projected_pct: float
    remaining_micro: int
    days_until_exhausted: float | None = Field(
        description="At the current run rate, or null when it will not run out this month"
    )


def evaluate(
    limit_micro: int, warn_at: float, spent_micro: int, projected_micro: int, run_rate_per_day: float
) -> BudgetStatus:
    """Where a budget stands. ``exceeded`` > ``projected_overrun`` > ``warning`` > ``ok``."""
    used = spent_micro / limit_micro if limit_micro else 0.0
    proj = projected_micro / limit_micro if limit_micro else 0.0
    if spent_micro >= limit_micro:
        status: Status = "exceeded"
    elif projected_micro > limit_micro:
        status = "projected_overrun"
    elif used >= warn_at:
        status = "warning"
    else:
        status = "ok"
    remaining = max(0, limit_micro - spent_micro)
    days_left = None
    if spent_micro < limit_micro and run_rate_per_day > 0 and projected_micro > limit_micro:
        days_left = round(remaining / run_rate_per_day, 1)
    return BudgetStatus(
        status=status, spent_micro=spent_micro, limit_micro=limit_micro, used_pct=round(used * 100, 1), projected_micro=projected_micro,
        projected_pct=round(proj * 100, 1), remaining_micro=remaining, days_until_exhausted=days_left,
    )  # fmt: skip
