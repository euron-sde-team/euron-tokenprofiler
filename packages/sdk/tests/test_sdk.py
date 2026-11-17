from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace as NS
from typing import Any

import httpx
import pytest
from tokenprofiler import Profiler, usage_from_anthropic, usage_from_gemini, usage_from_openai


class Collector:
    """A fake TokenProfiler server: records every batch, and can be told to fail."""

    def __init__(self) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self.fail_with: list[int] = []
        self.requests = 0
        self.seen: set[str] = set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests += 1
        assert req.headers["authorization"] == "Bearer tp_key"
        if self.fail_with:
            return httpx.Response(self.fail_with.pop(0))
        events = json.loads(req.content)["events"]
        dup = sum(e["id"] in self.seen for e in events)
        self.seen.update(e["id"] for e in events)
        self.batches.append(events)
        return httpx.Response(
            200, json={"accepted": len(events) - dup, "duplicates": dup, "rejected": 0, "errors": []}
        )

    @property
    def events(self) -> list[dict[str, Any]]:
        return [e for b in self.batches for e in b]


@pytest.fixture
def server() -> Collector:
    return Collector()


def make(server: Collector, **kw: Any) -> Profiler:
    return Profiler("http://tp", "tp_key", transport=httpx.MockTransport(server), flush_interval=0.05, **kw)


def openai_response(
    prompt: int = 100,
    completion: int = 20,
    cached: int = 0,
    finish: str = "stop",
    model: str = "gpt-4o-2024-08-06",
) -> Any:
    return NS(
        model=model,
        choices=[NS(finish_reason=finish, message=NS(content="hi"))],
        usage=NS(
            prompt_tokens=prompt,
            completion_tokens=completion,
            prompt_tokens_details=NS(cached_tokens=cached),
            completion_tokens_details=NS(reasoning_tokens=0),
        ),
    )


class FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response: Any = openai_response()
        self.error: BaseException | None = None
        self.chunks: list[Any] = []

    def create(self, **kw: Any) -> Any:
        self.calls.append(kw)
        if self.error:
            raise self.error
        return iter(self.chunks) if kw.get("stream") else self.response


class FakeOpenAI:
    def __init__(self) -> None:
        self.chat = NS(completions=FakeCompletions())
        self.embeddings = NS(create=lambda **kw: NS(usage=NS(prompt_tokens=42, total_tokens=42), data=[]))
        self.api_key = "sk-secret"


def test_tracked_events_are_batched_and_sent_with_defaults(server: Collector) -> None:
    tp = make(server, app="support-bot", environment="staging", tags={"team": "support"})
    for i in range(5):
        tp.track("gpt-4o", 100 + i, 20, prompt_id="faq", tags={"feature": "chat"})
    tp.flush()
    assert len(server.events) == 5 and tp.stats.sent == 5
    e = server.events[0]
    assert (e["app"], e["environment"], e["model"], e["prompt_tokens"], e["prompt_id"]) == (
        "support-bot",
        "staging",
        "gpt-4o",
        100,
        "faq",
    )
    assert (
        e["tags"] == {"team": "support", "feature": "chat"}
        and len(e["id"]) == 32
        and e["ts"] > 1_700_000_000_000
    )
    tp.close()


def test_track_never_raises_and_never_blocks_even_with_garbage_or_no_server() -> None:
    tp = Profiler("http://127.0.0.1:9", "tp_key", flush_interval=0.05, max_retries=0, timeout=0.2)
    t0 = time.perf_counter()
    for i in range(1_000):
        tp.track("gpt-4o", object(), i)  # type: ignore[arg-type]
        tp.track(None, 1, 1)  # type: ignore[arg-type]
    assert time.perf_counter() - t0 < 1.0, "tracking is a queue put, not a network call"
    tp.close()


def test_a_full_queue_drops_the_oldest_events_and_counts_them(server: Collector) -> None:
    gate = threading.Event()
    orig = server.__call__

    def slow(req: httpx.Request) -> httpx.Response:
        gate.wait(2)
        return orig(req)

    tp = Profiler(
        "http://tp",
        "tp_key",
        transport=httpx.MockTransport(slow),
        max_queue=50,
        batch_size=10,
        flush_interval=10,
    )
    for i in range(200):
        tp.track("gpt-4o", i, 1)
    assert tp.stats.dropped >= 100 and tp._q.qsize() <= 50
    gate.set()
    tp.close()


def test_transient_server_errors_are_retried_without_double_counting(server: Collector) -> None:
    server.fail_with = [503, 500]
    tp = make(server, max_retries=3)
    tp.track("gpt-4o", 10, 5)
    tp.flush()
    assert (
        server.requests == 3
        and len(server.events) == 1
        and tp.stats.sent == 1
        and tp.stats.failed_batches == 0
    )
    tp.close()


