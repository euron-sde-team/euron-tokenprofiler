from __future__ import annotations

import pytest
from conftest import ROOT_KEY, Stack, ev
from tokenprofiler_api.auth import RateLimiter
from tokenprofiler_api.settings import ApiSettings, apply_aws_secrets
from tokenprofiler_api.webhooks import WebhookError, check_url


async def test_requests_without_a_valid_key_are_rejected(stack: Stack) -> None:
    for key in (None, "tp_nope0000_wrong", "garbage"):
        async with stack.client(key) as anon:
            r = await anon.get("/api/overview")
            assert r.status_code == 401 and r.json()["error"]["type"] == "authentication_error"
    async with stack.client(None) as anon:
        assert (await anon.get("/api/system", headers={"X-API-Key": ROOT_KEY})).status_code == 200


async def test_scopes_gate_each_endpoint_family(stack: Stack) -> None:
    ingest_only = await stack.new_key(["ingest"])
    async with stack.client(ingest_only) as c:
        assert (await stack.post_events([ev(id="s1")], c)).status_code == 200
        for path in ("/api/overview", "/api/events", "/api/recommendations", "/api/budgets", "/api/pricing"):
            assert (await c.get(path)).status_code == 403, path
    reader = await stack.new_key(["read"])
    async with stack.client(reader) as c:
        assert (await c.get("/api/overview")).status_code == 200
        assert (await stack.post_events([ev()], c)).status_code == 403
        assert (await c.post("/api/budgets", json={"name": "x", "limit_usd": 5})).status_code == 403
        assert (await c.put("/api/pricing/foo", json={"input": 1, "output": 1})).status_code == 403
        assert (await c.get("/api/keys")).status_code == 403


async def test_secrets_are_shown_once_and_never_listed(stack: Stack) -> None:
    created = (await stack.api.post("/api/keys", json={"name": "ci", "scopes": ["ingest"]})).json()
    assert created["key"].startswith("tp_")
    listing = (await stack.api.get("/api/keys")).json()
    assert created["key"] not in str(listing) and "digest" not in str(listing)
    assert (await stack.api.post("/api/keys", json={"name": "x", "scopes": ["root"]})).status_code == 422


async def test_revoked_keys_stop_working_and_a_key_cannot_revoke_itself(stack: Stack) -> None:
    created = (await stack.api.post("/api/keys", json={"name": "temp", "scopes": ["read"]})).json()
    async with stack.client(created["key"]) as c:
        assert (await c.get("/api/system")).status_code == 200
        assert (await stack.api.delete(f"/api/keys/{created['prefix']}")).status_code == 204
        assert (await c.get("/api/system")).status_code == 401
    assert (await stack.api.delete("/api/keys/test0000")).status_code == 409
    assert (await stack.api.delete("/api/keys/nope0000")).status_code == 404


async def test_an_app_restricted_key_can_only_write_its_own_app(stack: Stack) -> None:
    key = await stack.new_key(["ingest"], apps=["support-bot"])
    async with stack.client(key) as c:
        r = await stack.post_events([ev(id="ok", app="support-bot"), ev(id="steal", app="payroll")], c)
        body = r.json()
        assert (body["accepted"], body["rejected"]) == (1, 1) and "may not use app" in body["errors"][0][
            "error"
        ]
        proxied = await c.post(
            "/proxy/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers={"X-TP-App": "payroll"},
        )
        assert proxied.status_code == 403
    assert (await stack.api.get("/api/events/steal")).status_code == 404


async def test_an_app_restricted_reader_sees_only_its_own_data_everywhere(stack: Stack) -> None:
    await stack.post_events(
        [
            ev(id="m1", app="mine", prompt_tokens=100),
            ev(id="m2", app="mine"),
            ev(id="t1", app="theirs", prompt_tokens=999_999),
        ]
    )
    key = await stack.new_key(["read"], apps=["mine"])
    async with stack.client(key) as c:
        s = (await c.get("/api/usage/summary?range=365d&group_by=app")).json()["data"]
        assert [r["app"] for r in s] == ["mine"]
        assert (await c.get("/api/events?range=365d")).json()["data"] and all(
            e["app"] == "mine" for e in (await c.get("/api/events?range=365d")).json()["data"]
        )
        assert (await c.get("/api/events/t1")).status_code == 404, (
            "a hidden event is indistinguishable from a missing one"
        )
        assert (await c.get("/api/events?range=365d&app=theirs")).status_code == 403
        assert (await c.get("/api/dimensions/app?range=365d")).json()["values"] == ["mine"]
        total = (await c.get("/api/overview?range=365d")).json()["totals"]
        assert total["requests"] == 2 and total["prompt_tokens"] < 10_000
        for path in (
            "/api/usage/timeseries?range=365d&group_by=app",
            "/api/prompts?range=365d",
            "/api/models/compare?range=365d",
            "/api/anomalies?range=365d",
            "/api/latency?range=365d&group_by=app",
            "/api/recommendations?range=365d",
        ):
            assert "theirs" not in (await c.get(path)).text, path


