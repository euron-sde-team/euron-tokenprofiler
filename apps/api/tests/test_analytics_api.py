from __future__ import annotations

import csv
import io
import json
from typing import Any

import pytest
from conftest import Stack, ev
from tokenprofiler_core.events import now_ms
from tokenprofiler_core.store import DAY_MS


@pytest.fixture
async def seeded(stack: Stack) -> Stack:
    await stack.seed_demo(days=30, scale=0.15)
    return stack


async def get(stack: Stack, path: str) -> Any:
    r = await stack.api.get(path)
    assert r.status_code == 200, f"{path}: {r.text}"
    return r.json()


async def test_overview_totals_match_the_store_and_include_the_trend_and_forecast(seeded: Stack) -> None:
    o = await get(seeded, "/api/overview?range=30d")
    expected = seeded.state.usage.aggregate(
        __import__("tokenprofiler_core.query", fromlist=["Filters"]).Filters(since=o["range"]["since"]),
        None,
        ["requests", "cost", "total_tokens"],
    )[0]
    assert o["totals"]["requests"] == expected["requests"] and o["totals"]["cost"] == expected["cost"] > 0
    assert o["forecast"]["projected_month_micro"] >= o["forecast"]["month_to_date_micro"] >= 0 and o[
        "forecast"
    ]["confidence"] in ("low", "medium", "high")
    assert {m["model"] for m in o["top_models"]} >= {"gpt-4o", "gpt-4.1"} and o["top_apps"][0]["cost"] >= o[
        "top_apps"
    ][-1]["cost"]
    assert o["latency"]["p95"] >= o["latency"]["p50"] > 0 and 0 < o["error_rate"] < 0.1
    assert o["cost_per_1k_requests_micro"] == round(o["totals"]["cost"] / o["totals"]["requests"] * 1000)


async def test_overview_compares_with_the_previous_equal_period(stack: Stack) -> None:
    now = now_ms()
    await stack.post_events(
        [ev(id=f"cur{i}", ts=now - 2 * DAY_MS, prompt_tokens=1_000, completion_tokens=0) for i in range(30)]
    )
    await stack.post_events(
        [ev(id=f"old{i}", ts=now - 20 * DAY_MS, prompt_tokens=1_000, completion_tokens=0) for i in range(10)]
    )
    o = await get(stack, "/api/overview?range=14d")
    assert o["totals"]["requests"] == 30 and o["previous"]["requests"] == 10, (
        "the 14 days before the window hold the 10 older events"
    )
    assert o["change"]["requests"] == pytest.approx(2.0)
    o = await get(stack, "/api/overview?range=30d")
    assert o["totals"]["requests"] == 40


async def test_summary_groups_filters_and_orders(seeded: Stack) -> None:
    rows = (await get(seeded, "/api/usage/summary?range=30d&group_by=app&metrics=requests&metrics=cost"))[
        "data"
    ]
    assert [r["cost"] for r in rows] == sorted((r["cost"] for r in rows), reverse=True)
    one = (await get(seeded, "/api/usage/summary?range=30d&group_by=model&app=support-bot&metrics=requests"))[
        "data"
    ]
    assert {r["model"] for r in one} == {"gpt-4o"}
    teams = (await get(seeded, "/api/usage/summary?range=30d&group_by=tag:team&metrics=cost"))["data"]
    assert {r["tag:team"] for r in teams} >= {"support", "platform", "devex"}
    filtered = (await get(seeded, "/api/usage/summary?range=30d&tag=team:support&metrics=requests"))["data"][
        0
    ]["requests"]
    assert (
        filtered
        == next(
            r
            for r in (await get(seeded, "/api/usage/summary?range=30d&group_by=app&metrics=requests"))["data"]
            if r["app"] == "support-bot"
        )["requests"]
    )


async def test_timeseries_has_a_point_per_day_and_can_split_by_a_dimension(seeded: Stack) -> None:
    days = (await get(seeded, "/api/usage/timeseries?range=30d&bucket=day"))["data"]
    assert 28 <= len(days) <= 31 and days == sorted(days, key=lambda r: r["day"])
    split = (await get(seeded, "/api/usage/timeseries?range=30d&bucket=day&group_by=app&top=3"))["data"]
    assert {r["app"] for r in split} <= {"other", *{r["app"] for r in split}} and "other" in {
        r["app"] for r in split
    }
    assert (await seeded.api.get("/api/usage/timeseries?bucket=week")).status_code == 422


async def test_events_paginate_with_a_cursor_and_can_be_fetched_individually(seeded: Stack) -> None:
    page1 = await get(seeded, "/api/events?range=30d&limit=40")
    assert len(page1["data"]) == 40 and page1["next_cursor"]
    page2 = await get(seeded, f"/api/events?range=30d&limit=40&cursor={page1['next_cursor']}")
    ids = [e["id"] for e in page1["data"] + page2["data"]]
    assert len(set(ids)) == 80 and page1["data"][0]["ts"] >= page2["data"][0]["ts"]
    one = await get(seeded, f"/api/events/{ids[0]}")
    assert one["id"] == ids[0] and "tags" in one
    assert (await seeded.api.get("/api/events?cursor=garbage")).status_code in (200, 422)


