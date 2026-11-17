from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from conftest import Stack

CHAT: dict[str, Any] = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Explain attention in one line."}],
}
H = {
    "X-TP-App": "support-bot",
    "X-TP-Prompt-Id": "faq",
    "X-TP-User": "u42",
    "X-TP-Tags": "team=support, feature=chat",
    "X-TP-Environment": "staging",
}


async def settle(stack: Stack, expected: int = 1) -> list[dict[str, Any]]:
    """The proxy records after responding (detached), so wait for the event to land."""
    for _ in range(100):
        rows: list[dict[str, Any]] = list((await stack.api.get("/api/events?range=365d")).json()["data"])
        if len(rows) >= expected:
            return rows
        await asyncio.sleep(0.02)
    return rows


async def test_a_proxied_call_is_forwarded_unchanged_and_recorded_with_its_labels(stack: Stack) -> None:
    r = await stack.api.post("/proxy/v1/chat/completions", json=CHAT, headers=H)
    assert (
        r.status_code == 200
        and r.json()["object"] == "chat.completion"
        and r.json()["choices"][0]["message"]["content"]
    )
    (e,) = await settle(stack)
    u = r.json()["usage"]
    assert (e["prompt_tokens"], e["completion_tokens"], e["estimated"]) == (
        u["prompt_tokens"],
        u["completion_tokens"],
        False,
    ), "the provider's own usage is recorded, not an estimate"
    assert (e["app"], e["environment"], e["prompt_id"], e["user_id"], e["status"], e["stream"]) == (
        "support-bot",
        "staging",
        "faq",
        "u42",
        "ok",
        False,
    )
    assert (
        e["tags"] == {"team": "support", "feature": "chat"}
        and e["latency_ms"] >= 0
        and e["cost_total"] > 0
        and e["finish_reason"] == "stop"
    )


async def test_streaming_is_passed_through_with_exact_usage_ttft_and_no_usage_chunk_unless_asked(
    stack: Stack,
) -> None:
    async with stack.api.stream(
        "POST", "/proxy/v1/chat/completions", json={**CHAT, "stream": True}, headers=H
    ) as r:
        text = "".join([c async for c in r.aiter_text()])
    frames = [x[6:] for x in text.split("\n\n") if x.startswith("data: ")]
    assert frames[-1] == "[DONE]" and not any(json.loads(f).get("usage") for f in frames[:-1]), (
        "the client did not ask for usage, so it does not see it"
    )
    (e,) = await settle(stack)
    assert (
        e["stream"] is True
        and e["estimated"] is False
        and e["completion_tokens"] > 5
        and e["ttft_ms"] is not None
        and e["ttft_ms"] <= e["latency_ms"]
    )


async def test_a_client_that_asks_for_stream_usage_still_gets_it(stack: Stack) -> None:
    body = {**CHAT, "stream": True, "stream_options": {"include_usage": True}}
    async with stack.api.stream("POST", "/proxy/v1/chat/completions", json=body, headers=H) as r:
        text = "".join([c async for c in r.aiter_text()])
    chunks = [json.loads(x[6:]) for x in text.split("\n\n") if x.startswith("data: ") and x != "data: [DONE]"]
    assert chunks[-1]["usage"]["completion_tokens"] > 0


async def test_provider_errors_are_returned_as_is_and_recorded_as_errors(stack: Stack) -> None:
    r = await stack.api.post(
        "/proxy/v1/chat/completions",
        json={**CHAT, "messages": [{"role": "user", "content": "[rate] please"}]},
        headers=H,
    )
    assert r.status_code == 429
    (e,) = await settle(stack)
    assert (
        e["status"] == "error"
        and e["http_status"] == 429
        and e["completion_tokens"] == 0
        and e["prompt_tokens"] > 0
    )
    s = await stack.api.post(
        "/proxy/v1/chat/completions",
        json={**CHAT, "messages": [{"role": "user", "content": "[fail]"}]},
        headers=H,
    )
    assert s.status_code == 500


async def test_a_streaming_error_before_the_first_byte_is_an_http_error_and_recorded(stack: Stack) -> None:
    r = await stack.api.post(
        "/proxy/v1/chat/completions",
        json={**CHAT, "stream": True, "messages": [{"role": "user", "content": "[fail]"}]},
        headers=H,
    )
    assert r.status_code == 500
    (e,) = await settle(stack)
    assert e["status"] == "error" and e["stream"] is True


async def test_truncated_answers_are_recorded_with_finish_reason_length(stack: Stack) -> None:
    await stack.api.post(
        "/proxy/v1/chat/completions",
        json={**CHAT, "messages": [{"role": "user", "content": "[length]"}], "max_tokens": 64},
        headers=H,
    )
    (e,) = await settle(stack)
    assert e["finish_reason"] == "length" and e["max_tokens"] == 64


