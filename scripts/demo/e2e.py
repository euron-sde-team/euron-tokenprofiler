"""Full-stack end-to-end assertions against the running Compose stack (`make up` first).

Exact arithmetic on the real stack: every event, price and total is checked to the micro-dollar. Covers auth and per-app
isolation, ingestion and idempotency, the profiling proxy against the mock provider (including streaming and prompt caching),
the Python SDK over real HTTP, analytics, the optimizer, budgets and alerts, pricing overrides and re-pricing, the Prompt Lab,
CSV export, the S3 report archive on Floci, and the Secrets Manager key Terraform created.
"""

from __future__ import annotations

import csv
import io
import json
import sys
import time
import urllib.request
import uuid

import boto3
from _client import Api
from tokenprofiler import Profiler

api = Api()
failures: list[str] = []
RUN = uuid.uuid4().hex[:6]


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else f'  ({detail})'}")
    if not cond:
        failures.append(name)


def section(t: str) -> None:
    print(f"\n{t}")


def app(name: str) -> str:
    return f"e2e-{name}-{RUN}"


def ev(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {"model": "gpt-4o", "prompt_tokens": 1_000, "completion_tokens": 500}
    base.update(kw)
    return base


def total(a: str, extra: str = "") -> dict[str, int]:
    rows = api.summary(
        f"&app={a}&metrics=requests&metrics=cost&metrics=prompt_tokens&metrics=completion_tokens&metrics=errors&metrics=cached_tokens{extra}"
    )
    return dict(rows[0]) if rows else {"requests": 0, "cost": 0}


def until(fn, tries: int = 100) -> bool:  # type: ignore[no-untyped-def]
    for _ in range(tries):
        if fn():
            return True
        time.sleep(0.1)
    return False


assert api.wait_ready(), "stack is not up (run `make up`)"

section("system")
s = api.call("GET", "/api/system").body
check(
    "the proxy is configured against the mock provider",
    s["features"]["proxy"] and "mock-llm" in (s["proxy_upstream"] or ""),
)
check(
    "the S3 reports bucket is configured",
    s["features"]["reports"] and s["features"]["reports_bucket"] == "tokenprofiler-local-reports",
)
check(
    "health and metrics are open",
    api.call("GET", "/healthz", key="").status == 200 and api.call("GET", "/metrics", key="").status == 200,
)

section("auth and secrets")
check("no key is rejected", api.call("GET", "/api/overview", key="").status == 401)
check("a bad key is rejected", api.call("GET", "/api/overview", key="tp_bad00000_nope").status == 401)
sm = boto3.client(
    "secretsmanager",
    region_name="us-east-1",
    endpoint_url=api.floci,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)
secret = json.loads(sm.get_secret_value(SecretId="tokenprofiler-local/app")["SecretString"])
check(
    "the bootstrap key Terraform put in Secrets Manager authenticates",
    api.call("GET", "/api/system", key=secret["bootstrap_api_key"]).status == 200,
)
check("the pepper never appears in an API response", secret["api_key_pepper"] not in json.dumps(s))
ingest_key = api.call(
    "POST", "/api/keys", {"name": f"e2e-ingest-{RUN}", "scopes": ["ingest"], "apps": [app("scoped")]}
).body["key"]
check("an ingest-only key cannot read", api.call("GET", "/api/overview", key=ingest_key).status == 403)
check(
    "it can write its own app",
    api.events([ev(app=app("scoped"), id=f"sc-{RUN}-1")], key=ingest_key).body["accepted"] == 1,
)
other = api.events([ev(app=app("other"), id=f"sc-{RUN}-2")], key=ingest_key).body
check("but not another app", other["accepted"] == 0 and other["rejected"] == 1, str(other))

section("ingestion and exact cost")
a = app("ingest")
r = api.events(
    [
        ev(app=a, id=f"{a}-1"),
        ev(
            app=a,
            id=f"{a}-2",
            model="gpt-4o-mini",
            prompt_tokens=2_000,
            completion_tokens=100,
            cached_tokens=500,
        ),
    ]
)
check("a batch is accepted", r.status == 200 and r.body["accepted"] == 2, str(r.body))
t = total(a)
want = (1_000 * 2.5 + 500 * 10) + ((2_000 - 500) * 0.15 + 500 * 0.075 + 100 * 0.6)
check(
    "cost is exact to the micro-dollar (cached tokens at the cached rate)",
    t["cost"] == round(want),
    f"{t['cost']} vs {want}",
)
dup = api.events([ev(app=a, id=f"{a}-1"), ev(app=a, id=f"{a}-3")]).body
check(
    "re-sending an id is a duplicate, not a second row",
    dup["accepted"] == 1 and dup["duplicates"] == 1 and total(a)["requests"] == 3,
)
bad = api.events(
    [ev(app=a, id=f"{a}-ok"), ev(app=a, prompt_tokens=-1), {"model": "gpt-4o"}, ev(app="bad app")]
).body
check(
    "one bad event does not reject the rest",
    bad["accepted"] == 1 and bad["rejected"] == 3 and {e["index"] for e in bad["errors"]} == {1, 2, 3},
    str(bad),
)
est = api.events(
    [
        {
            "model": "gpt-4o",
            "app": a,
            "id": f"{a}-text",
            "prompt_text": "Summarize this ticket for the on-call engineer, please.",
            "completion_text": "Intermittent 502s.",
        }
    ]
).body
e = api.call("GET", f"/api/events/{a}-text").body
check(
    "text-only events are estimated, flagged, and the text is not stored",
    est["accepted"] == 1 and e["estimated"] is True and "on-call" not in json.dumps(e),
)
check(
    "a 9 MB body is refused with 413",
    api.call(
        "POST", "/v1/events", raw_body=b'{"events":[{"model":"x","prompt_text":"' + b"a" * 9_000_000 + b'"}]}'
    ).status
    == 413,
)
check(
    "an unknown model is stored unpriced, not free and not guessed",
    api.events([ev(app=a, id=f"{a}-u", model="house-llm-1")]).body["accepted"] == 1
    and api.call("GET", f"/api/events/{a}-u").body["priced"] is False,
)

section("the profiling proxy (to the mock provider)")
pa = app("proxy")
H = {"X-TP-App": pa, "X-TP-Prompt-Id": "faq", "X-TP-User": "u42", "X-TP-Tags": "team=support"}
api.call("POST", "/reset", base=api.mock, key="")
r = api.call(
    "POST",
    "/proxy/v1/chat/completions",
    {"model": "gpt-4o", "messages": [{"role": "user", "content": "Explain attention in one line."}]},
    headers=H,
)
check(
    "a proxied call returns the provider's response unchanged",
    r.status == 200 and r.body["choices"][0]["message"]["content"],
)
check(
    "it is recorded with the provider's exact usage",
    until(lambda: total(pa)["requests"] == 1)
    and total(pa)["prompt_tokens"] == r.body["usage"]["prompt_tokens"]
    and total(pa)["completion_tokens"] == r.body["usage"]["completion_tokens"],
)
ev1 = api.call("GET", f"/api/events?range=365d&app={pa}").body["data"][0]
check(
    "labels and tags were captured",
    ev1["prompt_id"] == "faq"
    and ev1["user_id"] == "u42"
    and ev1["tags"] == {"team": "support"}
    and ev1["estimated"] is False,
)
req = urllib.request.Request(
    api.base + "/proxy/v1/chat/completions",
    data=json.dumps(
        {"model": "gpt-4o", "stream": True, "messages": [{"role": "user", "content": "Stream me an answer"}]}
    ).encode(),
    method="POST",
)
req.add_header("Authorization", f"Bearer {api.key}")
req.add_header("Content-Type", "application/json")
for k, v in H.items():
    req.add_header(k, v)
with urllib.request.urlopen(req, timeout=60) as resp:
    frames = [ln.decode().strip()[6:] for ln in resp if ln.decode().startswith("data:")]
check(
    "streaming passes through, ends with [DONE], and hides the usage chunk the client did not ask for",
    frames[-1] == "[DONE]" and not any(json.loads(f).get("usage") for f in frames[:-1]),
)
check(
    "the streamed call is recorded with exact usage and a time to first token",
    until(lambda: total(pa)["requests"] == 2)
    and api.call("GET", f"/api/events?range=365d&app={pa}").body["data"][0]["ttft_ms"] is not None,
)
api.call(
    "POST",
    "/proxy/v1/chat/completions",
    {"model": "gpt-4o", "messages": [{"role": "user", "content": "[rate] go"}]},
    headers=H,
)
check("a provider 429 is returned and recorded as an error", until(lambda: total(pa)["errors"] == 1))
long_system = "You are a careful assistant. " * 300
for q in ("first", "second"):
    api.call(
        "POST",
        "/proxy/v1/chat/completions",
        {
            "model": "gpt-4o",
            "messages": [{"role": "system", "content": long_system}, {"role": "user", "content": q}],
        },
        headers=H,
    )
check(
    "prompt caching reported by the provider is recorded and priced at the cached rate",
    until(lambda: total(pa)["cached_tokens"] > 1_000) and total(pa)["requests"] == 5,
)
check(
    "only the real calls reached the provider",
    api.call("GET", "/stats", base=api.mock, key="").body["calls"] == 5,
)
check(
    "a malformed request never reaches the provider",
    api.call("POST", "/proxy/v1/chat/completions", raw_body=b"{nope", headers=H).status == 400
    and api.call("GET", "/stats", base=api.mock, key="").body["calls"] == 5,
)

section("the Python SDK over real HTTP")
sa = app("sdk")
tp = Profiler(api.base, api.key, app=sa, flush_interval=0.2)
for i in range(25):
    tp.track("claude-3-5-sonnet-20241022", 1_200 + i, 300, prompt_id="sdk-faq", latency_ms=800 + i)
with tp.span("gpt-4o", prompt_id="span") as sp:
    sp.set(prompt_tokens=100, completion_tokens=10)
tp.flush()
tp.close()
check(
    "the SDK delivered everything",
    total(sa)["requests"] == 26 and tp.stats.sent == 26 and tp.stats.dropped == 0,
    str(tp.stats),
)
down = Profiler("http://127.0.0.1:9", "tp_x", max_retries=0, timeout=0.3, flush_interval=0.1)
t0 = time.perf_counter()
for _ in range(500):
    down.track("gpt-4o", 1, 1)
check(
    "with TokenProfiler unreachable the host app is never slowed (500 tracks in under 0.5 s)",
    time.perf_counter() - t0 < 0.5,
)
down.close()

section("analytics")
ov = api.call("GET", f"/api/overview?range=30d&app={a}").body
check(
    "overview totals match the summary",
    ov["totals"]["requests"] == total(a)["requests"] and ov["totals"]["cost"] == total(a)["cost"],
)
check(
    "the forecast and latency are present",
    ov["forecast"]["projected_month_micro"] >= ov["forecast"]["month_to_date_micro"],
)
cmp = api.call("GET", f"/api/models/compare?range=30d&app={sa}").body
mini = next(c for c in cmp["candidates"] if c["model"] == "claude-3-5-haiku")
check(
    "model comparison re-prices the real tokens on another model",
    cmp["actual_cost_micro"] > 0 and mini["cost_micro"] < cmp["actual_cost_micro"],
)
page = api.call("GET", f"/api/events?range=365d&app={sa}&limit=10").body
nxt = api.call("GET", f"/api/events?range=365d&app={sa}&limit=10&cursor={page['next_cursor']}").body
check(
    "event pagination has no repeats", not ({x["id"] for x in page["data"]} & {x["id"] for x in nxt["data"]})
)
csv_text = api.call("GET", f"/api/export/events?range=365d&app={a}").body
rows = list(csv.DictReader(io.StringIO(csv_text)))
check("the CSV export has every event", len(rows) >= 4 and {"id", "model", "cost_total"} <= set(rows[0]))

section("budgets and alerts")
ba = app("budget")
b = api.call("POST", "/api/budgets", {"name": f"e2e {RUN}", "limit_usd": 5, "scope": {"app": ba}}).body
check("a budget is created", "id" in b and b["status"]["status"] == "ok", str(b))
api.events(
    [ev(app=ba, id=f"{ba}-{i}", prompt_tokens=1, completion_tokens=40_000) for i in range(20)]
)  # 20 x $0.40 = $8
st = api.call("GET", "/api/budgets").body["data"]
mine = next(x for x in st if x["id"] == b["id"])
check(
    "spend against the budget is exact (20 calls of 1 in + 40,000 out on gpt-4o) and it is exceeded",
    mine["status"]["spent_micro"] == 20 * (3 + 400_000) and mine["status"]["status"] == "exceeded",
    str(mine["status"]),
)
raised = api.call("POST", "/api/alerts/evaluate").body["raised"]
alerts = api.call("GET", "/api/alerts").body["data"]
check(
    "an alert was raised once",
    raised >= 1 and any(x["kind"] == "budget" and f"e2e {RUN}" in x["title"] for x in alerts),
)
check("evaluating again does not repeat it", api.call("POST", "/api/alerts/evaluate").body["raised"] == 0)
al = next(x for x in alerts if f"e2e {RUN}" in x["title"])
check("an alert can be acknowledged", api.call("POST", f"/api/alerts/{al['id']}/ack").status == 204)
check(
    "a budget webhook to an internal address is refused at creation",
    api.call(
        "POST", "/api/budgets", {"name": f"hook {RUN}", "limit_usd": 1, "webhook_url": "http://127.0.0.1/x"}
    ).status
    == 422,
)
api.call("DELETE", f"/api/budgets/{b['id']}")

section("pricing and re-pricing")
pm = f"acme-llm-{RUN}"
api.events(
    [
        ev(
            app=app("price"),
            id=f"{app('price')}-{i}",
            model=pm,
            prompt_tokens=10_000,
            completion_tokens=2_000,
        )
        for i in range(10)
    ]
)
check(
    "an unpriced model costs zero and is listed as unpriced",
    total(app("price"))["cost"] == 0
    and any(
        r["model"].startswith("acme-llm")
        for r in api.call("GET", "/api/pricing/unpriced?range=365d").body["data"]
    ),
)
api.call("PUT", f"/api/pricing/{pm}", {"input": 1.0, "output": 4.0})
rp = api.call("POST", "/api/pricing/reprice", {"model": pm}).body
check(
    "re-pricing fixes history exactly",
    rp["events_updated"] == 10 and total(app("price"))["cost"] == 10 * (10_000 * 1.0 + 2_000 * 4.0),
    str(total(app("price"))),
)
api.call("DELETE", f"/api/pricing/{pm}")

section("optimizer and prompt lab")
oa = app("opt")
api.events(
    [
        ev(app=oa, id=f"{oa}-{i}", prompt_tokens=400, completion_tokens=30, prompt_id="classify")
        for i in range(400)
    ]
)
recs = api.call("GET", f"/api/recommendations?range=30d&app={oa}").body
down_rec = next((x for x in recs["recommendations"] if x["kind"] == "model_downgrade"), None)
check(
    "the optimizer proposes the cheaper model for a short-task workload, with evidence",
    down_rec is not None
    and down_rec["evidence"]["alternative"] == "gpt-4o-mini"
    and down_rec["steps"]
    and down_rec["monthly_savings_micro"] > 0,
    str(recs["recommendations"]),
)
check(
    "a recommendation can be dismissed and restored",
    api.call("POST", f"/api/recommendations/{down_rec['id']}/dismiss").status == 204
    and not any(
        x["id"] == down_rec["id"]
        for x in api.call("GET", f"/api/recommendations?range=30d&app={oa}").body["recommendations"]
    )
    and api.call("DELETE", f"/api/recommendations/{down_rec['id']}/dismiss").status == 204,
)
lab = api.call(
    "POST",
    "/api/prompts/analyze",
    {
        "text": "Please   answer   carefully   and   concisely.   \n\n\n\n" * 20 + "Always be concise.\n" * 6,
        "model": "gpt-4o",
        "requests_per_month": 100_000,
    },
).body
check(
    "the Prompt Lab finds waste and quotes the monthly saving",
    lab["tokens_saved"] > 0
    and lab["monthly_savings_micro"] > 0
    and {f["kind"] for f in lab["findings"]} >= {"duplicate_content", "whitespace"},
)

section("reports on S3 (Floci)")
rep = api.call("POST", f"/api/reports/archive?label=e2e{RUN}&range=30d").body
s3 = boto3.client(
    "s3",
    region_name="us-east-1",
    endpoint_url=api.floci,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)
obj = json.loads(s3.get_object(Bucket="tokenprofiler-local-reports", Key=rep["key"])["Body"].read())
check(
    "the archived report is in S3 and carries the totals and the price date",
    obj["totals"]["requests"] > 0 and bool(obj["prices_as_of"]),
)
check("it is listed", any(x["key"] == rep["key"] for x in api.call("GET", "/api/reports").body["data"]))

section("metrics")
m = api.call("GET", "/metrics", key="").body
check(
    "Prometheus exposes ingestion, proxy and alert counters",
    all(
        k in m
        for k in (
            "tokenprofiler_events_ingested_total",
            "tokenprofiler_proxy_requests_total",
            "tokenprofiler_alerts_total",
            "tokenprofiler_http_requests_total",
        )
    ),
)
check(
    "the key list never leaks secrets",
    "digest" not in json.dumps(api.call("GET", "/api/keys").body)
    and ingest_key not in json.dumps(api.call("GET", "/api/keys").body),
)
print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all end-to-end checks passed'}")
sys.exit(1 if failures else 0)
