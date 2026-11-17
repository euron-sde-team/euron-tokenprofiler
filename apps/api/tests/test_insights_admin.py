from __future__ import annotations

import asyncio
import random
from typing import Any

import httpx
import pytest
from conftest import ROOT_KEY, Stack, ev
from tokenprofiler_api.main import create_app
from tokenprofiler_api.settings import ApiSettings
from tokenprofiler_core.events import now_ms
from tokenprofiler_core.pricing import ModelPrice, PriceBook, Usage
from tokenprofiler_core.store import DAY_MS


async def get(stack: Stack, path: str) -> Any:
    r = await stack.api.get(path)
    assert r.status_code == 200, f"{path}: {r.text}"
    return r.json()


# ── recommendations ──────────────────────────────────────────────────────────────────────────────────────────
async def test_the_optimizer_finds_the_planted_opportunities_with_evidence_and_steps(stack: Stack) -> None:
    await stack.seed_demo(days=30, scale=0.2)
    r = await get(stack, "/api/recommendations")
    kinds = {(x["kind"], x["app"]) for x in r["recommendations"]}
    assert {
        ("model_downgrade", "support-bot"),
        ("batch_api", "nightly-summarizer"),
        ("quality", "docs-assistant"),
        ("error_waste", "code-review"),
    } <= kinds
    top = r["recommendations"][0]
    assert top["monthly_savings_micro"] > 0 and top["evidence"] and top["steps"] and len(top["id"]) == 12
    assert (
        0 < r["potential_monthly_savings_micro"] <= r["monthly_cost_micro"] * 0.7 + 1
        and "overlap" in r["overlap_note"]
    )


async def test_a_recommendation_can_be_dismissed_and_restored(stack: Stack) -> None:
    await stack.seed_demo(days=30, scale=0.2)
    first = (await get(stack, "/api/recommendations"))["recommendations"][0]["id"]
    assert (await stack.api.post(f"/api/recommendations/{first}/dismiss")).status_code == 204
    after = await get(stack, "/api/recommendations")
    assert first not in [x["id"] for x in after["recommendations"]] and after["dismissed_count"] == 1
    assert first in [
        x["id"] for x in (await get(stack, "/api/recommendations?include_dismissed=true"))["recommendations"]
    ]
    assert (await stack.api.delete(f"/api/recommendations/{first}/dismiss")).status_code == 204
    assert first in [x["id"] for x in (await get(stack, "/api/recommendations"))["recommendations"]]
    assert (await stack.api.post("/api/recommendations/bad%20id!/dismiss")).status_code == 422


# ── budgets and alerts ───────────────────────────────────────────────────────────────────────────────────────
async def spend(stack: Stack, app: str, usd: float, when: int | None = None, n: int = 20) -> None:
    per = round(usd * 1e6 / n)  # gpt-4o output is 10 micro-dollars per token
    await stack.post_events(
        [
            ev(
                id=f"{app}-{when}-{i}-{random.random()}",
                app=app,
                ts=when or now_ms(),
                prompt_tokens=1,
                completion_tokens=per // 10,
            )
            for i in range(n)
        ]
    )


async def test_budget_crud_and_live_status(stack: Stack) -> None:
    r = await stack.api.post(
        "/api/budgets",
        json={"name": "Support", "limit_usd": 100, "scope": {"app": "support"}, "warn_at": 0.5},
    )
    assert r.status_code == 201
    b = r.json()
    assert (
        b["status"]["status"] == "ok" and b["limit_micro"] == 100_000_000 and b["scope"] == {"app": "support"}
    )
    await spend(stack, "support", 60)
    await spend(stack, "other", 500)
    listed = (await get(stack, "/api/budgets"))["data"][0]
    assert (
        listed["status"]["spent_micro"] == pytest.approx(60_000_000, rel=0.01)
        and listed["status"]["status"] in ("warning", "projected_overrun")
        and listed["status"]["used_pct"] == pytest.approx(60, abs=1)
    )
    upd = await stack.api.put(
        f"/api/budgets/{b['id']}", json={"name": "Support", "limit_usd": 50, "scope": {"app": "support"}}
    )
    assert upd.json()["status"]["status"] == "exceeded"
    assert (await stack.api.delete(f"/api/budgets/{b['id']}")).status_code == 204
    assert (await stack.api.delete(f"/api/budgets/{b['id']}")).status_code == 404 and (
        await get(stack, "/api/budgets")
    )["data"] == []


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "", "limit_usd": 5},
        {"name": "x", "limit_usd": 0},
        {"name": "x", "limit_usd": 5, "scope": {"password": "a"}},
        {"name": "x", "limit_usd": 5, "webhook_url": "http://insecure.example.com/h"},
        {"name": "x", "limit_usd": 5, "webhook_url": "https://u:p@x.example.com/h"},
    ],
)
async def test_invalid_budgets_are_rejected(stack: Stack, bad: dict[str, Any]) -> None:
    assert (await stack.api.post("/api/budgets", json=bad)).status_code == 422