def test_an_unrecoverable_rejection_is_not_retried(server: Collector) -> None:
    server.fail_with = [401]
    tp = make(server, max_retries=3)
    tp.track("gpt-4o", 10, 5)
    tp.flush()
    assert server.requests == 1 and tp.stats.rejected == 1 and "401" in (tp.stats.last_error or "")
    tp.close()


def test_giving_up_after_the_retries_drops_the_batch_and_says_so(server: Collector) -> None:
    server.fail_with = [500] * 10
    tp = make(server, max_retries=1)
    tp.track("gpt-4o", 10, 5)
    tp.flush()
    assert tp.stats.failed_batches == 1 and tp.stats.dropped == 1
    tp.close()


def test_close_drains_whatever_is_queued(server: Collector) -> None:
    tp = Profiler("http://tp", "tp_key", transport=httpx.MockTransport(server), flush_interval=30)
    for i in range(30):
        tp.track("gpt-4o", i + 1, 1)
    tp.close()
    assert len(server.events) == 30


def test_a_profiler_without_credentials_is_a_silent_noop() -> None:
    tp = Profiler(None, None)
    assert tp.enabled is False and tp.track("gpt-4o", 1, 1) is None
    tp.flush()
    tp.close()
    assert Profiler("http://x", "k", enabled=False).enabled is False


def test_span_times_a_block_and_records_the_response(server: Collector) -> None:
    tp = make(server)
    with tp.span("gpt-4o", prompt_id="s1") as s:
        time.sleep(0.02)
        s.record(openai_response(300, 40, cached=100, finish="length"))
    with pytest.raises(RuntimeError), tp.span("gpt-4o") as s2:
        s2.set(prompt_tokens=500)
        raise RuntimeError("provider blew up")
    tp.flush()
    ok, bad = server.events
    assert (
        ok["prompt_tokens"] == 300
        and ok["cached_tokens"] == 100
        and ok["finish_reason"] == "length"
        and ok["latency_ms"] >= 20
    )
    assert (
        bad["status"] == "error"
        and bad["error_type"] == "RuntimeError"
        and bad["completion_tokens"] == 0
        and bad["prompt_tokens"] == 500
    )
    tp.close()


# ── the OpenAI wrapper ───────────────────────────────────────────────────────────────────────────────────────
def test_the_wrapper_returns_the_same_object_and_records_usage(server: Collector) -> None:
    tp, fake = make(server, app="bot"), FakeOpenAI()
    client = tp.wrap_openai(fake)
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        tp_prompt_id="greeting",
        tp_user_id="u1",
        max_tokens=64,
        temperature=0.2,
    )
    assert resp is fake.chat.completions.response, "the caller gets the provider's own response, untouched"
    assert "tp_prompt_id" not in fake.chat.completions.calls[0], (
        "TokenProfiler labels never reach the provider"
    )
    assert client.api_key == "sk-secret", "everything not wrapped still works"
    tp.flush()
    e = server.events[0]
    assert (
        e["model"],
        e["prompt_tokens"],
        e["completion_tokens"],
        e["prompt_id"],
        e["user_id"],
        e["max_tokens"],
        e["temperature"],
        e["finish_reason"],
        e["status"] if "status" in e else "ok",
    ) == ("gpt-4o-2024-08-06", 100, 20, "greeting", "u1", 64, 0.2, "stop", "ok")
    tp.close()


def test_provider_errors_are_recorded_and_re_raised_unchanged(server: Collector) -> None:
    tp, fake = make(server), FakeOpenAI()
    err = type("RateLimit", (Exception,), {"status_code": 429})("slow down")
    fake.chat.completions.error = err
    with pytest.raises(Exception, match="slow down") as info:
        tp.wrap_openai(fake).chat.completions.create(
            model="gpt-4o", messages=[{"role": "user", "content": "hi"}]
        )
    assert info.value is err
    tp.flush()
    e = server.events[0]
    assert (
        e["status"] == "error"
        and e["error_type"] == "RateLimit"
        and e["http_status"] == 429
        and e["prompt_tokens"] == 0
    )
    tp.close()


def chunk(
    content: str | None = None, finish: str | None = None, usage: Any = None, choices: bool = True
) -> Any:
    ch = [NS(delta=NS(content=content), finish_reason=finish)] if choices else []
    return NS(choices=ch, usage=usage, model="gpt-4o")