async def test_sql_injection_attempts_are_refused_or_neutralized(stack: Stack) -> None:
    await stack.post_events([ev(id="i1")])
    for q in (
        "group_by=app;DROP TABLE events",
        "group_by=tag:x')--",
        "metrics=cost);DROP TABLE events;--",
        "order_by=cost;DROP TABLE events",
    ):
        r = await stack.api.get(f"/api/usage/summary?range=365d&{q}")
        assert r.status_code == 422, q
    r = await stack.api.get("/api/usage/summary?range=365d&app=x'%20OR%20'1'='1")
    assert r.json()["data"][0]["requests"] == 0
    assert (await stack.api.get("/api/usage/summary?range=365d&tag=bad")).status_code == 422
    assert (await stack.api.get("/api/usage/summary?range=forever")).status_code == 422
    assert (await stack.api.get("/api/events/1%27%20OR%20%271%27=%271")).status_code == 404
    assert (await stack.api.get("/api/events?range=365d")).json()["data"], "the table survived"


async def test_oversized_and_malformed_bodies_get_clean_errors(stack: Stack) -> None:
    assert (
        await stack.api.post("/v1/events", content=b"{broken", headers={"Content-Type": "application/json"})
    ).status_code == 422
    assert (await stack.api.post("/v1/events", json={"events": []})).status_code == 422
    assert (await stack.api.post("/v1/events", json={"events": [1, "two", None]})).status_code == 422


async def test_every_error_has_the_standard_shape_and_a_request_id(stack: Stack) -> None:
    r = await stack.api.get("/api/events/nope", headers={"X-Request-ID": "trace-1"})
    e = r.json()["error"]
    assert (
        r.status_code == 404
        and e["code"] == "event_not_found"
        and e["request_id"] == "trace-1"
        and r.headers["x-request-id"] == "trace-1"
    )
    assert (await stack.api.put("/api/overview")).json()["error"]["code"] == "method_not_allowed"


def test_the_rate_limiter_windows() -> None:
    rl = RateLimiter(3)
    assert (
        [rl.allow("a", now=60.0) for _ in range(4)] == [True, True, True, False]
        and rl.allow("b", now=60.0)
        and rl.allow("a", now=121.0)
    )
    assert RateLimiter(0).allow("a")


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://x.example.com/h", "https"),
        ("http://x.example.com/h", "plain http"),
        ("https:///h", "no host"),
        ("https://user:pw@x.example.com/h", "credentials"),
    ],
)
def test_webhook_urls_are_validated(url: str, message: str) -> None:
    with pytest.raises(WebhookError, match=message):
        check_url(url, lookup=False)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "192.168.1.1", "::1", "0.0.0.0"])
def test_webhooks_cannot_target_internal_addresses(ip: str) -> None:
    with pytest.raises(WebhookError, match="non-public"):
        check_url("https://hooks.example.com/x", resolve=lambda h: [ip])
    assert check_url("https://hooks.example.com/x", allow_private=True, resolve=lambda h: [ip])
    with pytest.raises(WebhookError, match="resolved"):
        check_url("https://nowhere.example.com/x", resolve=lambda h: [])


def test_secrets_manager_overrides_the_pepper_and_bootstrap_key() -> None:
    import json

    class Fake:
        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            assert SecretId == "arn:x"
            return {
                "SecretString": json.dumps(
                    {"api_key_pepper": "from-aws", "bootstrap_api_key": "tp_boot0000_aws"}
                )
            }

    base = ApiSettings(_env_file=None, secrets_arn="arn:x", port=9999)
    out = apply_aws_secrets(base, Fake())
    assert (out.api_key_pepper, out.bootstrap_api_key, out.port) == ("from-aws", "tp_boot0000_aws", 9999)
    plain = ApiSettings(_env_file=None)
    assert apply_aws_secrets(plain, Fake()) is plain