async def test_alerts_are_raised_once_per_threshold_and_can_be_acknowledged(stack: Stack) -> None:
    await stack.api.post("/api/budgets", json={"name": "Tiny", "limit_usd": 10, "scope": {"app": "burner"}})
    await spend(stack, "burner", 12)
    assert (await stack.api.post("/api/alerts/evaluate")).json()["raised"] >= 1
    assert (await stack.api.post("/api/alerts/evaluate")).json()["raised"] == 0, (
        "the same crossing is not alerted twice"
    )
    alerts = (await get(stack, "/api/alerts"))["data"]
    a = next(x for x in alerts if x["kind"] == "budget")
    assert a["severity"] == "critical" and "exceeded" in a["title"] and a["detail"]["status"] == "exceeded"
    assert (await stack.api.post(f"/api/alerts/{a['id']}/ack")).status_code == 204
    assert all(x["id"] != a["id"] for x in (await get(stack, "/api/alerts"))["data"])
    assert any(
        x["id"] == a["id"] for x in (await get(stack, "/api/alerts?include_acknowledged=true"))["data"]
    )
    assert (await stack.api.post("/api/alerts/99999/ack")).status_code == 404
    assert "tokenprofiler_alerts_total" in (await stack.api.get("/metrics")).text


async def test_a_spend_spike_raises_an_anomaly_alert(stack: Stack) -> None:
    today = now_ms() // DAY_MS
    for d in range(1, 21):
        await spend(stack, "steady", 5, when=(today - d) * DAY_MS + 3_600_000, n=10)
    await spend(stack, "steady", 80, when=(today - 1) * DAY_MS + 7_200_000, n=10)
    await stack.api.post("/api/alerts/evaluate")
    alerts = (await get(stack, "/api/alerts"))["data"]
    spike = [a for a in alerts if a["kind"] == "anomaly"]
    assert spike and "steady" in spike[0]["title"] and spike[0]["detail"]["ratio"] > 5


async def test_budget_webhooks_are_delivered_and_the_target_is_checked_again_at_delivery(
    stack: Stack,
) -> None:
    sent: list[dict[str, Any]] = []

    def hook(req: httpx.Request) -> httpx.Response:
        import json

        sent.append(json.loads(req.content))
        return httpx.Response(200)

    stack.state.http = httpx.AsyncClient(transport=httpx.MockTransport(hook))
    stack.state.settings.allow_private_webhooks = (
        True  # the saved URL is a test host; delivery re-validates it
    )
    await stack.api.post(
        "/api/budgets",
        json={
            "name": "Hooked",
            "limit_usd": 5,
            "scope": {"app": "hk"},
            "webhook_url": "https://hooks.example.com/tp",
        },
    )
    await spend(stack, "hk", 9)
    await stack.api.post("/api/alerts/evaluate")
    assert (
        sent
        and sent[0]["kind"] == "budget"
        and sent[0]["severity"] == "critical"
        and "Hooked" in sent[0]["title"]
    )
    assert (await get(stack, "/api/alerts"))["data"][0]["delivered"] is True

    # DNS now points somewhere internal: delivery must refuse, and the alert stays undelivered for retry
    stack.state.settings.allow_private_webhooks = False
    sent.clear()
    await stack.api.post(
        "/api/budgets",
        json={
            "name": "Rebound",
            "limit_usd": 5,
            "scope": {"app": "rb"},
            "webhook_url": "https://localhost/tp",
        },
    )
    await spend(stack, "rb", 9)
    await stack.api.post("/api/alerts/evaluate")
    assert (
        sent == []
        and next(a for a in (await get(stack, "/api/alerts"))["data"] if "Rebound" in a["title"])["delivered"]
        is False
    )