async def test_prompts_rank_by_spend_with_derived_averages(seeded: Stack) -> None:
    rows = (await get(seeded, "/api/prompts?range=30d"))["data"]
    top = rows[0]
    assert top["cost"] >= rows[-1]["cost"] and top["avg_prompt_tokens"] == round(
        top["prompt_tokens"] / top["requests"]
    )
    assert top["cost_per_request_micro"] == round(top["cost"] / top["requests"])
    d = await get(seeded, "/api/prompts/support-faq?range=30d")
    assert (
        d["prompt_id"] == "support-faq"
        and d["totals"]["requests"] > 0
        and d["models"][0]["model"] == "gpt-4o"
        and d["trend"]
        and "p95" in d["latency"]
    )
    assert (await seeded.api.get("/api/prompts/does-not-exist?range=30d")).status_code == 404


async def test_model_comparison_reprices_the_actual_tokens_on_every_candidate(stack: Stack) -> None:
    await stack.post_events(
        [ev(id=f"c{i}", prompt_tokens=2_000, completion_tokens=500, ts=now_ms() - 1000) for i in range(100)]
    )
    c = await get(stack, "/api/models/compare?range=30d")
    assert c["actual_cost_micro"] == 100 * (2_000 * 2.5 + 500 * 10)
    mini = next(x for x in c["candidates"] if x["model"] == "gpt-4o-mini")
    assert mini["cost_micro"] == 100 * (2_000 * 0.15 + 500 * 0.6) and mini["delta_pct"] == pytest.approx(
        mini["cost_micro"] / c["actual_cost_micro"] - 1, abs=1e-4
    )
    assert [x["cost_micro"] for x in c["candidates"]] == sorted(
        x["cost_micro"] for x in c["candidates"]
    ) and "quality" in c["note"]
    assert all(not x["model"].startswith("text-embedding") for x in c["candidates"]) and all(
        x["model"] not in ("mock", "local") for x in c["candidates"]
    )


async def test_the_planted_retry_storm_is_the_anomaly(seeded: Stack) -> None:
    a = (await get(seeded, "/api/anomalies?range=30d&group_by=app"))["data"]
    storm_day = now_ms() // DAY_MS - 2
    hit = [x for x in a if x["scope"] == "code-review" and x["direction"] == "spike"]
    assert hit and hit[0]["ratio"] > 3 and abs(hit[0]["day"] - storm_day) <= 1
    assert not [x for x in a if x["scope"] == "search-index" and x["direction"] == "spike"], (
        "steady apps are not flagged"
    )


async def test_latency_percentiles_per_model(seeded: Stack) -> None:
    lat = (await get(seeded, "/api/latency?range=30d&group_by=model"))["data"]
    assert (
        lat["gpt-4.1"]["p50"] > lat["gpt-4o-mini"]["p50"]
        and lat["gpt-4o"]["p99"] >= lat["gpt-4o"]["p95"] >= lat["gpt-4o"]["p50"]
    )


async def test_dimensions_list_values_for_filter_menus(seeded: Stack) -> None:
    assert set((await get(seeded, "/api/dimensions/app?range=30d"))["values"]) >= {
        "support-bot",
        "code-review",
    }
    assert "team" not in (await get(seeded, "/api/dimensions/model?range=30d"))["values"]
    assert (await seeded.api.get("/api/dimensions/password")).status_code == 422
    names = await get(seeded, "/api/usage/metrics")
    assert "cost" in names["metrics"] and "app" in names["dimensions"]


async def test_csv_export_streams_all_rows_and_neutralizes_formulas(stack: Stack) -> None:
    await stack.post_events(
        [
            ev(id="x1", app="plain"),
            ev(id="x2", user_id='=HYPERLINK("http://evil")', prompt_id="+cmd|' /C calc'!A0"),
            ev(id="x3", user_id="@SUM(1)"),
        ]
    )
    r = await stack.api.get("/api/export/events?range=365d&format=csv")
    assert (
        r.status_code == 200
        and r.headers["content-type"].startswith("text/csv")
        and "attachment" in r.headers["content-disposition"]
    )
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 3
    for row in rows:
        for cell in row.values():
            assert not cell.startswith(("=", "+", "@")), f"formula injection: {cell}"
    j = await stack.api.get("/api/export/events?range=365d&format=jsonl&limit=2")
    assert len(j.text.strip().split("\n")) == 2 and json.loads(j.text.split("\n")[0])["id"]
    assert (await stack.api.get("/api/export/events?format=xml")).status_code == 422


async def test_an_empty_store_gives_zeroes_not_errors(stack: Stack) -> None:
    o = await get(stack, "/api/overview")
    assert (
        o["totals"]["requests"] == 0
        and o["cost_per_1k_requests_micro"] == 0
        and o["forecast"]["projected_month_micro"] == 0
    )
    for path in (
        "/api/usage/summary",
        "/api/prompts",
        "/api/models/compare",
        "/api/anomalies",
        "/api/latency",
        "/api/recommendations",
    ):
        assert (await stack.api.get(path)).status_code == 200, path
