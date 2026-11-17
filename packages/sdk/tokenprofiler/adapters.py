"""Turn a provider's usage object into TokenProfiler's convention: prompt_tokens is the *total* input, cached and cache-write
tokens are subsets of it, completion_tokens includes reasoning. Works on SDK objects and on plain dicts."""

from __future__ import annotations

from typing import Any


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def usage_from_openai(resp: Any) -> dict[str, Any]:
    """OpenAI chat/completions/embeddings responses (and compatible servers)."""
    u = _get(resp, "usage")
    if u is None:
        return {}
    out: dict[str, Any] = {
        "prompt_tokens": _get(u, "prompt_tokens"),
        "completion_tokens": _get(u, "completion_tokens", 0),
    }
    cached = _get(_get(u, "prompt_tokens_details"), "cached_tokens")
    if cached:
        out["cached_tokens"] = int(cached)
    reasoning = _get(_get(u, "completion_tokens_details"), "reasoning_tokens")
    if reasoning:
        out["reasoning_tokens"] = int(reasoning)
    return {k: v for k, v in out.items() if v is not None}


def usage_from_anthropic(resp: Any) -> dict[str, Any]:
    """Anthropic reports input_tokens *excluding* cache reads and writes, so they are added back to make the total."""
    u = _get(resp, "usage")
    if u is None:
        return {}
    read = int(_get(u, "cache_read_input_tokens", 0) or 0)
    write = int(_get(u, "cache_creation_input_tokens", 0) or 0)
    out: dict[str, Any] = {
        "prompt_tokens": int(_get(u, "input_tokens", 0) or 0) + read + write,
        "completion_tokens": int(_get(u, "output_tokens", 0) or 0),
    }
    if read:
        out["cached_tokens"] = read
    if write:
        out["cache_write_tokens"] = write
    return out


def usage_from_gemini(resp: Any) -> dict[str, Any]:
    """Google Gemini ``usage_metadata``: thoughts are billed as output, so they join the completion count."""
    u = _get(resp, "usage_metadata") or _get(resp, "usageMetadata")
    if u is None:
        return {}
    thoughts = int(_get(u, "thoughts_token_count", 0) or _get(u, "thoughtsTokenCount", 0) or 0)
    cached = int(_get(u, "cached_content_token_count", 0) or _get(u, "cachedContentTokenCount", 0) or 0)
    out: dict[str, Any] = {
        "prompt_tokens": int(_get(u, "prompt_token_count", 0) or _get(u, "promptTokenCount", 0) or 0),
        "completion_tokens": int(
            _get(u, "candidates_token_count", 0) or _get(u, "candidatesTokenCount", 0) or 0
        )
        + thoughts,
    }
    if cached:
        out["cached_tokens"] = cached
    if thoughts:
        out["reasoning_tokens"] = thoughts
    return out


def finish_reason(resp: Any) -> str | None:
    choices = _get(resp, "choices")
    if choices:
        fr = _get(choices[0], "finish_reason")
        return str(fr) if fr else None
    stop = _get(resp, "stop_reason")
    return str(stop) if stop else None