# ── pricing ──────────────────────────────────────────────────────────────────────────────────────────────────
async def test_the_price_book_lists_builtin_prices_and_shows_overrides(stack: Stack) -> None:
    p = await get(stack, "/api/pricing")
    assert p["unit"] == "USD per 1M tokens" and p["as_of"] and len(p["data"]) >= 35
    r = await stack.api.put(
        "/api/pricing/Acme-LLM-2025-01-01", json={"input": 0.5, "output": 1.5, "provider": "acme"}
    )
    assert r.status_code == 200 and r.json()["id"] == "acme-llm" and r.json()["overridden"] is True
    assert (
        next(x for x in (await get(stack, "/api/pricing"))["data"] if x["id"] == "acme-llm")["overridden"]
        is True
    )
    assert (await stack.api.put("/api/pricing/x", json={"input": -1, "output": 1})).status_code == 422
    assert (
        await stack.api.put("/api/pricing/x", json={"input": 1, "output": 1, "surprise": 1})
    ).status_code == 422


async def test_pricing_an_unknown_model_then_repricing_fixes_history_exactly(stack: Stack) -> None:
    rng = random.Random(4)
    events = [
        ev(
            id=f"r{i}",
            model="acme-llm",
            prompt_tokens=(p := rng.randint(10, 5_000)),
            completion_tokens=rng.randint(0, 2_000),
            cached_tokens=rng.randint(0, p) // 2,
            batch=rng.random() < 0.2,
            cache_hit=rng.random() < 0.1,
            ts=now_ms() - 1000,
        )
        for i in range(200)
    ]
    await stack.post_events(events)
    assert (await get(stack, "/api/usage/summary?metrics=cost&metrics=unpriced"))["data"][0] == {
        "cost": 0,
        "unpriced": 200,
    }
    await stack.api.put(
        "/api/pricing/acme-llm",
        json={"input": 0.55, "output": 1.9, "cached_input": 0.11, "batch_discount": 0.5},
    )
    assert (await stack.api.post("/api/pricing/reprice", json={"model": "acme-llm"})).json()[
        "events_updated"
    ] == 200
    book = PriceBook()
    book.set(
        ModelPrice(id="acme-llm", provider="x", input=0.55, output=1.9, cached_input=0.11, batch_discount=0.5)
    )
    for e in events:
        stored = await get(stack, f"/api/events/{e['id']}")
        want = book.cost(
            "acme-llm",
            Usage(e["prompt_tokens"], e["completion_tokens"], e["cached_tokens"]),
            batch=e["batch"],
        )
        if e["cache_hit"]:
            assert stored["cost_total"] == 0 and stored["priced"] is True
        else:
            assert (
                stored["cost_total"] == want.total
                and stored["cost_input"] == want.input
                and stored["cost_output"] == want.output
            ), f"{e['id']}: SQL re-pricing must equal the Python cost to the micro-dollar"
    assert (await get(stack, "/api/usage/summary?metrics=unpriced"))["data"][0]["unpriced"] == 0
    assert (await stack.api.post("/api/pricing/reprice", json={"model": "never-priced"})).status_code == 404


async def test_overrides_survive_a_restart_and_can_be_removed(stack: Stack) -> None:
    await stack.api.put("/api/pricing/gpt-4o", json={"input": 1.0, "output": 2.0})
    await stack.post_events([ev(id="p1", prompt_tokens=1_000_000, completion_tokens=0)])
    assert (await get(stack, "/api/events/p1"))["cost_total"] == 1_000_000, (
        "the override applies to new events"
    )
    app2 = create_app(
        ApiSettings(
            _env_file=None,
            data_dir=stack.tmp / "data",
            api_key_pepper="test-pepper-123",
            bootstrap_api_key=ROOT_KEY,
            upstream_base_url="http://mock/v1",
        ),
        http_client=stack.mock,
    )
    async with app2.router.lifespan_context(app2):
        assert app2.state.tp.prices.resolve("gpt-4o").input == 1.0
    assert (await stack.api.delete("/api/pricing/gpt-4o")).status_code == 204
    assert (
        stack.state.prices.resolve("gpt-4o").input == 2.5
        and (await stack.api.delete("/api/pricing/gpt-4o")).status_code == 404
    )