async def test_prompt_caching_reported_by_the_provider_is_recorded_and_discounted(stack: Stack) -> None:
    system = (
        "You are a careful assistant. " * 300
    )  # well over 1,024 tokens: the mock caches the repeated prefix
    for q in ("first question", "second question"):
        await stack.api.post(
            "/proxy/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": q}],
            },
            headers=H,
        )
    rows = await settle(stack, 2)
    first, second = sorted(rows, key=lambda r: r["ts"])[0], sorted(rows, key=lambda r: r["ts"])[-1]
    assert first["cached_tokens"] == 0 and second["cached_tokens"] > 1_000
    assert second["cost_cached"] == round(second["cached_tokens"] * 1.25)


async def test_embeddings_are_proxied_and_recorded_as_embedding_events(stack: Stack) -> None:
    r = await stack.api.post(
        "/proxy/v1/embeddings",
        json={"model": "text-embedding-3-small", "input": ["alpha beta", "gamma"]},
        headers=H,
    )
    assert r.status_code == 200
    (e,) = await settle(stack)
    assert (
        e["endpoint"] == "embedding"
        and e["completion_tokens"] == 0
        and e["prompt_tokens"] > 0
        and e["model"] == "text-embedding-3-small"
    )


async def test_bad_requests_are_refused_before_reaching_the_provider(stack: Stack) -> None:
    assert (
        await stack.api.post(
            "/proxy/v1/chat/completions", content=b"{not json", headers={"Content-Type": "application/json"}
        )
    ).status_code == 400
    assert (await stack.api.post("/proxy/v1/chat/completions", json={"model": "gpt-4o"})).status_code == 422
    assert (await stack.mock.get("/stats")).json()["calls"] == 0


async def test_the_proxy_needs_an_upstream_and_a_key(stack: Stack) -> None:
    async with stack.client(None) as anon:
        assert (await anon.post("/proxy/v1/chat/completions", json=CHAT)).status_code == 401
    stack.state.settings.upstream_base_url = None
    r = await stack.api.post("/proxy/v1/chat/completions", json=CHAT)
    assert r.status_code == 503 and r.json()["error"]["code"] == "proxy_not_configured"


async def test_an_unreachable_provider_is_a_502_and_is_still_recorded(stack: Stack) -> None:
    import httpx

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    stack.state.http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    r = await stack.api.post("/proxy/v1/chat/completions", json=CHAT, headers=H)
    assert r.status_code == 502 and r.json()["error"]["code"] == "upstream_unreachable"
    (e,) = await settle(stack)
    assert e["status"] == "error" and e["error_type"] == "ConnectError"


async def test_a_failure_to_record_never_breaks_the_callers_request(stack: Stack) -> None:
    def broken(events: Any) -> tuple[int, int]:
        raise RuntimeError("disk full")

    stack.state.usage.record = broken
    r = await stack.api.post("/proxy/v1/chat/completions", json=CHAT, headers=H)
    assert r.status_code == 200 and r.json()["choices"]
    await asyncio.sleep(0.1)
    assert "tokenprofiler_record_failures_total 1.0" in (await stack.api.get("/metrics")).text


async def test_a_client_that_disconnects_mid_stream_is_still_recorded(stack: Stack) -> None:
    """Closing the response generator in a finally records the call even though the client never read the end."""
    from collections.abc import MutableMapping

    from tokenprofiler_api.auth import Principal
    from tokenprofiler_api.routers import proxy

    scope = {
        "type": "http",
        "asgi": {"spec_version": "2.4"},
        "method": "POST",
        "path": "/proxy/v1/chat/completions",
        "headers": [],
    }
    chunks = 0

    async def receive() -> dict[str, str]:
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        nonlocal chunks
        if message["type"] == "http.response.body" and message.get("body"):
            chunks += 1
            if chunks == 2:
                raise OSError("client went away")

    from fastapi import Request

    body = json.dumps(
        {**CHAT, "stream": True, "messages": [{"role": "user", "content": "[big] tell me everything"}]}
    ).encode()

    async def rcv() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    req = Request(
        {
            **scope,
            "headers": [(b"content-type", b"application/json"), (b"x-tp-app", b"cutoff")],
            "app": stack.app,
        },
        rcv,
    )
    resp = await proxy.chat(req, stack.state, Principal(prefix="t", name="t", scopes=["*"]))
    import contextlib

    with contextlib.suppress(Exception):
        await resp(scope, receive, send)
    rows = await settle(stack)
    assert rows and rows[0]["app"] == "cutoff" and rows[0]["stream"] is True


@pytest.mark.parametrize("path", ["/proxy/v1/models"])
async def test_models_are_passed_through(stack: Stack, path: str) -> None:
    r = await stack.api.get(path)
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "gpt-4o"
