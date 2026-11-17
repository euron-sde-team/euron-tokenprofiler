from __future__ import annotations

import threading
from pathlib import Path

import pytest
from helpers import T0, ev, many
from tokenprofiler_core.query import Filters, QueryError
from tokenprofiler_core.store import DAY_MS, HOUR_MS, UsageStore


@pytest.fixture
def store() -> UsageStore:
    return UsageStore(":memory:")


def seed(store: UsageStore) -> None:
    store.record(
        [
            ev(
                id="a1",
                app="support",
                model="gpt-4o",
                prompt_tokens=2_000,
                completion_tokens=400,
                latency_ms=900,
                prompt_id="faq",
            ),
            ev(
                id="a2",
                app="support",
                model="gpt-4o",
                prompt_tokens=2_000,
                completion_tokens=600,
                latency_ms=1_100,
                prompt_id="faq",
            ),
            ev(
                id="a3",
                app="support",
                model="gpt-4o-mini",
                prompt_tokens=500,
                completion_tokens=100,
                latency_ms=300,
                prompt_id="triage",
            ),
            ev(
                id="b1",
                app="search",
                model="claude-3-5-sonnet",
                prompt_tokens=3_000,
                completion_tokens=800,
                latency_ms=2_000,
                environment="staging",
            ),
            ev(
                id="b2",
                app="search",
                model="claude-3-5-sonnet",
                status="error",
                prompt_tokens=3_000,
                completion_tokens=None,
                http_status=529,
                error_type="overloaded",
            ),
            ev(id="c1", app="batch", model="mystery-model", prompt_tokens=100, completion_tokens=100),
        ]
    )


def test_recording_is_idempotent_by_id(store: UsageStore) -> None:
    assert store.record([ev(id="x"), ev(id="y")]) == (2, 0)
    assert store.record([ev(id="x"), ev(id="z")]) == (1, 1), (
        "a re-sent id is a duplicate, not an error and not a second row"
    )
    assert store.count() == 3


def test_a_failing_batch_is_rolled_back_entirely(store: UsageStore) -> None:
    bad = ev(id="bad")
    bad.model = None  # type: ignore[assignment]  # violates NOT NULL
    with pytest.raises(Exception, match="NOT NULL"):
        store.record([ev(id="ok1"), bad])
    assert store.count() == 0
    assert store.record([ev(id="ok1")]) == (1, 0), "the store is still usable after a failed batch"


def test_aggregate_totals_and_exact_costs(store: UsageStore) -> None:
    seed(store)
    (row,) = store.aggregate(
        Filters(), None, ["requests", "prompt_tokens", "completion_tokens", "cost", "errors", "unpriced"]
    )
    assert (
        row["requests"] == 6
        and row["prompt_tokens"] == 10_600
        and row["errors"] == 1
        and row["unpriced"] == 1
    )
    by_model = {r["model"]: r for r in store.aggregate(Filters(), ["model"], ["requests", "cost"])}
    assert by_model["gpt-4o"]["cost"] == (2_000 * 2.5 + 400 * 10) + (2_000 * 2.5 + 600 * 10)
    assert by_model["mystery-model"]["cost"] == 0


def test_group_by_orders_by_cost_by_default(store: UsageStore) -> None:
    seed(store)
    rows = store.aggregate(Filters(), ["app"])
    assert [r["app"] for r in rows] == sorted(
        (r["app"] for r in rows), key=lambda a: -next(x["cost"] for x in rows if x["app"] == a)
    )
    assert {r["app"] for r in rows} == {"support", "search", "batch"}


def test_filters_narrow_the_results(store: UsageStore) -> None:
    seed(store)
    assert store.count(Filters(eq={"app": "support"})) == 3
    assert store.count(Filters(eq={"app": "support", "model": "gpt-4o"})) == 2
    assert store.count(Filters(eq={"environment": "staging"})) == 1
    assert store.count(Filters(eq={"status": "error"})) == 1
    assert store.count(Filters(since=T0 + 2 * HOUR_MS)) == 0
    assert store.count(Filters(until=T0)) == 0


def test_time_range_is_inclusive_of_since_and_exclusive_of_until(store: UsageStore) -> None:
    store.record([ev(id="t1", ts=1_000_000_000_000), ev(id="t2", ts=1_000_000_001_000)])
    assert store.count(Filters(since=1_000_000_000_000, until=1_000_000_001_000)) == 1


def test_tags_can_be_filtered_and_grouped(store: UsageStore) -> None:
    store.record(
        [
            ev(id="g1", tags={"team": "growth", "feature": "chat"}),
            ev(id="g2", tags={"team": "growth"}),
            ev(id="p1", tags={"team": "platform"}),
            ev(id="n1"),
        ]
    )
    assert store.count(Filters(tags={"team": "growth"})) == 2
    rows = {r["tag:team"]: r["requests"] for r in store.aggregate(Filters(), ["tag:team"], ["requests"])}
    assert rows == {"growth": 2, "platform": 1, "(none)": 1}


def test_sql_injection_attempts_are_rejected_or_neutralized(store: UsageStore) -> None:
    seed(store)
    for dim in ("app; DROP TABLE events", "app) --", "tag:x') OR 1=1 --", "1", ""):
        with pytest.raises(QueryError):
            store.aggregate(Filters(), [dim])
    with pytest.raises(QueryError):
        store.aggregate(Filters(), ["app"], ["cost); DROP TABLE events; --"])
    with pytest.raises(QueryError):
        store.aggregate(Filters(), ["app"], order_by="cost; DROP TABLE events")
    with pytest.raises(QueryError):
        Filters(eq={"1=1 OR app": "x"}).where()
    with pytest.raises(QueryError):
        Filters(tags={"a') OR ('1": "x"}).where()
    assert store.count(Filters(eq={"app": "x' OR '1'='1"})) == 0, "values are bound, never interpolated"
    assert store.count() == 6, "the table is intact"


