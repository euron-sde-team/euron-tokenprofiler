# tokenprofiler (SDK)

```python
from tokenprofiler import Profiler

tp = Profiler("http://localhost:8380", "tp_...", app="support-bot")
client = tp.wrap_openai(OpenAI())  # every call is recorded; return values are unchanged
```

See the repository README and docs/guides/integrate.md.
