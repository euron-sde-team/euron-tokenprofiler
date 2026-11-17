"""The mock provider is test infrastructure, so its behaviour (usage, caching, failure triggers) is pinned down."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mock_llm.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(latency_ms=0))


def chat(client: TestClient, content: str, **extra: object) -> dict[str, Any]:
    r = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": content}], **extra},
    )
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def test_reports_usage_that_adds_up(client: TestClient) -> None:
    u = chat(client, "hello there")["usage"]
    assert u["prompt_tokens"] > 0 and u["completion_tokens"] > 0
    assert u["total_tokens"] == u["prompt_tokens"] + u["completion_tokens"]


def test_the_same_prompt_gets_the_same_answer(client: TestClient) -> None:
    assert (
        chat(client, "same")["choices"][0]["message"]["content"]
        == chat(client, "same")["choices"][0]["message"]["content"]
    )


def test_a_failure_trigger_returns_the_error_and_a_rate_trigger_a_429(client: TestClient) -> None:
    def post(text: str) -> int:
        r = client.post(
            "/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": text}]}
        )
        return int(r.status_code)

    assert post("x [fail]") == 500
    assert post("x [rate]") == 429
    assert client.post("/v1/chat/completions", json={"model": "gpt-4o"}).status_code == 400


def test_length_trigger_truncates(client: TestClient) -> None:
    assert chat(client, "go [length]")["choices"][0]["finish_reason"] == "length"


def test_a_long_repeated_prefix_is_reported_as_cached_the_second_time(client: TestClient) -> None:
    system = "You are a careful assistant. " * 400

    def msgs(q: str) -> list[dict[str, str]]:
        return [{"role": "system", "content": system}, {"role": "user", "content": q}]

    first = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": msgs("one")}).json()[
        "usage"
    ]
    second = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": msgs("two")}).json()[
        "usage"
    ]
    assert first["prompt_tokens_details"]["cached_tokens"] == 0
    assert second["prompt_tokens_details"]["cached_tokens"] >= 1024


def test_streaming_ends_with_a_usage_chunk_when_asked(client: TestClient) -> None:
    def stream(opts: dict[str, Any] | None) -> list[dict[str, Any]]:
        body: dict[str, Any] = {
            "model": "gpt-4o",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        }
        if opts:
            body["stream_options"] = opts
        r = client.post("/v1/chat/completions", json=body)
        return [
            json.loads(line[6:])
            for line in r.text.splitlines()
            if line.startswith("data: ") and line != "data: [DONE]"
        ]

    assert any(c.get("usage") for c in stream({"include_usage": True}))


def test_stats_count_calls_and_reset_clears_them(client: TestClient) -> None:
    chat(client, "a")
    chat(client, "b")
    assert client.get("/stats").json()["calls"] == 2
    client.post("/reset")
    assert client.get("/stats").json()["calls"] == 0


def test_embeddings_report_usage(client: TestClient) -> None:
    r = client.post(
        "/v1/embeddings", json={"model": "text-embedding-3-small", "input": ["a b c", "d e"]}
    ).json()
    assert len(r["data"]) == 2 and r["usage"]["prompt_tokens"] > 0