async def test_the_optimizer_and_forecast_never_leak_other_apps_through_an_app_restricted_key(
    stack: Stack,
) -> None:
    """Regression: the optimizer rebuilt its filters and dropped the key's app allow-list. Real traffic on several apps makes
    every rule fire, so a leak would show up as another app's name or cost."""
    await stack.seed_demo(days=30, scale=0.2)
    full = (await stack.api.get("/api/recommendations?range=365d")).json()
    assert {r["app"] for r in full["recommendations"]} >= {"support-bot", "nightly-summarizer"}
    key = await stack.new_key(["read"], apps=["docs-assistant"])
    async with stack.client(key) as c:
        mine = (await c.get("/api/recommendations?range=365d")).json()
        assert {r["app"] for r in mine["recommendations"]} <= {"docs-assistant"}
        assert 0 < mine["monthly_cost_micro"] < full["monthly_cost_micro"] / 2, (
            "only this app's spend is projected"
        )
        for path in (
            "/api/overview?range=365d",
            "/api/prompts?range=365d",
            "/api/models/compare?range=365d",
            "/api/usage/summary?range=365d&group_by=app",
        ):
            body = (await c.get(path)).text
            assert "support-bot" not in body and "nightly-summarizer" not in body, path


async def test_the_dashboard_cache_is_keyed_by_visibility_so_it_can_never_leak_across_keys(
    stack: Stack,
) -> None:
    """Enable the cache, warm it as an admin who sees everything, then read the same URL as an app-restricted key."""
    stack.state.cache.ttl = 60
    await stack.post_events(
        [
            ev(id="a", app="alpha", prompt_tokens=1_000_000, completion_tokens=0),
            ev(id="b", app="beta", prompt_tokens=2_000_000, completion_tokens=0),
        ]
    )
    full = (await stack.api.get("/api/overview?range=365d")).json()
    assert full["totals"]["prompt_tokens"] == 3_000_000
    key = await stack.new_key(["read"], apps=["alpha"])
    async with stack.client(key) as c:
        mine = (await c.get("/api/overview?range=365d")).json()
        assert mine["totals"]["prompt_tokens"] == 1_000_000 and [r["app"] for r in mine["top_apps"]] == [
            "alpha"
        ]
    assert (await stack.api.get("/api/overview?range=365d")).json()["totals"]["prompt_tokens"] == 3_000_000
    assert stack.state.cache.hits >= 1, "the admin's second read was a cache hit"


async def test_concurrent_identical_requests_compute_once(stack: Stack) -> None:
    import asyncio

    from tokenprofiler_api.cache import TTLCache

    cache = TTLCache(60)
    calls = 0

    async def slow() -> int:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return 42

    assert (
        await asyncio.gather(*(cache.get_or_compute("k", slow) for _ in range(20))) == [42] * 20
        and calls == 1
    )
    clock = [0.0]
    ttl = TTLCache(10, clock=lambda: clock[0])
    assert (
        await ttl.get_or_compute("x", slow) == 42 and await ttl.get_or_compute("x", slow) == 42 and calls == 2
    )
    clock[0] = 11
    await ttl.get_or_compute("x", slow)
    assert calls == 3, "an expired entry is recomputed"
    off = TTLCache(0)
    await off.get_or_compute("y", slow)
    await off.get_or_compute("y", slow)
    assert calls == 5


def test_a_transient_secrets_manager_error_is_retried_but_a_persistent_one_fails_closed() -> None:
    import json

    class Flaky:
        def __init__(self, fail: int) -> None:
            self.fail, self.calls = fail, 0

        def get_secret_value(self, SecretId: str) -> dict[str, str]:
            self.calls += 1
            if self.calls <= self.fail:
                raise ConnectionError("secrets manager is starting")
            return {"SecretString": json.dumps({"api_key_pepper": "p", "bootstrap_api_key": "tp_boot0000_x"})}

    naps: list[float] = []
    base = ApiSettings(_env_file=None, secrets_arn="arn:x")
    ok = apply_aws_secrets(base, Flaky(2), sleep=naps.append)
    assert ok.api_key_pepper == "p" and naps == [1.0, 2.0], "two failures are retried with growing pauses"
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        apply_aws_secrets(base, Flaky(99), attempts=3, sleep=lambda s: None)