def test_streams_pass_through_unchanged_and_the_extra_usage_chunk_is_hidden_unless_requested(
    server: Collector,
) -> None:
    tp, fake = make(server), FakeOpenAI()
    usage = NS(
        prompt_tokens=50, completion_tokens=7, prompt_tokens_details=None, completion_tokens_details=None
    )
    fake.chat.completions.chunks = [
        chunk("Hel"),
        chunk("lo"),
        chunk(None, "stop"),
        chunk(usage=usage, choices=False),
    ]
    client = tp.wrap_openai(fake)
    got = list(client.chat.completions.create(model="gpt-4o", messages=[], stream=True))
    assert [c.choices[0].delta.content for c in got if c.choices] == ["Hel", "lo", None] and all(
        c.choices for c in got
    ), "the caller never sees the usage-only chunk it did not ask for"
    assert fake.chat.completions.calls[0]["stream_options"] == {"include_usage": True}, (
        "usage was requested from the provider for exact numbers"
    )
    fake.chat.completions.chunks = [chunk("a"), chunk(usage=usage, choices=False)]
    asked = list(
        client.chat.completions.create(
            model="gpt-4o", messages=[], stream=True, stream_options={"include_usage": True}
        )
    )
    assert len(asked) == 2 and not asked[-1].choices
    tp.flush()
    e = server.events[0]
    assert (
        e["stream"] is True
        and e["prompt_tokens"] == 50
        and e["completion_tokens"] == 7
        and e["ttft_ms"] is not None
        and e["finish_reason"] == "stop"
    )
    tp.close()


def test_a_stream_that_fails_midway_is_recorded_as_an_error(server: Collector) -> None:
    tp, fake = make(server), FakeOpenAI()

    def broken() -> Any:
        yield chunk("partial")
        raise ConnectionError("dropped")

    fake.chat.completions.create = lambda **kw: broken()
    with pytest.raises(ConnectionError):
        list(tp.wrap_openai(fake).chat.completions.create(model="gpt-4o", messages=[], stream=True))
    tp.flush()
    assert server.events[0]["status"] == "error" and server.events[0]["error_type"] == "ConnectionError"
    tp.close()


def test_embeddings_are_recorded_as_embedding_events(server: Collector) -> None:
    tp = make(server)
    tp.wrap_openai(FakeOpenAI()).embeddings.create(model="text-embedding-3-small", input=["a", "b"])
    tp.flush()
    e = server.events[0]
    assert e["endpoint"] == "embedding" and e["prompt_tokens"] == 42 and e["completion_tokens"] == 0
    tp.close()


def test_when_the_provider_reports_no_usage_the_prompt_text_goes_up_for_estimation(server: Collector) -> None:
    tp, fake = make(server), FakeOpenAI()
    fake.chat.completions.response = NS(model="local", choices=[NS(finish_reason="stop")], usage=None)
    tp.wrap_openai(fake).chat.completions.create(
        model="local", messages=[{"role": "user", "content": "estimate this please"}]
    )
    tp.flush()
    assert (
        server.events[0]["prompt_text"] == "estimate this please" and "prompt_tokens" not in server.events[0]
    )
    tp.close()


# ── Anthropic and adapters ───────────────────────────────────────────────────────────────────────────────────
def test_anthropic_usage_is_normalized_so_prompt_is_the_total_input() -> None:
    resp = NS(
        usage=NS(
            input_tokens=100, output_tokens=30, cache_read_input_tokens=800, cache_creation_input_tokens=50
        )
    )
    assert usage_from_anthropic(resp) == {
        "prompt_tokens": 950,
        "completion_tokens": 30,
        "cached_tokens": 800,
        "cache_write_tokens": 50,
    }
    assert usage_from_anthropic({"usage": {"input_tokens": 5, "output_tokens": 2}}) == {
        "prompt_tokens": 5,
        "completion_tokens": 2,
    }
    assert usage_from_anthropic(NS(usage=None)) == {}


def test_gemini_thoughts_count_as_output_and_cache_is_a_subset() -> None:
    resp = NS(
        usage_metadata=NS(
            prompt_token_count=1000,
            candidates_token_count=200,
            thoughts_token_count=300,
            cached_content_token_count=600,
        )
    )
    assert usage_from_gemini(resp) == {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cached_tokens": 600,
        "reasoning_tokens": 300,
    }
    assert usage_from_gemini({"usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 3}}) == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
    }


def test_openai_usage_from_dicts_and_objects_agree() -> None:
    d = {
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "prompt_tokens_details": {"cached_tokens": 6},
            "completion_tokens_details": {"reasoning_tokens": 2},
        }
    }
    assert usage_from_openai(d) == {
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "cached_tokens": 6,
        "reasoning_tokens": 2,
    }
    assert usage_from_openai(NS(usage=None)) == {} and usage_from_openai({}) == {}


def test_the_anthropic_wrapper_records_messages_create(server: Collector) -> None:
    tp = make(server)
    resp = NS(
        model="claude-3-5-sonnet-20241022",
        stop_reason="end_turn",
        usage=NS(input_tokens=10, output_tokens=5, cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )
    fake = NS(messages=NS(create=lambda **kw: resp))
    assert (
        tp.wrap_anthropic(fake).messages.create(model="claude-3-5-sonnet", messages=[], tp_prompt_id="a")
        is resp
    )
    tp.flush()
    e = server.events[0]
    assert (
        e["model"] == "claude-3-5-sonnet-20241022"
        and e["prompt_tokens"] == 10
        and e["completion_tokens"] == 5
        and e["finish_reason"] == "end_turn"
        and e["prompt_id"] == "a"
    )
    tp.close()
