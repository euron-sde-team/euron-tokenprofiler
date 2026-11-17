"""A deterministic stand-in for an LLM provider, speaking the OpenAI wire format.

It reports realistic ``usage`` (estimated with the same tokenizer model TokenProfiler uses), simulates **prompt caching**
(a repeated prompt prefix of 1,024+ tokens comes back as ``cached_tokens`` for five minutes, like OpenAI), streams with
``stream_options.include_usage``, and counts every call (``GET /stats``) so tests can prove what reached the provider.

Prompt triggers for failure paths: ``[fail]`` 500, ``[rate]`` 429, ``[length]`` a truncated answer, ``[slow]`` waits 2 s,
``[cut]`` drops the connection mid-stream, ``[big]`` answers with a long reply.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from tokenprofiler_core.tokens import estimate, family_for_model

CACHE_TTL_S = 300
CACHE_MIN_TOKENS = 1024
WORDS = [
    "the",
    "model",
    "reads",
    "each",
    "token",
    "and",
    "predicts",
    "the",
    "next",
    "one",
    "using",
    "attention",
    "over",
    "the",
    "whole",
    "context",
    "so",
    "far",
]


def _text(body: dict[str, Any]) -> str:
    out = []
    for m in body.get("messages") or []:
        c = m.get("content")
        out.append(
            c
            if isinstance(c, str)
            else " ".join(str(p.get("text", "")) for p in c or [] if isinstance(p, dict))
        )
    return "\n".join(out)


def _reply(prompt: str, big: bool) -> str:
    seed = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
    n = 400 if big else 20 + seed % 60
    return " ".join(WORDS[(seed >> (i % 40)) % len(WORDS)] for i in range(n))


def create_app(latency_ms: int | None = None) -> FastAPI:
    delay = (latency_ms if latency_ms is not None else int(os.environ.get("MOCK_LATENCY_MS", "50"))) / 1000
    app = FastAPI(title="Mock LLM", version="0.1.0")
    state: dict[str, Any] = {"calls": 0, "embedding_calls": 0, "by_model": {}, "prefix_seen": {}}

    def usage_for(body: dict[str, Any], completion: str) -> dict[str, Any]:
        family = family_for_model(str(body.get("model", "")))
        text = _text(body)
        prompt_tokens = estimate(text, family) + 3 * len(body.get("messages") or [])
        completion_tokens = estimate(completion, family)
        cached = 0
        if prompt_tokens >= CACHE_MIN_TOKENS:  # the shared prefix: everything but the last message
            msgs = body.get("messages") or []
            prefix = (
                "\n".join(str(m.get("content")) for m in msgs[:-1])
                if len(msgs) > 1
                else text[: len(text) * 3 // 4]
            )
            key = hashlib.sha256(prefix.encode()).hexdigest()
            seen = state["prefix_seen"].get(key)
            now = time.time()
            if seen and now - seen < CACHE_TTL_S:
                cached = (estimate(prefix, family) // 128) * 128  # providers cache in blocks
            state["prefix_seen"][key] = now
        return {
            "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens,
            "prompt_tokens_details": {"cached_tokens": min(cached, prompt_tokens)}, "completion_tokens_details": {"reasoning_tokens": 0},
        }  # fmt: skip

    def check(body: dict[str, Any]) -> str:
        if not body.get("messages"):
            raise HTTPException(400, "messages required")
        prompt = _text(body)
        state["calls"] += 1
        state["by_model"][body.get("model", "")] = state["by_model"].get(body.get("model", ""), 0) + 1
        if "[fail]" in prompt or body.get("model") == "mock-error":
            raise HTTPException(500, "the mock provider failed on purpose")
        if "[rate]" in prompt:
            raise HTTPException(429, "rate limit reached")
        return prompt

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> Any:
        body = await request.json()
        prompt = check(body)
        await asyncio.sleep(2.0 if "[slow]" in prompt else delay)
        reply = _reply(prompt, "[big]" in prompt)
        finish = "stop"
        max_tokens = body.get("max_tokens") or body.get("max_completion_tokens")
        if "[length]" in prompt:
            reply, finish = reply[:60], "length"
        elif max_tokens and estimate(reply, family_for_model(str(body.get("model", "")))) > max_tokens:
            reply, finish = reply[: int(max_tokens) * 4], "length"
        usage = usage_for(body, reply)
        base = {
            "id": f"chatcmpl-mock-{state['calls']}",
            "created": int(time.time()),
            "model": body.get("model", "mock"),
        }
        if not body.get("stream"):
            return {
                **base,
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": reply}, "finish_reason": finish}
                ],
                "usage": usage,
            }

        async def gen() -> AsyncIterator[str]:
            def chunk(delta: dict[str, Any], fin: str | None = None) -> str:
                return (
                    "data: "
                    + json.dumps(
                        {
                            **base,
                            "object": "chat.completion.chunk",
                            "choices": [{"index": 0, "delta": delta, "finish_reason": fin}],
                        }
                    )
                    + "\n\n"
                )

            yield chunk({"role": "assistant", "content": ""})
            for i, w in enumerate(reply.split(" ")):
                await asyncio.sleep(0.002)
                if "[cut]" in prompt and i == 5:
                    raise ConnectionError("mock provider dropped the connection")
                yield chunk({"content": w + " "})
            yield chunk({}, finish)
            if (body.get("stream_options") or {}).get("include_usage"):
                yield (
                    "data: "
                    + json.dumps({**base, "object": "chat.completion.chunk", "choices": [], "usage": usage})
                    + "\n\n"
                )
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> dict[str, Any]:
        body = await request.json()
        texts = body.get("input") or []
        texts = [texts] if isinstance(texts, str) else texts
        state["embedding_calls"] += 1
        n = sum(estimate(str(t), "cl100k") for t in texts)
        return {
            "object": "list",
            "model": body.get("model", "mock-embed"),
            "data": [{"object": "embedding", "index": i, "embedding": [0.0] * 8} for i in range(len(texts))],
            "usage": {"prompt_tokens": n, "total_tokens": n},
        }

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [{"id": "gpt-4o", "object": "model"}, {"id": "gpt-4o-mini", "object": "model"}],
        }

    @app.get("/stats")
    async def stats() -> dict[str, Any]:
        return {
            "calls": state["calls"],
            "embedding_calls": state["embedding_calls"],
            "by_model": state["by_model"],
        }

    @app.post("/reset")
    async def reset() -> dict[str, bool]:
        state.update(calls=0, embedding_calls=0, by_model={}, prefix_seen={})
        return {"ok": True}

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


def serve() -> None:
    uvicorn.run(
        "mock_llm.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104
        port=int(os.environ.get("MOCK_PORT", "8390")),
    )


if __name__ == "__main__":
    serve()
