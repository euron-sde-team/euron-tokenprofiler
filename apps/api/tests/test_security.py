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
