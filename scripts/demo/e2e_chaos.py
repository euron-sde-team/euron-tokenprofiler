"""Adversarial end-to-end tests against the running Compose stack: concurrency, a restart under write load, a dead provider,
a dead S3, hostile input, credential stuffing and tenant isolation. The stack must stay correct and stay up."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from _client import ROOT, Api
from tokenprofiler import Profiler

api = Api()
failures: list[str] = []
RUN = uuid.uuid4().hex[:6]
COMPOSE = [
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy/compose/compose.yaml"),
    "-f",
    str(ROOT / "deploy/compose/compose.floci.yaml"),
]


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'} {name}{'' if cond else f'  ({detail})'}")
    if not cond:
        failures.append(name)


def section(t: str) -> None:
    print(f"\n{t}")


def sh(*args: str) -> None:
    subprocess.run([*COMPOSE, *args], capture_output=True, text=True, check=False)


def count(a: str) -> int:
    rows = api.summary(f"&app={a}&metrics=requests")
    return int(rows[0]["requests"]) if rows else 0


assert api.wait_ready(), "stack is not up (run `make up`)"

section("concurrent writers")
a = f"chaos-{RUN}"
N, BATCH, WORKERS = 40, 50, 20


def writer(i: int) -> int:
    ev = [
        {"id": f"{a}-{i}-{j}", "model": "gpt-4o", "app": a, "prompt_tokens": 1_000, "completion_tokens": 100}
        for j in range(BATCH)
    ]
    r = api.events(ev)
    return int(r.body["accepted"]) if r.status == 200 else -1


with ThreadPoolExecutor(WORKERS) as pool:
    accepted = list(pool.map(writer, range(N)))
check("every batch from 20 parallel writers is accepted", all(x == BATCH for x in accepted), str(accepted))
check("nothing is lost and nothing is double counted", count(a) == N * BATCH, str(count(a)))
with ThreadPoolExecutor(WORKERS) as pool:
    again = list(pool.map(writer, range(N)))
check(
    "replaying the same 2,000 events counts nothing twice",
    all(x == 0 for x in again) and count(a) == N * BATCH,
)
cost = api.summary(f"&app={a}&metrics=cost")[0]["cost"]
check("the total cost is exact", cost == N * BATCH * (1_000 * 2.5 + 100 * 10), str(cost))

section("restart the API while writers are active")
b = f"restart-{RUN}"
sent: list[int] = []
stop = False


def steady() -> None:
    i = 0
    while not stop:
        ev = [
            {"id": f"{b}-{i}-{j}", "model": "gpt-4o", "app": b, "prompt_tokens": 100, "completion_tokens": 10}
            for j in range(20)
        ]
        for _ in range(60):  # a client that retries the same batch (same ids) until it gets an answer
            try:
                r = api.events(ev)
                if r.status == 200:
                    sent.append(20)
                    break
            except Exception:
                pass
            time.sleep(0.25)
        i += 1


t = threading.Thread(target=steady)
t.start()
time.sleep(2)
sh("restart", "api")
check("the API comes back", api.wait_ready(timeout=90))
time.sleep(3)
stop = True
t.join()
check(
    "a retrying client loses nothing across the restart (ids make retries safe)",
    count(b) == sum(sent) > 0,
    f"{count(b)} stored vs {sum(sent)} acknowledged",
)
check("data survived the restart (volume)", count(a) == N * BATCH)
check("API keys survived the restart", api.call("GET", "/api/keys").status == 200)

section("the provider goes down")
sh("stop", "mock-llm")
pa = f"down-{RUN}"
r = api.call(
    "POST",
    "/proxy/v1/chat/completions",
    {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    headers={"X-TP-App": pa},
)
check(
    "the proxy answers 502 upstream_unreachable, quickly",
    r.status == 502 and r.error_code == "upstream_unreachable" and r.ms < 15_000,
    f"{r.status} {r.ms:.0f}ms",
)
time.sleep(1)
check(
    "the failed call is still recorded as an error",
    count(pa) == 1 and api.summary(f"&app={pa}&metrics=errors")[0]["errors"] == 1,
)
check(
    "ingestion and analytics are unaffected",
    api.events([{"model": "gpt-4o", "app": pa, "prompt_tokens": 1, "completion_tokens": 1}]).status == 200
    and api.call("GET", "/api/overview").status == 200,
)
sh("start", "mock-llm")
time.sleep(4)
check(
    "when the provider returns, the proxy works again",
    api.call(
        "POST",
        "/proxy/v1/chat/completions",
        {"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        headers={"X-TP-App": pa},
    ).status
    == 200,
)

section("S3 goes down")
sh("stop", "floci")
rr = api.call("POST", "/api/reports/archive?label=chaos&range=30d")
check(
    "archiving fails cleanly with a 502, not a crash",
    rr.status == 502 and rr.error_code == "reports_unavailable",
    f"{rr.status} {rr.body}",
)
check(
    "everything else keeps working",
    api.events([{"model": "gpt-4o", "app": pa, "prompt_tokens": 1, "completion_tokens": 1}]).status == 200
    and api.call("GET", "/readyz", key="").status == 200,
)
sh("start", "floci")
time.sleep(3)
# Floci keeps its state in memory, so a restart wipes the secret and bucket Terraform created: re-provision them, as an operator would.
sh("up", "--force-recreate", "--no-deps", "terraform")
sh("restart", "api")
check(
    "after S3 is re-provisioned the API restarts cleanly (Secrets Manager read with retries)",
    api.wait_ready(timeout=120),
)

section("hostile input")
nasty = [
    {"events": [{"model": "gpt-4o", "app": "../../etc/passwd", "prompt_tokens": 1, "completion_tokens": 1}]},
    {"events": [{"model": "gpt-4o'; DROP TABLE events;--", "prompt_tokens": 1, "completion_tokens": 1}]},
    {"events": [{"model": "gpt-4o", "prompt_tokens": 10**15, "completion_tokens": 1}]},
    {
        "events": [
            {
                "model": "gpt-4o",
                "prompt_text": "\x00" * 1000 + "<script>alert(1)</script>",
                "completion_tokens": 1,
                "app": pa,
            }
        ]
    },
    {"events": [{"model": "gpt-4o", "tags": {"a b": "c"}, "prompt_tokens": 1, "completion_tokens": 1}]},
    {"events": "not a list"},
    {"events": [None, 5, "x"]},
    {"nope": 1},
    {"events": [{"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 1, "app": pa}] * 1_001},
]
codes = [api.call("POST", "/v1/events", n).status for n in nasty]
check("hostile bodies never produce a 5xx", all(c < 500 for c in codes), str(codes))
check("a markup-and-NUL prompt is counted, not executed", codes[3] == 200)
for q in (
    "group_by=app;DROP TABLE events",
    "metrics=cost);DELETE FROM events;--",
    "range=1';--",
    "app=' OR 1=1 --&group_by=app",
):
    api.call("GET", f"/api/usage/summary?{quote(q, safe='=&')}")
check("SQL injection attempts left the data intact", count(a) == N * BATCH)
check(
    "path-like and unicode ids are refused or handled",
    api.call("GET", "/api/events/..%2F..%2Fetc%2Fpasswd").status == 404
    and api.call("GET", "/api/prompts/%00").status in (404, 422),
)

section("credential stuffing")
with ThreadPoolExecutor(30) as pool:
    bad = list(
        pool.map(
            lambda _: (
                api.call("GET", "/api/overview", key=f"tp_{uuid.uuid4().hex[:8]}_{uuid.uuid4().hex}").status
            ),
            range(300),
        )
    )
check("300 wrong keys are all 401", set(bad) == {401}, str(set(bad)))
check("a good key still works straight after", api.call("GET", "/api/overview").status == 200)

section("tenant isolation under load")
team_a, team_b = f"team-a-{RUN}", f"team-b-{RUN}"
ka = api.call("POST", "/api/keys", {"name": f"a-{RUN}", "scopes": ["ingest", "read"], "apps": [team_a]}).body[
    "key"
]
kb = api.call("POST", "/api/keys", {"name": f"b-{RUN}", "scopes": ["ingest", "read"], "apps": [team_b]}).body[
    "key"
]
api.events(
    [
        {
            "model": "gpt-4o",
            "app": team_a,
            "prompt_tokens": 1_000_000,
            "completion_tokens": 0,
            "id": f"{team_a}-1",
        }
    ],
    key=ka,
)
api.events(
    [
        {
            "model": "gpt-4o",
            "app": team_b,
            "prompt_tokens": 2_000_000,
            "completion_tokens": 0,
            "id": f"{team_b}-1",
        }
    ],
    key=kb,
)
paths = [
    "/api/overview?range=365d",
    "/api/usage/summary?range=365d&group_by=app",
    "/api/events?range=365d",
    "/api/prompts?range=365d",
    "/api/models/compare?range=365d",
    "/api/recommendations?range=365d",
    "/api/dimensions/app?range=365d",
]
leaks = [
    p
    for p in paths
    if team_b in api.call("GET", p, key=ka).body.__str__()
    or team_a in api.call("GET", p, key=kb).body.__str__()
]
check("neither team sees the other's data on any read endpoint", not leaks, str(leaks))
check(
    "a team cannot fetch the other's event by id",
    api.call("GET", f"/api/events/{team_b}-1", key=ka).status == 404,
)
foreign = api.events([{"model": "gpt-4o", "app": team_b, "prompt_tokens": 1, "completion_tokens": 1}], key=ka)
check(
    "a team cannot write into the other's app",
    foreign.status == 422 and foreign.error_code == "all_events_invalid",
    str(foreign.body),
)
section("the SDK under failure")
p = Profiler(api.base, "tp_invalid_key", app=f"sdk-{RUN}", flush_interval=0.1, max_retries=1)
for _ in range(100):
    p.track("gpt-4o", 10, 10)
p.flush(timeout=5)
check("rejected telemetry is counted and never raised", p.stats.rejected > 0 or p.stats.dropped > 0)
p.close()

print(json.dumps({"events": api.call("GET", "/api/system").body["bounds"]["events"]}))
print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all chaos checks passed'}")
sys.exit(1 if failures else 0)
