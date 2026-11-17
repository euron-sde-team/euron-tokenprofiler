from __future__ import annotations

import asyncio

from conftest import NOW, Stack, ev

SUMMARY = "/api/usage/summary?range=365d&metrics=requests&metrics=cost&metrics=prompt_tokens"


async def total(stack: Stack, qs: str = "") -> dict[str, int]:
    r = await stack.api.get(f"{SUMMARY}{qs}")
    return dict(r.json()["data"][0]) if r.json()["data"] else {"requests": 0, "cost": 0}


async def test_a_batch_is_stored_and_costed_exactly(stack: Stack) -> None:
    r = await stack.post_events(
        [ev(id="a1"), ev(id="a2", model="gpt-4o-mini", prompt_tokens=2_000, completion_tokens=100)]
    )
    assert r.status_code == 200 and r.json() == {"accepted": 2, "duplicates": 0, "rejected": 0, "errors": []}
    t = await total(stack)
    assert t["requests"] == 2 and t["cost"] == (1_000 * 2.5 + 500 * 10) + (2_000 * 0.15 + 100 * 0.6)


async def test_resending_the_same_ids_is_idempotent(stack: Stack) -> None:
    batch = [ev(id="dup1"), ev(id="dup2")]
    assert (await stack.post_events(batch)).json()["accepted"] == 2
    again = (await stack.post_events([*batch, ev(id="new")])).json()
    assert (again["accepted"], again["duplicates"]) == (1, 2)
    assert (await total(stack))["requests"] == 3


async def test_one_bad_event_does_not_reject_the_rest_and_the_error_names_the_index_and_field(
    stack: Stack,
) -> None:
    r = await stack.post_events(
        [ev(id="good1"), ev(id="bad", prompt_tokens=-5), {"model": "gpt-4o"}, ev(id="good2", app="has space")]
    )
    body = r.json()
    assert r.status_code == 200 and (body["accepted"], body["rejected"]) == (1, 3)
    by_index = {e["index"]: e["error"] for e in body["errors"]}
    assert "prompt_tokens" in by_index[1] and "prompt_tokens" in by_index[2] and "app" in by_index[3]


async def test_a_batch_with_nothing_valid_is_a_422_with_details(stack: Stack) -> None:
    r = await stack.post_events([{"model": ""}, {"nope": 1}])
    assert (
        r.status_code == 422
        and r.json()["error"]["code"] == "all_events_invalid"
        and len(r.json()["error"]["errors"]) == 2
    )


async def test_text_is_estimated_and_flagged_and_never_stored(stack: Stack) -> None:
    r = await stack.post_events(
        [
            {
                "id": "t1",
                "model": "gpt-4o",
                "prompt_text": "Summarize the quarterly report for the board, please.",
                "completion_text": "Revenue grew 12%.",
                "app": "docs",
            }
        ]
    )
    assert r.json()["accepted"] == 1
    e = (await stack.api.get("/api/events/t1")).json()
    assert (
        e["estimated"] is True
        and e["prompt_tokens"] > 5
        and e["prompt_id"].startswith("auto:")
        and e["prompt_hash"]
    )
    assert "quarterly" not in str(e), "the prompt text is used to count tokens and is then discarded"


async def test_batch_size_and_body_size_are_limited(stack: Stack) -> None:
    stack.state.settings.max_batch_events = 5
    r = await stack.post_events([ev(id=f"b{i}") for i in range(6)])
    assert r.status_code == 413 and r.json()["error"]["code"] == "batch_too_large"
    stack.state.settings.max_request_bytes = 2_048
    big = await stack.post_events([ev(id="x", prompt_text="a" * 10_000)])
    assert big.status_code == 413 and big.json()["error"]["code"] == "payload_too_large"


async def test_an_old_event_keeps_its_own_timestamp(stack: Stack) -> None:
    await stack.post_events([ev(id="old", ts=NOW - 100 * 86_400_000)])
    assert (await stack.api.get("/api/events/old")).json()["ts"] == NOW - 100 * 86_400_000


async def test_the_ingest_rate_limit_applies_per_key(stack: Stack) -> None:
    stack.state.limiter.rpm = 2
    key = await stack.new_key(["ingest"])
    async with stack.client(key) as c:
        codes = [(await stack.post_events([ev()], c)).status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]


async def test_many_concurrent_writers_lose_nothing(stack: Stack) -> None:
    results = await asyncio.gather(
        *(stack.post_events([ev(id=f"w{i}-{j}") for j in range(20)]) for i in range(20))
    )
    assert all(r.status_code == 200 for r in results)
    assert (await total(stack))["requests"] == 400


async def test_an_estimate_is_free_and_stores_nothing(stack: Stack) -> None:
    r = await stack.api.post(
        "/api/estimate",
        json={
            "model": "gpt-4o",
            "text": "Hello world, this is a test prompt.",
            "expected_completion_tokens": 200,
            "requests": 1_000,
        },
    )
    b = r.json()
    assert (
        r.status_code == 200
        and b["estimated"] is True
        and b["cost_total_micro"] == b["cost_per_request_micro"] * 1_000
        and b["alternatives"][0]["total_micro"] <= b["alternatives"][-1]["total_micro"]
    )
    assert (await total(stack))["requests"] == 0
    bad = await stack.api.post("/api/estimate", json={"model": "gpt-4o"})
    assert bad.status_code == 422


async def test_unknown_models_are_stored_unpriced_and_listed(stack: Stack) -> None:
    await stack.post_events([ev(id="u1", model="house-llm-1"), ev(id="u2", model="house-llm-1")])
    rows = (await stack.api.get("/api/pricing/unpriced?range=365d")).json()["data"]
    assert rows[0]["model"] == "house-llm-1" and rows[0]["unpriced"] == 2
