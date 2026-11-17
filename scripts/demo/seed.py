"""Loads a realistic month and a half of (synthetic) LLM traffic into the running stack, so the console has something to show.

Five applications with deliberately different shapes (see tokenprofiler_core.demo), three budgets in different states, and a
few real calls through the profiling proxy. Every event is tagged data=synthetic. Re-running is safe: event ids are derived
from the seed, so nothing is duplicated.

    python scripts/demo/seed.py            # 45 days at 30% volume (about 100k events)
    python scripts/demo/seed.py --small    # 14 days, 8% volume, for a quick look
"""

from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor

from _client import Api
from tokenprofiler_core.demo import generate

api = Api()
if not api.wait_ready(timeout=15):
    sys.exit("API not ready. Run `make up` first.")
small = "--small" in sys.argv
days, scale = (14, 0.08) if small else (45, 0.3)

now = int(time.time() * 1000)
events = [e.model_dump(exclude_none=True) for e in generate(now, days=days, scale=scale, seed=7)]
batches = [events[i : i + 1000] for i in range(0, len(events), 1000)]
print(f"sending {len(events):,} synthetic events in {len(batches)} batches")


def send(batch: list[dict[str, object]]) -> tuple[int, int, int]:
    r = api.events(batch)
    assert r.status == 200, r.body
    return r.body["accepted"], r.body["duplicates"], r.body["rejected"]


with ThreadPoolExecutor(4) as pool:
    results = list(pool.map(send, batches))
acc, dup, rej = (sum(x[i] for x in results) for i in range(3))
print(f"  accepted {acc:,}, already there {dup:,}, rejected {rej}")

print("budgets")
over = api.call("GET", "/api/overview?range=30d").body
projected = over["forecast"]["projected_month_micro"] / 1e6
mtd = over["forecast"]["month_to_date_micro"] / 1e6
existing = {b["name"] for b in api.call("GET", "/api/budgets").body["data"]}
for name, limit, scope in (
    ("Company total", max(50, round(projected * 1.35, -1)), {}),
    ("Support bot", max(20, round(projected * 0.5 * 0.7, -1)), {"app": "support-bot"}),
    ("Nightly summarizer", max(10, round(mtd * 0.18, 0)), {"app": "nightly-summarizer"}),
):
    if name not in existing:
        r = api.call("POST", "/api/budgets", {"name": name, "limit_usd": limit, "scope": scope})
        print(f"  {name}: ${limit:,.0f}/month ->", r.body["status"]["status"] if r.status == 201 else r.body)

print("real calls through the profiling proxy (to the mock provider)")
for i in range(30):
    r = api.call(
        "POST",
        "/proxy/v1/chat/completions",
        {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": f"Summarize ticket {i} in one line."}],
            "max_tokens": 80,
        },
        headers={
            "X-TP-App": "proxy-demo",
            "X-TP-Prompt-Id": "ticket-summary",
            "X-TP-Tags": "team=support,data=synthetic",
        },
    )
    assert r.status == 200, r.body
time.sleep(1)

print("alerts and a first report")
print("  raised:", api.call("POST", "/api/alerts/evaluate").body["raised"])
a = api.call("POST", "/api/reports/archive?label=seed&range=30d")
print("  report:", a.body.get("key", a.body))
recs = api.call("GET", "/api/recommendations?range=30d").body
print(
    f"optimizer: {len(recs['recommendations'])} recommendations, potential ${recs['potential_monthly_savings_micro'] / 1e6:,.0f}/month"
)
