"""Synthetic but realistic LLM traffic, for demos, screenshots and tests. Every event is tagged ``data=synthetic``.

Five applications with deliberately different shapes, so every part of the product has something to find:

  support-bot        gpt-4o, long fixed system prompt, short answers, many repeated FAQs, multi-turn sessions, a few errors
  docs-assistant     claude-3-5-sonnet RAG: big prompts, some answers cut off by max_tokens
  code-review        gpt-4.1, huge diffs, and on one day a retry storm (the cost anomaly)
  nightly-summarizer gpt-4o background job: no user, no streaming, runs at night (a batch-API candidate)
  search-index       text-embedding-3-large where -small would do, plus search-rerank on gpt-4o-mini

Deterministic for a given seed. Event ids are derived from the seed and a counter, so re-seeding does not duplicate.
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterator

from .events import EventIn

DAY_MS = 86_400_000
HOUR_MS = 3_600_000
TEAMS = {
    "support-bot": "support",
    "docs-assistant": "platform",
    "code-review": "devex",
    "nightly-summarizer": "data",
    "search-index": "search",
}
FAQ_HASHES = [f"{i:02x}fa90{i:04x}".ljust(16, "0")[:16] for i in range(40)]


def _business_hour_weights() -> list[float]:
    return [0.2 + 1.8 * math.exp(-((h - 14) ** 2) / 18) for h in range(24)]


def _lognorm(rng: random.Random, median: float, sigma: float) -> float:
    return rng.lognormvariate(math.log(median), sigma)


class _Gen:
    def __init__(self, end_ms: int, days: int, scale: float, seed: int) -> None:
        self.rng = random.Random(seed)
        self.seed, self.end_ms, self.days, self.scale = seed, end_ms, days, scale
        self.n = 0
        self.hours = _business_hour_weights()
        self.session = 0

    def eid(self) -> str:
        self.n += 1
        return f"demo-{self.seed}-{self.n:07d}"

    def ts(self, day_start: int, hour: int) -> int:
        return day_start + hour * HOUR_MS + int(self.rng.random() * HOUR_MS)

    def hour_of_business_day(self) -> int:
        return self.rng.choices(range(24), self.hours)[0]

    def count(self, per_day: float, weekday: int, growth: float = 1.0) -> int:
        weekend = 0.35 if weekday >= 5 else 1.0
        mean = per_day * self.scale * weekend * growth
        return max(0, round(self.rng.gauss(mean, math.sqrt(max(mean, 1)))))


def generate(end_ms: int, days: int = 45, scale: float = 1.0, seed: int = 7) -> Iterator[EventIn]:
    """Yields events for the ``days`` days ending at ``end_ms``. ``scale`` multiplies every volume (0.05 for tests)."""
    g = _Gen(end_ms, days, scale, seed)
    end_day = end_ms // DAY_MS
    for i in range(days):
        day = end_day - days + 1 + i
        day_start = day * DAY_MS
        weekday = (day + 3) % 7  # 1970-01-01 was a Thursday
        growth = 1 + 0.012 * i  # the support bot slowly grows
        yield from _support(g, day_start, weekday, growth, end_ms)
        yield from _docs(g, day_start, weekday, end_ms)
        yield from _code(g, day_start, weekday, end_ms, storm=(i == days - 3))
        yield from _nightly(g, day_start, weekday, end_ms)
        yield from _search(g, day_start, weekday, end_ms)


def _ok(g: _Gen, ts: int, end_ms: int) -> bool:
    return ts <= end_ms


def _support(g: _Gen, day_start: int, weekday: int, growth: float, end_ms: int) -> Iterator[EventIn]:
    r = g.rng
    for env, share in (("production", 1.0), ("staging", 0.08)):
        for _ in range(g.count(420 * share, weekday, growth)):
            ts = g.ts(day_start, g.hour_of_business_day())
            if not _ok(g, ts, end_ms):
                continue
            repeat = r.random() < 0.13
            sys_tokens = 1800 + r.randint(0, 40)  # a long, mostly fixed system prompt
            q = r.randint(25, 90)
            err = r.random() < 0.015
            session = None
            hist = 0
            if (
                r.random() < 0.35 and env == "production"
            ):  # a multi-turn conversation: history grows each turn
                g.session += 1
                session = f"s{g.seed}-{g.session}"
                turns = r.randint(3, 9)
                for t in range(turns):
                    hist_t = hist
                    comp = int(_lognorm(r, 110, 0.5))
                    yield _event(
                        g,
                        ts + t * 45_000,
                        "support-bot",
                        env,
                        "gpt-4o",
                        sys_tokens + q + hist_t,
                        comp,
                        prompt_id="support-chat",
                        session=session,
                        stream=True,
                        user=f"u{r.randint(1, 900)}",
                        lat=_lognorm(r, 1400, 0.4),
                        team="support",
                        feature="chat",
                        max_tokens=1024,
                    )
                    hist += q + comp
                continue
            comp = 0 if err else int(_lognorm(r, 95, 0.5))
            yield _event(
                g, ts, "support-bot", env, "gpt-4o", sys_tokens + q, comp, prompt_id="support-faq", stream=True,
                user=f"u{r.randint(1, 900)}", lat=_lognorm(r, 1100, 0.45), team="support", feature="faq", max_tokens=1024,
                prompt_hash=(r.choice(FAQ_HASHES[: 1 + int(r.paretovariate(1.2)) % 40]) if repeat else f"{r.getrandbits(64):016x}"),
                error=("rate_limit_exceeded" if err else None)
            )  # fmt: skip


def _docs(g: _Gen, day_start: int, weekday: int, end_ms: int) -> Iterator[EventIn]:
    r = g.rng
    for _ in range(g.count(150, weekday)):
        ts = g.ts(day_start, g.hour_of_business_day())
        if not _ok(g, ts, end_ms):
            continue
        prompt = int(_lognorm(r, 3400, 0.35))
        cut = r.random() < 0.06
        comp = 512 if cut else int(_lognorm(r, 330, 0.5))
        yield _event(
            g, ts, "docs-assistant", "production", "claude-3-5-sonnet-20241022", prompt, min(comp, 512), prompt_id="docs-rag", stream=True,
            user=f"u{r.randint(1, 400)}", lat=_lognorm(r, 2600, 0.4), team="platform", feature="rag", max_tokens=512,
            finish="length" if cut else "stop", cached=0,
        )  # fmt: skip


def _code(g: _Gen, day_start: int, weekday: int, end_ms: int, storm: bool) -> Iterator[EventIn]:
    r = g.rng
    n = g.count(80, weekday)
    for _ in range(n):
        ts = g.ts(day_start, g.hour_of_business_day())
        if not _ok(g, ts, end_ms):
            continue
        prompt = int(_lognorm(r, 6200, 0.45))
        comp = int(_lognorm(r, 780, 0.5))
        yield _event(
            g,
            ts,
            "code-review",
            "production",
            "gpt-4.1",
            prompt,
            comp,
            prompt_id="code-review-diff",
            user=f"dev{r.randint(1, 60)}",
            lat=_lognorm(r, 5200, 0.4),
            team="devex",
            feature="pr-review",
            max_tokens=4096,
        )
    if storm:  # a bug retries the same huge request in a loop for a few hours
        for _ in range(round(1500 * g.scale)):
            ts = g.ts(day_start, r.randint(9, 13))
            if not _ok(g, ts, end_ms):
                continue
            prompt = int(_lognorm(r, 14000, 0.2))
            failed = r.random() < 0.55
            yield _event(
                g, ts, "code-review", "production", "gpt-4.1", prompt, 0 if failed else int(_lognorm(r, 700, 0.3)), prompt_id="code-review-diff",
                user="dev7", lat=_lognorm(r, 30000, 0.3) if failed else _lognorm(r, 6000, 0.3), team="devex", feature="pr-review", max_tokens=4096,
                error="timeout" if failed else None,
            )  # fmt: skip


def _nightly(g: _Gen, day_start: int, weekday: int, end_ms: int) -> Iterator[EventIn]:
    r = g.rng
    for _ in range(round(700 * g.scale)):
        ts = day_start + 2 * HOUR_MS + int(r.random() * 2 * HOUR_MS)
        if not _ok(g, ts, end_ms):
            continue
        yield _event(
            g,
            ts,
            "nightly-summarizer",
            "production",
            "gpt-4o",
            int(_lognorm(r, 2600, 0.3)),
            int(_lognorm(r, 300, 0.3)),
            prompt_id="nightly-digest",
            lat=_lognorm(r, 3000, 0.3),
            team="data",
            feature="digest",
            max_tokens=1024,
        )


def _search(g: _Gen, day_start: int, weekday: int, end_ms: int) -> Iterator[EventIn]:
    r = g.rng
    for _ in range(g.count(900, weekday)):
        ts = g.ts(day_start, g.hour_of_business_day())
        if _ok(g, ts, end_ms):
            yield _event(
                g,
                ts,
                "search-index",
                "production",
                "text-embedding-3-large",
                int(_lognorm(r, 480, 0.4)),
                0,
                endpoint="embedding",
                prompt_id="index-chunk",
                lat=_lognorm(r, 180, 0.4),
                team="search",
                feature="embed",
            )
    for _ in range(g.count(1500, weekday)):
        ts = g.ts(day_start, g.hour_of_business_day())
        if _ok(g, ts, end_ms):
            yield _event(
                g,
                ts,
                "search-index",
                "production",
                "gpt-4o-mini",
                int(_lognorm(r, 420, 0.3)),
                int(_lognorm(r, 22, 0.3)),
                prompt_id="rerank-v2",
                user=f"u{r.randint(1, 2000)}",
                lat=_lognorm(r, 350, 0.4),
                team="search",
                feature="rerank",
                max_tokens=64,
            )
    if g.rng.random() < 0.3:  # a handful of calls to a model nobody added a price for
        ts = g.ts(day_start, 11)
        if _ok(g, ts, end_ms):
            yield _event(
                g,
                ts,
                "search-index",
                "production",
                "acme-internal-llm",
                800,
                120,
                prompt_id="experiment",
                lat=900.0,
                team="search",
                feature="experiment",
            )


def _event(
    g: _Gen, ts: int, app: str, env: str, model: str, prompt: int, completion: int, *, prompt_id: str, lat: float, team: str, feature: str,
    stream: bool = False, user: str | None = None, session: str | None = None, max_tokens: int | None = None, finish: str | None = None,
    error: str | None = None, endpoint: str = "chat", prompt_hash: str | None = None, cached: int = 0, cache_hit: bool = False,
) -> EventIn:  # fmt: skip
    ok = error is None
    return EventIn(
        id=g.eid(), ts=ts, model=model, app=app, environment=env, endpoint=endpoint, prompt_tokens=prompt, completion_tokens=completion,
        cached_tokens=min(cached, prompt), latency_ms=round(lat, 1), ttft_ms=round(lat * 0.25, 1) if stream and ok else None,
        status="ok" if ok else "error", error_type=error, http_status=None if ok else (429 if error == "rate_limit_exceeded" else 504),
        finish_reason=finish or ("stop" if ok else None), stream=stream, max_tokens=max_tokens, prompt_id=prompt_id, prompt_hash=prompt_hash,
        user_id=user, session_id=session, cache_hit=cache_hit, tags={"team": team, "feature": feature, "data": "synthetic"},
    )  # fmt: skip