# ── prompt lab ───────────────────────────────────────────────────────────────────────────────────────────────
async def test_prompt_lab_returns_a_breakdown_findings_savings_and_a_price_table(stack: Stack) -> None:
    messy = (
        "You are a helpful assistant.   Please   answer   carefully   and   concisely.   \n\n\n\n" * 20
        + "Rules apply to every answer you give here.\n" * 5
    )
    r = await stack.api.post(
        "/api/prompts/analyze",
        json={
            "text": messy,
            "model": "gpt-4o",
            "expected_completion_tokens": 200,
            "requests_per_month": 50_000,
        },
    )
    b = r.json()
    assert (
        r.status_code == 200
        and b["estimated"] is True
        and b["tokens_saved"] > 0
        and b["optimized_tokens"] < b["total_tokens"]
    )
    assert {f["kind"] for f in b["findings"]} >= {"duplicate_content", "whitespace"} and b["optimized_text"]
    assert (
        b["monthly_savings_micro"]
        == (b["cost_per_request_micro"] - b["optimized_cost_per_request_micro"]) * 50_000
        > 0
    )
    assert (
        b["price_table"][0]["total_micro"] <= b["price_table"][-1]["total_micro"]
        and b["segments"][0]["role"] == "user"
    )
    chat = await stack.api.post(
        "/api/prompts/analyze",
        json={
            "messages": [{"role": "system", "content": "Be terse. " * 100}, {"role": "user", "content": "Hi"}]
        },
    )
    assert {s["role"] for s in chat.json()["segments"]} == {"system", "user"}
    assert (await stack.api.post("/api/prompts/analyze", json={})).status_code == 422
    assert (
        await stack.api.post("/api/prompts/analyze", json={"text": "x", "messages": []})
    ).status_code == 422


# ── reports, ops, system ─────────────────────────────────────────────────────────────────────────────────────
async def test_a_report_is_archived_to_s3_and_listed(stack: Stack) -> None:
    await stack.post_events([ev(id="rp1", ts=now_ms() - 1000), ev(id="rp2", app="other", ts=now_ms() - 1000)])
    r = await stack.api.post("/api/reports/archive?label=weekly&range=30d")
    assert r.status_code == 200
    body = r.json()
    assert body["bucket"] == "test-reports" and body["key"].startswith("reports/") and body["rows"] >= 1
    import json

    saved = json.loads(stack.s3.objects[("test-reports", body["key"])])
    assert saved["totals"]["requests"] == 2 and saved["prices_as_of"]
    assert (await get(stack, "/api/reports"))["data"][0]["key"] == body["key"]
    stack.state.s3 = None
    assert (await stack.api.post("/api/reports/archive")).status_code == 503


async def test_health_readiness_metrics_and_system(stack: Stack) -> None:
    async with stack.client(None) as anon:
        assert (await anon.get("/healthz")).json() == {"status": "ok"}
        ready = (await anon.get("/readyz")).json()
        assert ready["status"] == "ready" and ready["proxy"] is True
    await stack.post_events([ev(id="m1")])
    text = (await stack.api.get("/metrics")).text
    assert (
        'tokenprofiler_events_ingested_total{source="api"} 1.0' in text
        and "tokenprofiler_http_requests_total" in text
    )
    s = await get(stack, "/api/system")
    assert (
        s["bounds"]["events"] == 1
        and s["features"]["proxy"]
        and s["features"]["reports"]
        and s["prices_as_of"]
        and s["retention_days"] == 400
    )


async def test_cors_allows_the_console_origin_only(stack: Stack) -> None:
    async with stack.client(None) as c:
        ok = await c.options(
            "/api/overview",
            headers={"Origin": "http://localhost:3300", "Access-Control-Request-Method": "GET"},
        )
        bad = await c.options(
            "/api/overview",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
        )
    assert (
        ok.headers["access-control-allow-origin"] == "http://localhost:3300"
        and "access-control-allow-origin" not in bad.headers
    )


async def test_retention_deletes_only_events_older_than_the_limit(stack: Stack) -> None:
    now = now_ms()
    await stack.post_events([ev(id="ancient", ts=now - 500 * DAY_MS), ev(id="recent", ts=now - DAY_MS)])
    deleted = await asyncio.to_thread(
        stack.state.usage.delete_before, now - stack.state.settings.retention_days * DAY_MS
    )
    assert (
        deleted == 1
        and (await stack.api.get("/api/events/ancient")).status_code == 404
        and (await stack.api.get("/api/events/recent")).status_code == 200
    )


async def test_a_dead_reports_bucket_is_a_clear_502_not_a_500(stack: Stack) -> None:
    class Broken:
        def put_object(self, **kw: Any) -> None:
            raise ConnectionError("endpoint unreachable")

    stack.state.s3 = Broken()
    r = await stack.api.post("/api/reports/archive")
    assert r.status_code == 502 and r.json()["error"]["code"] == "reports_unavailable"
    assert (await get(stack, "/api/reports"))["data"] == [], (
        "nothing is recorded for a report that was not written"
    )
