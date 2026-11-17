"""Prompt analysis: where the tokens go, what in a prompt is wasteful, and what it costs on each model.

Everything here is deterministic text analysis; token numbers are estimates (see ``tokens.py``) and say so. The only
*automatic* rewrites offered are ones that cannot change meaning: whitespace normalization and compacting JSON that
parses to the same value. Everything else is a suggestion with the tokens it would save.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from .money import MICRO
from .pricing import PriceBook
from .tokens import MESSAGE_OVERHEAD, REPLY_PRIMING, estimate, family_for_model

Severity = Literal["info", "low", "medium", "high"]
MAX_ANALYZE_CHARS = 400_000

_WS_RUN = re.compile(r"[ \t]{2,}")
_BLANKS = re.compile(r"\n{3,}")
_TRAILING = re.compile(r"[ \t]+\n")
_BASE64 = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}")
_HEX = re.compile(r"\b[0-9a-fA-F]{64,}\b")
_NUM_ARRAY = re.compile(r"(?:-?\d+\.\d+\s*,\s*){30,}-?\d+\.\d+")
_FENCED_JSON = re.compile(r"```json\s*\n(.*?)```", re.S)
_EXAMPLE = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:example|few[- ]shot|sample)\b.*$|^\s*(?:input|output|q|a)\s*:", re.M
)
FILLER = [
    "i would like you to", "i want you to", "i need you to", "could you please", "can you please", "please note that",
    "it is important that you", "it is very important that", "make sure to", "make sure that you", "be sure to",
    "as an ai language model", "as a helpful assistant", "feel free to", "i hope this helps", "thank you in advance",
    "thanks in advance", "if you don't mind", "kindly",
]  # fmt: skip


@dataclass(slots=True)
class Finding:
    kind: str
    severity: Severity
    title: str
    detail: str
    tokens_saved: int
    auto_fixable: bool = False
    examples: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Segment:
    role: str
    tokens: int
    share: float
    chars: int


@dataclass(slots=True)
class Analysis:
    family: str
    total_tokens: int
    segments: list[Segment]
    findings: list[Finding]
    optimized_text: str | None
    optimized_tokens: int
    tokens_saved: int
    saved_pct: float
    estimated: bool = True


def segments_of(messages: list[dict[str, Any]], family: str) -> list[Segment]:
    raw: list[tuple[str, int, int]] = []
    for m in messages:
        c = m.get("content")
        text = (
            c
            if isinstance(c, str)
            else " ".join(str(p.get("text", "")) for p in c if isinstance(p, dict))
            if isinstance(c, list)
            else ""
        )
        raw.append((str(m.get("role", "user")), estimate(text, family) + MESSAGE_OVERHEAD, len(text)))
    total = sum(t for _, t, _ in raw) + REPLY_PRIMING
    merged: dict[str, list[int]] = {}
    for role, tokens, chars in raw:
        slot = merged.setdefault(role, [0, 0])
        slot[0] += tokens
        slot[1] += chars
    return [Segment(r, t, round(t / total, 4), c) for r, (t, c) in merged.items()]


def normalize_whitespace(text: str) -> str:
    """Collapses runs of spaces, trailing spaces and runs of blank lines. Leading indentation of lines is kept, so code
    and nested lists keep their structure."""
    lines = []
    for line in text.split("\n"):
        stripped = line.lstrip(" \t")
        indent = line[: len(line) - len(stripped)]
        lines.append(indent + _WS_RUN.sub(" ", stripped).rstrip())
    return _BLANKS.sub("\n\n", "\n".join(lines)).strip("\n")


def compact_json_blocks(text: str) -> tuple[str, int]:
    """Re-serializes fenced ```json blocks and whole-text JSON compactly, only when the value is unchanged.
    Returns (text, number of blocks compacted)."""
    n = 0

    def swap(m: re.Match[str]) -> str:
        nonlocal n
        body = m.group(1)
        try:
            value = json.loads(body)
        except ValueError:
            return m.group(0)
        compact = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if len(compact) >= len(body.strip()):
            return m.group(0)
        n += 1
        return f"```json\n{compact}\n```"

    out = _FENCED_JSON.sub(swap, text)
    if out == text:
        try:
            value = json.loads(text)
        except ValueError:
            return text, 0
        compact = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
        if len(compact) < len(text.strip()):
            return compact, 1
    return out, n


def _duplicate_blocks(text: str) -> list[str]:
    seen: Counter[str] = Counter()
    for block in re.split(r"\n\s*\n|\n", text):
        b = " ".join(block.split())
        if len(b) >= 40:
            seen[b] += 1
    return [b for b, c in seen.items() if c > 1]


def analyze_text(text: str, family: str = "o200k") -> tuple[list[Finding], str]:
    """Findings for one piece of prompt text, and the safely auto-optimized version of it."""
    text = text[:MAX_ANALYZE_CHARS]
    base = estimate(text, family)
    findings: list[Finding] = []

    optimized = normalize_whitespace(text)
    ws_saved = base - estimate(optimized, family)
    if ws_saved >= 2:
        findings.append(
            Finding(
                "whitespace",
                "low",
                "Redundant whitespace",
                f"Runs of spaces, trailing spaces and blank lines cost about {ws_saved} tokens.",
                ws_saved,
                True,
            )
        )

    compacted, blocks = compact_json_blocks(optimized)
    json_saved = estimate(optimized, family) - estimate(compacted, family)
    if blocks and json_saved >= 5:
        findings.append(
            Finding(
                "json_pretty_printed",
                "medium",
                "Pretty-printed JSON",
                f"{blocks} JSON block(s) use indentation and spacing. Compact JSON parses to the same value and saves about {json_saved} tokens.",
                json_saved,
                True,
            )
        )
        optimized = compacted

    dups = _duplicate_blocks(text)
    if dups:
        dup_tokens = sum(estimate(d, family) * (text.count(d) - 1) for d in dups)
        findings.append(
            Finding(
                "duplicate_content",
                "high" if dup_tokens > 200 else "medium",
                "Repeated passages",
                f"{len(dups)} passage(s) appear more than once, about {dup_tokens} tokens that repeat.",
                dup_tokens,
                False,
                [d[:90] for d in dups[:3]],
            )
        )

    lower = text.lower()
    found = [(p, lower.count(p)) for p in FILLER if p in lower]
    if found:
        saved = sum(estimate(p, family) * c for p, c in found)
        findings.append(
            Finding(
                "filler",
                "low",
                "Filler phrases",
                f"Phrases such as {', '.join(repr(p) for p, _ in found[:3])} rarely change the answer. About {saved} tokens.",
                saved,
                False,
                [p for p, _ in found[:5]],
            )
        )

    blobs = (
        [m.group(0) for m in _BASE64.finditer(text)]
        + [m.group(0) for m in _HEX.finditer(text)]
        + [m.group(0) for m in _NUM_ARRAY.finditer(text)]
    )
    if blobs:
        blob_tokens = sum(estimate(b, family) for b in blobs)
        findings.append(
            Finding(
                "opaque_blob",
                "high",
                "Opaque data in the prompt",
                f"{len(blobs)} base64, hex or numeric-array blob(s) cost about {blob_tokens} tokens and models read them poorly. Send a reference or a summary instead.",
                blob_tokens,
                False,
                [b[:40] + "…" for b in blobs[:2]],
            )
        )

    ex_lines = len(_EXAMPLE.findall(text))
    if ex_lines >= 6 and base > 300:
        findings.append(
            Finding(
                "few_shot_heavy",
                "info",
                "Many in-context examples",
                f"About {ex_lines} example markers. Examples help, but each one is paid for on every call: try fewer, or cache the stable prefix.",
                0,
            )
        )

    if base > 3000 and not any(f.kind == "duplicate_content" for f in findings):
        findings.append(
            Finding(
                "long_prompt",
                "info",
                "Long prompt",
                f"About {base} tokens of input on every call. If most of it is the same each time, prompt caching makes it much cheaper.",
                0,
            )
        )
    return findings, optimized


def analyze(
    messages: list[dict[str, Any]] | None = None,
    text: str | None = None,
    model: str = "gpt-4o",
    prices: PriceBook | None = None,
) -> Analysis:
    """Analyzes a chat request (``messages``) or a single prompt (``text``) for a model's tokenizer family."""
    family = family_for_model(model)
    if messages is None:
        if text is None:
            raise ValueError("provide messages or text")
        messages = [{"role": "user", "content": text}]
    segs = segments_of(messages, family)
    findings: list[Finding] = []
    optimized_parts: list[dict[str, Any]] = []
    for m in messages:
        c = m.get("content")
        content = c if isinstance(c, str) else ""
        f, opt = analyze_text(content, family)
        for x in f:
            x.title = f"{x.title} ({m.get('role', 'user')})" if len(messages) > 1 else x.title
        findings.extend(f)
        optimized_parts.append({**m, "content": opt} if isinstance(c, str) else m)
    total = sum(s.tokens for s in segs) + REPLY_PRIMING
    opt_total = (
        sum(
            estimate(p["content"], family) + MESSAGE_OVERHEAD
            for p in optimized_parts
            if isinstance(p.get("content"), str)
        )
        + REPLY_PRIMING
    )
    saved = max(0, total - opt_total)
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    findings.sort(key=lambda f: (order[f.severity], -f.tokens_saved))
    joined = (
        "\n\n".join(str(p["content"]) for p in optimized_parts if isinstance(p.get("content"), str))
        if len(messages) == 1
        else None
    )
    return Analysis(
        family,
        total,
        segs,
        findings,
        joined if len(messages) == 1 else None,
        opt_total,
        saved,
        round(100 * saved / total, 1) if total else 0.0,
    )


def price_table(
    prompt_tokens: int,
    completion_tokens: int,
    prices: PriceBook,
    models: list[str] | None = None,
    requests: int = 1,
) -> list[dict[str, Any]]:
    """What a call of this size costs on each model, cheapest first. Unpriced models are left out."""
    from .pricing import Usage

    rows = []
    for p in prices.all():
        if models and p.id not in models:
            continue
        if p.provider in (
            "self-hosted",
            "test",
        ):  # free local models and the test model are not purchasing options
            continue
        c = prices.cost(p.id, Usage(prompt_tokens, completion_tokens))
        rows.append({"model": p.id, "provider": p.provider, "tier": p.tier, "per_request_micro": c.total, "total_micro": c.total * requests,
                     "context": p.context, "fits": p.context is None or prompt_tokens + completion_tokens <= p.context})  # fmt: skip
    return sorted(rows, key=lambda r: r["total_micro"])


__all__ = [
    "MICRO",
    "Analysis",
    "Finding",
    "Segment",
    "analyze",
    "analyze_text",
    "compact_json_blocks",
    "normalize_whitespace",
    "price_table",
    "segments_of",
]
