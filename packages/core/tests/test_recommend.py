from __future__ import annotations

from typing import Any

import pytest
from helpers import BOOK, T0, ev
from tokenprofiler_core import recommend
from tokenprofiler_core.demo import generate
from tokenprofiler_core.ingest import resolve
from tokenprofiler_core.pricing import PriceBook
from tokenprofiler_core.query import Filters
from tokenprofiler_core.recommend import Settings
from tokenprofiler_core.store import DAY_MS, StoredEvent, UsageStore

NOW = T0 + 10 * DAY_MS


@pytest.fixture
def store() -> UsageStore:
    return UsageStore(":memory:")


def spread(n: int, i: int, span_days: int = 9) -> int:
    return T0 + 1_000 + i * (span_days * DAY_MS // n)


def batch(n: int, prefix: str, **kw: Any) -> list[StoredEvent]:
    return [ev(id=f"{prefix}{i}", ts=spread(n, i), **kw) for i in range(n)]


def kinds(result: dict[str, Any]) -> dict[str, recommend.Recommendation]:
    return {r.kind + ":" + (r.app or "") + ":" + (r.prompt_id or ""): r for r in result["recommendations"]}


def run(store: UsageStore, **kw: Any) -> dict[str, Any]:
    return recommend.analyze(store, BOOK, now_ms=NOW, settings=Settings(**kw) if kw else None)


def test_an_empty_store_has_no_recommendations(store: UsageStore) -> None:
    r = run(store)
    assert (
        r["recommendations"] == []
        and r["monthly_cost_micro"] == 0
        and r["potential_monthly_savings_micro"] == 0
    )


def test_expensive_model_on_short_tasks_suggests_the_cheaper_sibling_with_the_exact_arithmetic(
    store: UsageStore,
) -> None:
    store.record(batch(2_000, "d", app="triage", model="gpt-4o", prompt_tokens=400, completion_tokens=30))
    rec = kinds(run(store))["model_downgrade:triage:"]
    assert (
        rec.model == "gpt-4o" and rec.evidence["alternative"] == "gpt-4o-mini" and rec.confidence == "medium"
    )
    now_cost = 2_000 * (400 * 2.5 + 30 * 10)
    alt_cost = 2_000 * (400 * 0.15 + 30 * 0.6)
    assert rec.evidence["current_cost_micro"] == now_cost and rec.evidence["alternative_cost_micro"] == round(
        alt_cost
    )
    assert rec.monthly_savings_micro == pytest.approx((now_cost - alt_cost) * 30 / 10, rel=0.05), (
        "projected from the 10-day window to 30"
    )
    assert rec.steps and "sample" in rec.steps[0]


def test_a_model_with_no_cheaper_sibling_or_already_small_is_left_alone(store: UsageStore) -> None:
    store.record(batch(500, "s", app="x", model="gpt-4o-mini", prompt_tokens=2_000, completion_tokens=500))
    assert not [r for r in run(store)["recommendations"] if r.kind == "model_downgrade"]


def test_downgrade_needs_enough_traffic_and_enough_savings(store: UsageStore) -> None:
    store.record(batch(10, "few", app="rare", model="gpt-4o", prompt_tokens=400, completion_tokens=30))
    assert not run(store)["recommendations"]
    assert not list(run(store, min_requests=5, min_savings_micro=10**12)["recommendations"])


def test_long_outputs_lower_the_confidence_of_a_downgrade(store: UsageStore) -> None:
    store.record(batch(300, "l", app="writer", model="gpt-4o", prompt_tokens=800, completion_tokens=1_200))
    assert kinds(run(store))["model_downgrade:writer:"].confidence == "low"


def test_prompt_caching_is_suggested_for_a_large_shared_prefix_and_priced_from_the_cached_rate(
    store: UsageStore,
) -> None:
    n = 3_000  # one day of busy traffic right before "now": a call every ~29 s, so the provider's cache window is almost always warm
    store.record(
        [
            ev(
                id=f"c{i}",
                ts=NOW - DAY_MS + i * (DAY_MS // n),
                app="bot",
                model="gpt-4o",
                prompt_tokens=3_000,
                completion_tokens=100,
                prompt_id="bot-prompt",
            )
            for i in range(n)
        ]
    )
    rec = kinds(run(store))["prompt_caching:bot:bot-prompt"]
    assert (
        rec.evidence["min_prompt_tokens"] == 3_000
        and rec.evidence["estimated_shared_prefix"] == 2_100
        and rec.evidence["cached_rate_ratio"] == 0.5
    )
    assert rec.confidence == "high" and rec.effort == "low" and rec.evidence["est_hit_rate"] == 0.9
    assert rec.monthly_savings_micro == pytest.approx(n * 2_100 * 0.9 * (2.5 - 1.25) * 30 / 1, rel=0.03)


def test_infrequent_traffic_gets_a_lower_hit_rate_and_confidence(store: UsageStore) -> None:
    """1,500 calls over 10 days is one every ~10 minutes: most land after the provider's cache has expired."""
    store.record(
        batch(
            1_500,
            "slow",
            app="bot",
            model="gpt-4o",
            prompt_tokens=3_000,
            completion_tokens=100,
            prompt_id="bot-prompt",
        )
    )
    rec = kinds(run(store))["prompt_caching:bot:bot-prompt"]
    assert 0.3 < rec.evidence["est_hit_rate"] < 0.6 and rec.confidence == "medium"


def test_prompt_caching_is_not_suggested_when_already_cached_small_or_rare(store: UsageStore) -> None:
    store.record(
        batch(
            400,
            "a",
            app="cached",
            model="gpt-4o",
            prompt_tokens=3_000,
            completion_tokens=50,
            cached_tokens=2_400,
            prompt_id="p1",
        )
    )
    store.record(
        batch(400, "b", app="small", model="gpt-4o", prompt_tokens=600, completion_tokens=50, prompt_id="p2")
    )
    store.record(
        [
            ev(
                id=f"r{i}",
                ts=T0 + i * DAY_MS // 2,
                app="rare",
                model="gpt-4o",
                prompt_tokens=3_000,
                completion_tokens=50,
                prompt_id="p3",
            )
            for i in range(40)
        ]
    )
    assert not [r for r in run(store)["recommendations"] if r.kind == "prompt_caching"], (
        "already cached, too small, and too infrequent to hit the cache window"
    )


def test_no_prompt_caching_for_a_provider_without_a_cached_price(store: UsageStore) -> None:
    store.record(
        batch(
            600,
            "m",
            app="eu",
            model="mistral-large",
            prompt_tokens=3_000,
            completion_tokens=50,
            prompt_id="p",
        )
    )
    assert not [r for r in run(store)["recommendations"] if r.kind == "prompt_caching"]


def test_exact_repeats_suggest_a_response_cache_and_count_all_but_the_first_call(store: UsageStore) -> None:
    rows = [
        ev(
            id=f"q{i}",
            ts=spread(100, i),
            app="faq",
            model="gpt-4o-mini",
            prompt_tokens=2_000,
            completion_tokens=400,
            prompt_hash="a" * 16,
        )
        for i in range(100)
    ]
    rows += [
        ev(
            id=f"u{i}",
            ts=spread(100, i),
            app="faq",
            model="gpt-4o-mini",
            prompt_tokens=2_000,
            completion_tokens=400,
            prompt_hash=f"{i:016x}",
        )
        for i in range(100)
    ]
    store.record(rows)
    rec = kinds(run(store, min_savings_micro=1))["response_cache:faq:"]
    assert rec.evidence["repeated_calls"] == 99 and rec.confidence == "high"
    one_call = 2_000 * 0.15 + 400 * 0.6
    assert rec.monthly_savings_micro == pytest.approx(99 * one_call * 30 / 10, rel=0.05)


def test_truncated_answers_are_reported_as_a_quality_issue_not_a_saving(store: UsageStore) -> None:
    rows = batch(
        200, "ok", app="docs", model="gpt-4o", prompt_tokens=500, completion_tokens=100, finish_reason="stop"
    )
    rows += [
        ev(
            id=f"t{i}",
            ts=spread(20, i),
            app="docs",
            model="gpt-4o",
            prompt_tokens=500,
            completion_tokens=512,
            finish_reason="length",
            max_tokens=512,
        )
        for i in range(20)
    ]
    store.record(rows)
    rec = kinds(run(store))["quality:docs:"]
    assert (
        rec.monthly_savings_micro == 0
        and rec.evidence["truncated_calls"] == 20
        and rec.evidence["share"] == pytest.approx(20 / 220, abs=0.001)
    )


def test_failed_calls_that_were_billed_are_waste(store: UsageStore) -> None:
    rows = batch(200, "ok", app="api", model="gpt-4.1-nano", prompt_tokens=500, completion_tokens=50)
    rows += [
        ev(
            id=f"e{i}",
            ts=spread(300, i),
            app="api",
            model="gpt-4.1",
            prompt_tokens=20_000,
            completion_tokens=None,
            status="error",
            error_type="timeout",
            http_status=504,
        )
        for i in range(300)
    ]
    store.record(rows)
    rec = kinds(run(store))["error_waste:api:"]
    assert (
        rec.evidence["error_type"] == "timeout"
        and rec.evidence["failed_calls"] == 300
        and rec.evidence["wasted_micro"] == 300 * 20_000 * 2
    )


def test_background_jobs_on_a_batch_capable_provider_suggest_the_batch_api(store: UsageStore) -> None:
    store.record(batch(400, "n", app="nightly", model="gpt-4.1", prompt_tokens=2_000, completion_tokens=300))
    rec = kinds(run(store))["batch_api:nightly:"]
    assert rec.confidence == "low" and rec.evidence["discount"] == 0.5 and rec.monthly_savings_micro > 0
    store2 = UsageStore(":memory:")
    store2.record(
        batch(
            400,
            "u",
            app="interactive",
            model="gpt-4.1",
            prompt_tokens=2_000,
            completion_tokens=300,
            user_id="someone",
            stream=True,
        )
    )
    assert not [r for r in run(store2)["recommendations"] if r.kind == "batch_api"], (
        "calls with end users or streaming are interactive"
    )
    store3 = UsageStore(":memory:")
    store3.record(
        batch(
            400, "b", app="already", model="gpt-4.1", prompt_tokens=2_000, completion_tokens=300, batch=True
        )
    )
    assert not [r for r in run(store3)["recommendations"] if r.kind == "batch_api"]


def test_conversations_whose_history_keeps_growing_are_found(store: UsageStore) -> None:
    rows = []
    for s in range(15):
        for turn in range(8):
            rows.append(
                ev(
                    id=f"s{s}-{turn}",
                    ts=T0 + s * 3_600_000 + turn * 60_000,
                    app="chat",
                    model="gpt-4o",
                    prompt_tokens=500 + turn * 900,
                    completion_tokens=150,
                    session_id=f"sess{s}",
                )
            )
    store.record(rows)
    rec = kinds(run(store, min_savings_micro=1))["context_growth:chat:"]
    assert rec.evidence["sessions"] == 15 and rec.evidence["median_growth"] > 2.5


def test_flat_conversations_are_not_flagged(store: UsageStore) -> None:
    store.record(
        [
            ev(
                id=f"f{s}-{t}",
                ts=T0 + s * 3_600_000 + t * 60_000,
                app="chat",
                prompt_tokens=700,
                session_id=f"fs{s}",
            )
            for s in range(15)
            for t in range(8)
        ]
    )
    assert not [r for r in run(store, min_savings_micro=1)["recommendations"] if r.kind == "context_growth"]


def test_unpriced_models_are_reported_so_totals_are_not_trusted_blindly(store: UsageStore) -> None:
    store.record(
        [ev(id=f"x{i}", model="house-llm-3", prompt_tokens=1_000, completion_tokens=100) for i in range(5)]
    )
    rec = kinds(run(store))["pricing::"]
    assert (
        rec.evidence["models"] == ["house-llm"]
        or "house-llm-3" in rec.evidence["models"][0]
        or rec.evidence["calls"] == 5
    )


def test_the_potential_is_a_capped_ceiling_not_a_sum_of_overlapping_ideas(store: UsageStore) -> None:
    store.record(
        batch(
            3_000,
            "big",
            app="a",
            model="gpt-4o",
            prompt_tokens=3_000,
            completion_tokens=40,
            prompt_id="p",
            user_id=None,
        )
    )
    r = run(store)
    assert (
        r["sum_of_recommendations_micro"]
        > r["potential_monthly_savings_micro"]
        == pytest.approx(r["monthly_cost_micro"] * 0.7, abs=2)
    )
    assert "overlap" in r["overlap_note"]


def test_recommendation_ids_are_stable_so_they_can_be_dismissed(store: UsageStore) -> None:
    store.record(batch(2_000, "d", app="triage", model="gpt-4o", prompt_tokens=400, completion_tokens=30))
    a, b = run(store), run(store)
    assert [r.id for r in a["recommendations"]] == [r.id for r in b["recommendations"]] and all(
        len(r.id) == 12 for r in a["recommendations"]
    )


def test_scoping_to_one_app_only_analyzes_that_app(store: UsageStore) -> None:
    store.record(batch(2_000, "t", app="triage", model="gpt-4o", prompt_tokens=400, completion_tokens=30))
    store.record(batch(2_000, "o", app="other", model="gpt-4o", prompt_tokens=400, completion_tokens=30))
    scoped = recommend.analyze(store, BOOK, now_ms=NOW, base=Filters(eq={"app": "triage"}))
    assert {r.app for r in scoped["recommendations"]} == {"triage"}


def test_new_data_is_projected_from_what_exists_not_diluted_over_thirty_days(store: UsageStore) -> None:
    """Two days of data must be scaled to a month by 30/2, not reported as if it were thirty days of spend."""
    store.record(
        [
            ev(
                id=f"n{i}",
                ts=NOW - 2 * DAY_MS + i * (2 * DAY_MS // 1_000),
                app="new",
                model="gpt-4o",
                prompt_tokens=400,
                completion_tokens=30,
            )
            for i in range(1_000)
        ]
    )
    r = run(store)
    assert r["observed_days"] == pytest.approx(2.0, abs=0.1)
    assert r["monthly_cost_micro"] == pytest.approx(1_000 * (400 * 2.5 + 30 * 10) * 15, rel=0.05)


# ── the demo generator drives the whole stack ─────────────────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def demo_store() -> UsageStore:
    s = UsageStore(":memory:")
    book = PriceBook()
    s.record([resolve(e, book) for e in generate(NOW, days=30, scale=0.2, seed=3)])
    return s


def test_the_generator_is_deterministic_and_idempotent_to_reseed() -> None:
    a = [e.model_dump() for e in generate(NOW, days=3, scale=0.05, seed=9)]
    b = [e.model_dump() for e in generate(NOW, days=3, scale=0.05, seed=9)]
    assert a == b and a
    ids = [e["id"] for e in a]
    assert len(set(ids)) == len(ids)
    assert all(e["tags"]["data"] == "synthetic" and e["ts"] <= NOW for e in a)


def test_the_demo_traffic_contains_every_scenario(demo_store: UsageStore) -> None:
    apps = {r["app"] for r in demo_store.aggregate(Filters(), ["app"])}
    assert apps == {"support-bot", "docs-assistant", "code-review", "nightly-summarizer", "search-index"}
    assert (
        demo_store.count(Filters(eq={"status": "error"})) > 0
        and demo_store.count(Filters(eq={"finish_reason": "length"})) > 0
    )
    assert demo_store.aggregate(Filters(), None, ["unpriced"])[0]["unpriced"] > 0


def test_the_optimizer_finds_the_planted_opportunities_in_the_demo_data(demo_store: UsageStore) -> None:
    got = {(r.kind, r.app) for r in recommend.analyze(demo_store, BOOK, now_ms=NOW)["recommendations"]}
    assert {
        ("model_downgrade", "support-bot"),
        ("batch_api", "nightly-summarizer"),
        ("quality", "docs-assistant"),
        ("error_waste", "code-review"),
    } <= got
    assert ("batch_api", "support-bot") not in got