def test_group_by_is_capped(store: UsageStore) -> None:
    with pytest.raises(QueryError, match="at most 3"):
        store.aggregate(Filters(), ["app", "model", "environment", "status"])


def test_latency_percentiles_interpolate_and_skip_missing(store: UsageStore) -> None:
    store.record([ev(id=f"l{i}", latency_ms=float(i * 100)) for i in range(1, 11)] + [ev(id="nolat")])
    p = store.latency_percentiles(Filters())["all"]
    assert p["p50"] == 550.0 and p["p95"] == pytest.approx(955.0) and p["count"] == 10.0
    assert store.latency_percentiles(Filters(), "model")["gpt-4o"]["p99"] == pytest.approx(991.0)


def test_timeseries_fills_by_bucket_and_splits_by_dimension(store: UsageStore) -> None:
    rows = [
        ev(id=f"d{d}{m}", ts=T0 + d * DAY_MS + 1_000, model=m)
        for d in range(3)
        for m in ("gpt-4o", "gpt-4o-mini")
    ]
    store.record(rows)
    plain = store.timeseries(Filters(), "day")
    assert [r["requests"] for r in plain] == [2, 2, 2] and plain[0]["day"] == T0 // DAY_MS
    split = store.timeseries(Filters(), "day", ["requests", "cost"], group_by="model")
    assert len(split) == 6 and {r["model"] for r in split} == {"gpt-4o", "gpt-4o-mini"}


def test_timeseries_collapses_the_long_tail_into_other(store: UsageStore) -> None:
    store.record([ev(id=f"m{i}", model=f"mystery-{i}", prompt_tokens=100 * (i + 1)) for i in range(10)])
    split = store.timeseries(Filters(), "day", ["requests"], group_by="model", top=3)
    assert {r["model"] for r in split} <= {"other", *(r["model"] for r in split)} and "other" in {
        r["model"] for r in split
    }
    assert sum(r["requests"] for r in split) == 10


def test_event_listing_paginates_newest_first_without_gaps_or_repeats(store: UsageStore) -> None:
    store.record(
        [ev(id=f"p{i:03d}", ts=T0 + (i // 3) * 1000) for i in range(25)]
    )  # many events share a timestamp
    seen: list[str] = []
    cursor = None
    for _ in range(10):
        page = store.list_events(Filters(), limit=7, before=cursor)
        if not page:
            break
        seen += [r["id"] for r in page]
        cursor = (page[-1]["ts"], page[-1]["id"])
    assert len(seen) == 25 and len(set(seen)) == 25
    expected = sorted((f"p{i:03d}" for i in range(25)), key=lambda i: (int(i[1:]) // 3, i), reverse=True)
    assert seen == expected, "newest first, ties broken by id, no matter where the page boundaries fall"


def test_get_event_and_public_shape(store: UsageStore) -> None:
    store.record([ev(id="one", tags={"k": "v"}, stream=True)])
    e = store.get_event("one")
    assert e is not None and e["tags"] == {"k": "v"} and e["stream"] is True and e["estimated"] is False
    assert store.get_event("nope") is None


def test_distinct_values(store: UsageStore) -> None:
    seed(store)
    assert set(store.distinct("app")) == {"support", "search", "batch"}
    assert store.distinct("app", Filters(eq={"environment": "staging"})) == ["search"]
    with pytest.raises(QueryError):
        store.distinct("password")


def test_repeated_prompts_find_cache_candidates(store: UsageStore) -> None:
    store.record(
        [ev(id=f"r{i}", prompt_hash="a" * 16, prompt_id="faq") for i in range(5)]
        + [ev(id="once", prompt_hash="b" * 16)]
        + [
            ev(id="err", prompt_hash="c" * 16, status="error", completion_tokens=0),
            ev(id="err2", prompt_hash="c" * 16, status="error", completion_tokens=0),
        ]
        + [
            ev(id="hit1", prompt_hash="d" * 16, cache_hit=True),
            ev(id="hit2", prompt_hash="d" * 16, cache_hit=True),
        ]
    )
    rows = store.repeated_prompts(Filters())
    assert [(r["prompt_hash"], r["n"]) for r in rows] == [("a" * 16, 5)], (
        "errors and already-cached calls are not candidates"
    )


def test_retention_deletes_old_events(store: UsageStore) -> None:
    store.record([ev(id="old", ts=T0 - 40 * DAY_MS), ev(id="new", ts=T0)])
    assert store.delete_before(T0 - 30 * DAY_MS) == 1 and store.count() == 1
    assert store.bounds()["events"] == 1


def test_concurrent_writers_and_readers_do_not_corrupt_the_store(tmp_path: Path) -> None:
    s = UsageStore(tmp_path / "t.db")
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(50):
                s.record(many(4, id=f"w{n}-{i}-"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def reader() -> None:
        try:
            for _ in range(50):
                s.aggregate(Filters(), ["model"])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)] + [
        threading.Thread(target=reader) for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and s.count() == 4 * 50 * 4


def test_the_store_persists_across_connections(tmp_path: Path) -> None:
    a = UsageStore(tmp_path / "p.db")
    a.record([ev(id="keep")])
    a.close()
    assert UsageStore(tmp_path / "p.db").count() == 1
