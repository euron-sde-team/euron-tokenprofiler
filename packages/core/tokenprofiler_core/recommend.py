"""The optimizer: reads real usage and proposes specific, quantified cost reductions.

Each rule looks at what actually happened (volumes, token sizes, repeats, errors, finish reasons) and states its evidence
and how sure it is. Savings are projected to 30 days from the observed window. Recommendations *overlap* (a cheaper model
and a cache both act on the same spend), so the headline total is a capped ceiling, not a sum; see ``potential``.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Literal

from .pricing import PriceBook
from .query import Filters
from .store import DAY_MS, UsageStore

Confidence = Literal["high", "medium", "low"]
Effort = Literal["low", "medium", "high"]
OVERLAP_CAP = 0.7  # the optimizer never claims more than 70% of the spend can be removed


@dataclass(slots=True)
class Recommendation:
    id: str
    kind: str
    title: str
    summary: str
    monthly_savings_micro: int
    confidence: Confidence
    effort: Effort
    app: str | None = None
    model: str | None = None
    prompt_id: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Settings:
    window_days: int = 30
    min_requests: int = 30
    min_savings_micro: int = 500_000  # $0.50 a month: below that it is not worth anyone's attention


def _rid(kind: str, *parts: str | None) -> str:
    return hashlib.sha256("|".join([kind, *(p or "" for p in parts)]).encode()).hexdigest()[:12]


def _scale(span_ms: float, window_days: int) -> float:
    """Factor that turns spend observed over the data span into spend over 30 days."""
    days = max(1.0, min(float(window_days), span_ms / DAY_MS))
    return 30.0 / days


def analyze(
    store: UsageStore,
    prices: PriceBook,
    *,
    now_ms: int,
    settings: Settings | None = None,
    base: Filters | None = None,
) -> dict[str, Any]:
    cfg = settings or Settings()
    f = base or Filters()
    f = Filters(
        since=max(f.since or 0, now_ms - cfg.window_days * DAY_MS),
        until=f.until,
        eq=dict(f.eq),
        tags=dict(f.tags),
    )
    totals = store.aggregate(f, None, ["requests", "cost", "unpriced"])[0]
    first = store.first_ts(f)
    span = (now_ms - first) if first else 0
    scale = _scale(span, cfg.window_days)
    recs: list[Recommendation] = []

    templates = store.template_stats(f)
    recs += _downgrade(store, prices, f, cfg, scale)
    recs += _prompt_caching(templates, prices, cfg, scale)
    recs += _response_cache(store, f, cfg, scale, int(totals["requests"]))
    recs += _truncation(store, f, cfg)
    recs += _error_waste(store, f, cfg, scale)
    recs += _batch(store, prices, f, cfg, scale)
    recs += _context_growth(store, f, cfg, scale)
    recs += _unpriced(store, f, int(totals["unpriced"]))

    recs = [
        r
        for r in recs
        if r.monthly_savings_micro >= cfg.min_savings_micro or r.kind in ("pricing", "quality")
    ]
    recs.sort(key=lambda r: (-r.monthly_savings_micro, r.kind))
    monthly = round(int(totals["cost"]) * scale)
    summed = sum(r.monthly_savings_micro for r in recs)
    potential = min(summed, round(monthly * OVERLAP_CAP))
    return {
        "window_days": cfg.window_days, "observed_days": round(span / DAY_MS, 1), "monthly_cost_micro": monthly,
        "potential_monthly_savings_micro": potential, "sum_of_recommendations_micro": summed,
        "overlap_note": "Recommendations act on overlapping spend, so the potential is capped and is not the sum.",
        "recommendations": recs,
    }  # fmt: skip


# ── rules ────────────────────────────────────────────────────────────────────────────────────────────────────
def _downgrade(
    store: UsageStore, prices: PriceBook, f: Filters, cfg: Settings, scale: float
) -> list[Recommendation]:
    out = []
    rows = store.aggregate(
        f,
        ["app", "model"],
        ["requests", "prompt_tokens", "completion_tokens", "cached_tokens", "cost", "errors"],
        limit=500,
    )
    for r in rows:
        p = prices.resolve(r["model"])
        if p is None or not p.cheaper or p.tier == "small" or r["requests"] < cfg.min_requests:
            continue
        alt = prices.resolve(p.cheaper)
        if alt is None:
            continue
        uncached = r["prompt_tokens"] - r["cached_tokens"]
        alt_cost = round(
            uncached * alt.input
            + r["cached_tokens"] * (alt.cached_input if alt.cached_input is not None else alt.input)
            + r["completion_tokens"] * alt.output
        )
        saving = round((r["cost"] - alt_cost) * scale)
        if saving < cfg.min_savings_micro:
            continue
        avg_c, avg_p = r["completion_tokens"] / r["requests"], r["prompt_tokens"] / r["requests"]
        simple = avg_c <= 150 and avg_p <= 1500
        conf: Confidence = "medium" if avg_c <= 300 else "low"
        out.append(Recommendation(
            id=_rid("downgrade", r["app"], r["model"]), kind="model_downgrade",
            title=f"Try {alt.id} instead of {p.id} for {r['app']}",
            summary=f"{r['requests']:,} calls average {avg_p:,.0f} tokens in and {avg_c:,.0f} out" + (" (short, simple-looking tasks)" if simple else "")
            + f". {alt.id} is {p.id}'s cheaper sibling and would cost about {alt_cost * scale / 1e6:,.2f} instead of {r['cost'] * scale / 1e6:,.2f} a month.",
            monthly_savings_micro=saving, confidence=conf, effort="medium", app=r["app"], model=p.id,
            evidence={"requests": r["requests"], "avg_prompt_tokens": round(avg_p), "avg_completion_tokens": round(avg_c), "alternative": alt.id,
                      "current_cost_micro": r["cost"], "alternative_cost_micro": alt_cost, "error_rate": round(r["errors"] / r["requests"], 4)},
            steps=[f"Replay a sample of {p.id} requests against {alt.id} and compare the answers (an evaluation set, or a human check on 50 cases).",
                   "Route a small share of traffic to the cheaper model and watch quality and error metrics.",
                   "Keep the larger model only for the prompts where the comparison shows a real difference."],
        ))  # fmt: skip
    return out


def _prompt_caching(
    templates: list[dict[str, Any]], prices: PriceBook, cfg: Settings, scale: float
) -> list[Recommendation]:
    out = []
    for t in templates:
        p = prices.resolve(t["model"])
        if p is None or p.cached_input is None or p.cached_input >= p.input:
            continue
        if t["requests"] < cfg.min_requests or t["min_prompt"] < 1024:
            continue
        already = t["cached_tokens"] / t["prompt_tokens"] if t["prompt_tokens"] else 0.0
        if already >= 0.25:
            continue
        span_min = max(1.0, (t["last_ts"] - t["first_ts"]) / 60_000)
        per_5min = t["requests"] / span_min * 5
        hit = min(0.9, 1 - math.exp(-per_5min))  # chance a call lands inside the provider's cache window
        if hit < 0.2:
            continue
        stable = int(
            0.7 * t["min_prompt"]
        )  # the shortest call bounds the shared prefix; 30% margin for dynamic content up front
        saving = round(t["requests"] * stable * hit * (p.input - p.cached_input) * (1 - already) * scale)
        tpl = None if t["prompt_id"] == "(none)" else t["prompt_id"]
        out.append(Recommendation(
            id=_rid("prompt_cache", t["app"], t["model"], t["prompt_id"]), kind="prompt_caching",
            title=f"Cache the shared prompt prefix in {tpl or t['app']}",
            summary=f"{t['requests']:,} calls to {p.id} have at least {t['min_prompt']:,} input tokens, and only {already:.0%} are served from the prompt cache. "
            f"About {stable:,} tokens per call are probably identical each time; cached input is billed at {p.cached_input / p.input:.0%} of the normal rate.",
            monthly_savings_micro=saving, confidence="high" if hit >= 0.7 and stable >= 2000 else "medium", effort="low", app=t["app"], model=p.id,
            prompt_id=tpl, evidence={"requests": t["requests"], "min_prompt_tokens": t["min_prompt"], "estimated_shared_prefix": stable,
                                     "cached_share": round(already, 3), "est_hit_rate": round(hit, 2), "cached_rate_ratio": round(p.cached_input / p.input, 3)},
            steps=["Put everything that does not change (instructions, tool definitions, examples) at the start of the prompt, and anything per-request at the end.",
                   "OpenAI and Google cache long shared prefixes automatically; for Anthropic, mark the stable block with cache_control.",
                   "Send the cached_tokens usage field to TokenProfiler to confirm the hit rate."],
        ))  # fmt: skip
    return out


def _response_cache(
    store: UsageStore, f: Filters, cfg: Settings, scale: float, total_requests: int
) -> list[Recommendation]:
    rows = store.repeated_prompts(f, min_count=2, limit=200)
    if not rows or total_requests < cfg.min_requests:
        return []
    out = []
    by_app: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_app.setdefault(r["app"], []).append(r)
    for app, group in by_app.items():
        repeats = sum(r["n"] - 1 for r in group)
        saving = round(sum(r["cost"] * (r["n"] - 1) / r["n"] for r in group) * scale)
        calls = sum(r["n"] for r in group)
        share = repeats / total_requests
        if repeats < max(5, cfg.min_requests // 3):
            continue
        out.append(Recommendation(
            id=_rid("response_cache", app), kind="response_cache", title=f"Cache repeated requests in {app}",
            summary=f"{repeats:,} calls ({share:.0%} of all traffic) repeat an earlier prompt exactly. Answering them from a cache costs nothing and responds in milliseconds.",
            monthly_savings_micro=saving, confidence="high" if share >= 0.05 else "medium", effort="low", app=app,
            evidence={"repeated_calls": repeats, "distinct_repeated_prompts": len(group), "calls_in_those_prompts": calls,
                      "top": [{"prompt_hash": r["prompt_hash"], "calls": r["n"], "prompt_id": r["prompt_id"]} for r in group[:3]]},
            steps=["Put a response cache in front of the provider: keyed on the exact prompt, with a TTL that matches how fast your answers go stale.",
                   "For paraphrased repeats, a semantic cache (such as Euron SemanticCache) also catches near-duplicates.",
                   "Send cache_hit=true events to TokenProfiler to track the spend avoided."],
        ))  # fmt: skip
    return out


def _truncation(store: UsageStore, f: Filters, cfg: Settings) -> list[Recommendation]:
    out = []
    rows = store.aggregate(
        Filters(since=f.since, until=f.until, eq={**f.eq, "finish_reason": "length"}, tags=f.tags),
        ["app", "model"],
        ["requests", "cost"],
        limit=100,
    )
    totals = {
        (r["app"], r["model"]): r["requests"]
        for r in store.aggregate(f, ["app", "model"], ["requests"], limit=1000)
    }
    for r in rows:
        n = totals.get((r["app"], r["model"]), 0)
        if n < cfg.min_requests or r["requests"] / n < 0.03:
            continue
        out.append(Recommendation(
            id=_rid("truncation", r["app"], r["model"]), kind="quality", title=f"{r['requests']:,} answers from {r['app']} were cut off",
            summary=f"{r['requests'] / n:.0%} of {r['model']} calls ended with finish_reason=length: the output hit max_tokens. Truncated answers are usually retried or are wrong, and the tokens were still billed.",
            monthly_savings_micro=0, confidence="high", effort="low", app=r["app"], model=r["model"],
            evidence={"truncated_calls": r["requests"], "share": round(r["requests"] / n, 4), "billed_micro": r["cost"]},
            steps=["Raise max_tokens for these prompts, or ask for a shorter answer in the instructions.", "Alert on finish_reason=length in your own tests."],
        ))  # fmt: skip
    return out


def _error_waste(store: UsageStore, f: Filters, cfg: Settings, scale: float) -> list[Recommendation]:
    out = []
    ef = Filters(since=f.since, until=f.until, eq={**f.eq, "status": "error"}, tags=f.tags)
    for r in store.aggregate(ef, ["app", "error_type"], ["requests", "cost", "prompt_tokens"], limit=200):
        saving = round(r["cost"] * scale)
        if r["requests"] < max(10, cfg.min_requests // 3) or saving < cfg.min_savings_micro:
            continue
        out.append(Recommendation(
            id=_rid("errors", r["app"], r["error_type"]), kind="error_waste", title=f"Failed calls in {r['app']} still cost money",
            summary=f"{r['requests']:,} calls failed with '{r['error_type']}' after sending {r['prompt_tokens']:,} input tokens. Providers bill prompts that time out or are rejected late, and retries send them again.",
            monthly_savings_micro=saving, confidence="medium", effort="medium", app=r["app"],
            evidence={"failed_calls": r["requests"], "error_type": r["error_type"], "wasted_micro": r["cost"]},
            steps=["Cap retries and add jitter; do not retry on 4xx.", "Validate the prompt size before sending so over-limit requests fail locally.", "Use a circuit breaker so an outage does not multiply the spend."],
        ))  # fmt: skip
    return out


def _batch(
    store: UsageStore, prices: PriceBook, f: Filters, cfg: Settings, scale: float
) -> list[Recommendation]:
    out = []
    for r in store.aggregate(f, ["app", "provider"], ["requests", "cost"], limit=200):
        p = next((x for x in prices.all() if x.provider == r["provider"] and x.batch_discount), None)
        if p is None or r["requests"] < max(100, cfg.min_requests):
            continue
        af = Filters(
            since=f.since, until=f.until, eq={**f.eq, "app": r["app"], "provider": r["provider"]}, tags=f.tags
        )
        shape = store.workload_shape(af)
        if shape["batch"] or shape["no_user"] / shape["n"] < 0.7 or shape["no_stream"] / shape["n"] < 0.9:
            continue
        saving = round(r["cost"] * (p.batch_discount or 0) * scale)
        out.append(Recommendation(
            id=_rid("batch", r["app"], r["provider"]), kind="batch_api", title=f"Use the {r['provider']} batch API for {r['app']}",
            summary=f"{r['app']} sends {r['requests']:,} non-streaming calls with no end user attached, which looks like background work. {r['provider']}'s batch API charges {p.batch_discount:.0%} less if you can wait up to a day.",
            monthly_savings_micro=saving, confidence="low", effort="high", app=r["app"],
            evidence={"requests": r["requests"], "share_without_user": round(shape["no_user"] / shape["n"], 3), "discount": p.batch_discount},
            steps=["Confirm nobody waits on these results in real time.", "Submit them as a batch job and poll for completion.", "Mark them batch=true in TokenProfiler so the discount is reflected."],
        ))  # fmt: skip
    return out


def _context_growth(store: UsageStore, f: Filters, cfg: Settings, scale: float) -> list[Recommendation]:
    sessions: dict[str, list[Any]] = {}
    for r in store.session_calls(f):
        sessions.setdefault(r["session_id"], []).append(r)
    by_app: dict[str, list[tuple[float, int, int]]] = {}  # app -> (growth ratio, calls, trimmable cost)
    for calls in sessions.values():
        if len(calls) < 6:
            continue
        first = max(1, calls[0]["prompt_tokens"])
        ratio = calls[-1]["prompt_tokens"] / first
        extra_share = sum(
            max(0, c["prompt_tokens"] - first) / max(1, c["prompt_tokens"]) * c["cost_total"] for c in calls
        )
        by_app.setdefault(calls[0]["app"], []).append((ratio, len(calls), round(extra_share)))
    out = []
    for app, items in by_app.items():
        if len(items) < 10 or statistics.median(r for r, _, _ in items) < 2.5:
            continue
        saving = round(
            0.4 * sum(c for _, _, c in items) * scale
        )  # assume 40% of the accumulated history can be dropped or summarized
        out.append(Recommendation(
            id=_rid("context_growth", app), kind="context_growth", title=f"Conversation history keeps growing in {app}",
            summary=f"Across {len(items)} sessions the prompt grows {statistics.median(r for r, _, _ in items):.1f}x from the first call to the last, because the whole history is resent every turn.",
            monthly_savings_micro=saving, confidence="medium", effort="medium", app=app,
            evidence={"sessions": len(items), "median_growth": round(statistics.median(r for r, _, _ in items), 2), "median_calls": statistics.median(n for _, n, _ in items)},
            steps=["Summarize older turns into a short running summary and keep only the last few verbatim.", "Drop tool results once they have been used.", "Cache the stable start of the conversation (system prompt and early turns)."],
        ))  # fmt: skip
    return out


def _unpriced(store: UsageStore, f: Filters, unpriced: int) -> list[Recommendation]:
    if not unpriced:
        return []
    rows = store.aggregate(
        Filters(since=f.since, until=f.until, eq=dict(f.eq), tags=f.tags),
        ["model"],
        ["requests", "unpriced"],
        limit=500,
    )
    models = [r["model"] for r in rows if r["unpriced"]]
    return [Recommendation(
        id=_rid("unpriced"), kind="pricing", title=f"{unpriced:,} calls have no price", summary=f"No price is known for: {', '.join(models[:6])}. Their cost shows as $0 and every total is understated by their real spend.",
        monthly_savings_micro=0, confidence="high", effort="low", evidence={"models": models[:20], "calls": unpriced},
        steps=["Add a price in Settings, or in the price file (TP_PRICING_FILE); past events are re-priced when you ask for a re-price."],
    )]  # fmt: skip


__all__ = ["Recommendation", "Settings", "analyze"]
