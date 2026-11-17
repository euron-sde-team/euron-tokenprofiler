# Integrating your application

## Python SDK

```python
from openai import OpenAI
from tokenprofiler import Profiler

tp = Profiler("http://localhost:8380", "tp_...", app="support-bot")  # an `ingest` key is enough
client = tp.wrap_openai(OpenAI())  # every call is recorded; return values are unchanged

client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello"}],
    tp_prompt_id="greeting",  # optional: group calls by template
)
tp.close()  # flushes the queue at shutdown
```

Anthropic clients work the same way with `tp.wrap_anthropic(client)`; its streamed responses are not recorded by the wrapper
(their usage arrives in different events), so use `tp.track` for those.

`tp.track(model, prompt_tokens, completion_tokens, ...)` records usage manually; `tp.stats` reports queued, sent, dropped
and rejected counts. Streaming works: the wrapper requests usage and hides that final chunk from you.

**Failure behaviour:** the queue is bounded (oldest dropped when full), sending happens on a background thread, retries
use backoff, and nothing the SDK does can raise into your code or add latency to your call.

## The proxy (any language)

```python
OpenAI(
    base_url="http://localhost:8380/proxy/v1",
    api_key="tp_...",
    default_headers={
        "X-Upstream-Api-Key": "<provider key>",
        "X-TP-App": "support-bot",
        "X-TP-Prompt-Id": "greeting",
    },
)
```

Chat completions and embeddings are forwarded to `TP_UPSTREAM_BASE_URL` and recorded. Provider errors are passed through
and recorded as errors. If the profiler cannot reach the provider you get `502 upstream_unreachable`.

## Raw HTTP

`POST /v1/events` with up to 1,000 events. Useful fields: `id` (makes retries safe), `model`, `app`, `environment`,
`prompt_id`, `user_id`, `session_id`, `prompt_tokens`, `completion_tokens`, `cached_tokens`, `cache_write_tokens`,
`reasoning_tokens`, `latency_ms`, `status`, `error_type`, `finish_reason`, `tags`, and optionally `prompt_text` (used only
to estimate tokens and group by shape; not stored). Omit token counts and send `prompt_text` to have them estimated.

## Labelling well

Costs are only as useful as the labels. Set `app` and `environment` everywhere, give each prompt template a stable
`prompt_id`, and use `tags` (for example `team`, `feature`) for anything you want to group by.
