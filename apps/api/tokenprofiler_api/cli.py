"""``tokenprofiler`` command line: serve the API, manage keys, seed demo data, estimate tokens."""

from __future__ import annotations

import argparse
import asyncio
import sys

import uvicorn
from tokenprofiler_core.money import format_usd
from tokenprofiler_core.pricing import PriceBook, Usage
from tokenprofiler_core.prompts import price_table
from tokenprofiler_core.tokens import count

from .auth import ALL_SCOPES, ApiKeyStore
from .db import Database
from .settings import apply_aws_secrets, get_settings


async def _create_key(name: str, scopes: list[str], apps: list[str]) -> str:
    s = apply_aws_secrets(get_settings())
    db = Database(s.data_path / "control.db")
    try:
        return (await ApiKeyStore(db, s.api_key_pepper).create(name, scopes, apps=apps)).key
    finally:
        db.close()


def _estimate(text: str, model: str, completion: int) -> int:
    tc = count(text, model)
    book = PriceBook()
    cost = book.cost(model, Usage(tc.tokens, completion))
    print(f"{tc.tokens} prompt tokens (estimated, {tc.family} tokenizer)")
    print(f"{model}: {format_usd(cost.total)} per request" + ("" if cost.priced else " (no price known)"))
    print("\ncheapest alternatives:")
    for r in price_table(tc.tokens, completion, book)[:5]:
        print(f"  {r['model']:<26} {format_usd(r['per_request_micro']):>10}")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tokenprofiler", description="Euron TokenProfiler")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the API server (default)")
    k = sub.add_parser("keys:create", help="create an API key and print it once")
    k.add_argument("--name", required=True)
    k.add_argument("--scopes", default="*", help=f"comma-separated: *,{','.join(ALL_SCOPES)}")
    k.add_argument("--apps", default="", help="comma-separated apps to limit the key to")
    e = sub.add_parser("estimate", help="estimate tokens and cost for a prompt read from stdin")
    e.add_argument("--model", default="gpt-4o")
    e.add_argument("--completion", type=int, default=300, help="expected completion tokens")
    args = parser.parse_args(argv)
    if args.cmd == "keys:create":
        print(
            asyncio.run(
                _create_key(
                    args.name,
                    [x.strip() for x in args.scopes.split(",") if x.strip()],
                    [x.strip() for x in args.apps.split(",") if x.strip()],
                )
            )
        )
        return
    if args.cmd == "estimate":
        sys.exit(_estimate(sys.stdin.read(), args.model, args.completion))
    s = get_settings()
    uvicorn.run(
        "tokenprofiler_api.main:create_app",
        factory=True,
        host=s.host,
        port=s.port,
        log_config=None,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