async def test_budgets_and_alerts_do_not_leak_other_teams_spend(stack: Stack) -> None:
    """Regression: a budget's status carries real spend, so an app-restricted key must only see budgets scoped to its own apps
    (an unscoped 'company total' budget reveals every app's spend)."""
    await stack.post_events(
        [
            ev(id="a", app="alpha", prompt_tokens=1, completion_tokens=500_000),
            ev(id="b", app="beta", prompt_tokens=1, completion_tokens=900_000),
        ]
    )
    await stack.api.post("/api/budgets", json={"name": "Company total", "limit_usd": 1_000})
    mine = (
        await stack.api.post(
            "/api/budgets", json={"name": "Alpha", "limit_usd": 1, "scope": {"app": "alpha"}}
        )
    ).json()
    theirs = (
        await stack.api.post("/api/budgets", json={"name": "Beta", "limit_usd": 1, "scope": {"app": "beta"}})
    ).json()
    await stack.api.post("/api/alerts/evaluate")
    key = await stack.new_key(["read", "budgets:write"], apps=["alpha"])
    async with stack.client(key) as c:
        listed = (await c.get("/api/budgets")).json()["data"]
        assert [b["name"] for b in listed] == ["Alpha"], (
            "neither the company total nor beta's budget is visible"
        )
        alerts = (await c.get("/api/alerts?include_acknowledged=true")).json()["data"]
        assert alerts and all("Alpha" in a["title"] for a in alerts), [a["title"] for a in alerts]
        assert "Beta" not in str(alerts) and "Company" not in str(alerts)
        assert (await c.post("/api/budgets", json={"name": "Sneaky", "limit_usd": 5})).status_code == 403, (
            "an unscoped budget would reveal total spend"
        )
        assert (
            await c.post("/api/budgets", json={"name": "Sneaky", "limit_usd": 5, "scope": {"app": "beta"}})
        ).status_code == 403
        assert (
            await c.post(
                "/api/budgets", json={"name": "Alpha two", "limit_usd": 5, "scope": {"app": "alpha"}}
            )
        ).status_code == 201
        assert (await c.delete(f"/api/budgets/{theirs['id']}")).status_code == 404
        assert (
            await c.put(
                f"/api/budgets/{theirs['id']}", json={"name": "x", "limit_usd": 1, "scope": {"app": "alpha"}}
            )
        ).status_code == 404
        assert (await c.delete(f"/api/budgets/{mine['id']}")).status_code == 204
    assert {b["name"] for b in (await stack.api.get("/api/budgets")).json()["data"]} >= {
        "Company total",
        "Beta",
        "Alpha two",
    }


async def test_the_budget_list_is_cached_but_never_stale_after_a_change(stack: Stack) -> None:
    stack.state.cache.ttl = 60
    assert (await stack.api.get("/api/budgets")).json()["data"] == []
    created = (await stack.api.post("/api/budgets", json={"name": "Fresh", "limit_usd": 5})).json()
    assert [b["name"] for b in (await stack.api.get("/api/budgets")).json()["data"]] == ["Fresh"], (
        "creating clears the cache"
    )
    await stack.api.put(f"/api/budgets/{created['id']}", json={"name": "Renamed", "limit_usd": 5})
    assert [b["name"] for b in (await stack.api.get("/api/budgets")).json()["data"]] == ["Renamed"]
    await stack.api.delete(f"/api/budgets/{created['id']}")
    assert (await stack.api.get("/api/budgets")).json()["data"] == []


async def test_a_value_computed_before_a_clear_is_never_cached() -> None:
    """The race behind a flaky UI test: a list request in flight while a budget is created must not re-cache the old list."""
    import asyncio

    from tokenprofiler_api.cache import TTLCache

    cache = TTLCache(60)
    started, release = asyncio.Event(), asyncio.Event()
    state = {"value": "old"}

    async def compute() -> str:
        seen = state["value"]  # the old list, read at the start, like a query that began before the write
        started.set()
        await release.wait()
        return seen

    task = asyncio.create_task(cache.get_or_compute("k", compute))
    await started.wait()
    state["value"] = "new"
    cache.clear()  # the write happens while the old computation is still running
    release.set()
    assert await task == "old", "the in-flight caller still gets its answer"

    async def fresh() -> str:
        return state["value"]

    assert await cache.get_or_compute("k", fresh) == "new", (
        "the next reader recomputes instead of reading a stale cached value"
    )
