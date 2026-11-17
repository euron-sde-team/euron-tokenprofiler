# tokenprofiler (SDK)

The Python client for [Euron TokenProfiler](https://github.com/euron-sde-team/euron-tokenprofiler). It records LLM usage
without ever blocking or failing your call: a bounded queue (oldest dropped when full), a background sender, retries with
backoff, and idempotent event ids.

```python
from openai import OpenAI
from tokenprofiler import Profiler

tp = Profiler("http://localhost:8380", "tp_...", app="support-bot")  # an `ingest` key is enough
client = tp.wrap_openai(OpenAI())  # every call is recorded; return values are unchanged
client.chat.completions.create(
    model="gpt-4o", messages=[{"role": "user", "content": "Hi"}], tp_prompt_id="greeting"
)
tp.close()  # flush at shutdown
```

Also: `wrap_openai_async`, `wrap_anthropic`, and `tp.track(model, prompt_tokens, completion_tokens, ...)`. `tp.stats` reports
queued, sent, dropped and rejected events. See the [integration guide](../../docs/guides/integrate.md).
