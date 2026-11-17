# Local quickstart

You need Python 3.12, Node 22 with pnpm, and Docker (for the full stack). No AWS account and no AI provider key.

## Option A: the whole stack (recommended)

```bash
make install   # virtualenv + dependencies
make up        # Floci (emulated AWS), Terraform, API, mock LLM provider, console
make demo      # 45 days of synthetic traffic, budgets, proxied calls, alerts
```

Open <http://localhost:3300>. The console is pre-connected to the local demo key. API docs: <http://localhost:8380/docs>.

`make up` provisions a secrets entry and a reports bucket in Floci with the same Terraform that builds production, and
the API reads its pepper and bootstrap key from Secrets Manager. `make down` removes everything, including the volumes.

## Option B: just the API

```bash
make dev                      # API on :8380, data in .localdata/
pnpm console:dev              # console on :3300 (in another terminal)
.venv/bin/tokenprofiler keys:create --name me --scopes '*'   # prints a key once
```

Paste that key into the console's connect screen.

## Send your first events

```bash
curl localhost:8380/v1/events -H "Authorization: Bearer $TP_API_KEY" -H "Content-Type: application/json" \
  -d '{"events":[{"model":"gpt-4o","app":"hello","prompt_tokens":1200,"completion_tokens":300,"latency_ms":800}]}'
```

Then open Overview, or `GET /api/overview`. Try the CLI estimator: `echo "your prompt" | tokenprofiler estimate --model gpt-4o`.

## Troubleshooting

- *Console says Unreachable*: the API is not up, or its URL is wrong; check `make ps` and `make logs SERVICE=api`.
- *401*: the key is wrong or revoked. Create one with `make key`.
- *Port in use*: change the published ports in `deploy/compose/compose.yaml`.
- *`make up` slow the first time*: the console image runs `pnpm install` in Docker; later builds are cached.
