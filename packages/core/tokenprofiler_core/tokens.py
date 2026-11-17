"""Token counting without a tokenizer download.

Providers report real usage and TokenProfiler always prefers it. This estimator is for the cases where there is no
report (a prompt typed into the Prompt Lab, an event whose SDK did not capture usage, "what would this cost on another
model?"). It is a regular-expression approximation of byte-pair tokenization whose parameters were fitted against the
real ``tiktoken`` encodings on a corpus of code, Markdown, JSON, configuration, chat and non-English text, and then
checked on samples the fit never saw. Measured accuracy is in ``tests/test_tokens.py`` and the testing guide. Anything it
produces is an *estimate* and is flagged as one wherever it is stored or shown.

``exact_count`` uses ``tiktoken`` when it is installed (``pip install tokenprofiler-core[exact]``) and its encoding data
is available, otherwise it returns ``None``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

# Parameters fitted against tiktoken (see the module docstring). Per family:
#   ws/ws1  tokens for a run of spaces / a single stray space      nl  tokens for a line break
#   short   ASCII words up to this many characters are one token   div characters per extra token beyond `short`
#   cjk     tokens per CJK character                               nonascii tokens per non-ASCII letter
#   punct   tokens per punctuation character (min 1 per run)      sym tokens per non-ASCII symbol (emoji, arrows)
PARAMS: dict[str, dict[str, float]] = {
    "o200k": {"ws": 0.2, "ws1": 0.1, "nl": 0.8, "short": 8, "div": 4.5, "cjk": 0.6, "nonascii": 0.3, "punct": 0.3, "sym": 0.8},
    "cl100k": {"ws": 0.6, "ws1": 0.0, "nl": 0.5, "short": 8, "div": 3.0, "cjk": 0.8, "nonascii": 0.5, "punct": 0.3, "sym": 2.0},
}  # fmt: skip
PARAMS["generic"] = PARAMS[
    "cl100k"
]  # Claude, Gemini, Llama and others: no public tokenizer here, so the broader one

_TOKEN = re.compile(r"[ ]?[^\W\d_]+(?:'[^\W\d_]+)?|[ ]?\d{1,3}|[ ]?[^\w\s]+|\n+|[ \t]+|\s")
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]")

# Per-message framing in OpenAI-style chat formats (role markers and separators).
MESSAGE_OVERHEAD = 3
REPLY_PRIMING = 3

_O200K = ("gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-5", "o1", "o3", "o4", "chatgpt-4o")
_CL100K = ("gpt-4", "gpt-3.5", "text-embedding", "davinci", "babbage")


def family_for_model(model: str) -> str:
    """The tokenizer family a model name belongs to: ``o200k``, ``cl100k`` or ``generic`` (unknown tokenizer)."""
    m = model.lower().split("/")[-1]
    if m.startswith(_O200K):
        return "o200k"
    if m.startswith(_CL100K):
        return "cl100k"
    return "generic"


@dataclass(frozen=True, slots=True)
class TokenCount:
    tokens: int
    estimated: bool
    family: str


def estimate(text: str, family: str = "o200k") -> int:
    """Approximate token count of ``text`` for a tokenizer family. Empty text is 0 tokens, anything else at least 1."""
    if not text:
        return 0
    p = PARAMS.get(family, PARAMS["generic"])
    total = 0.0
    for m in _TOKEN.finditer(text):
        chunk = m.group(0)
        core = chunk.lstrip(" ")
        if not core:
            total += p["ws"] if len(chunk) > 1 else p["ws1"]
            continue
        if core[0].isspace():
            total += p["nl"]
            continue
        ch = core[0]
        if core.isdigit():
            total += 1
        elif _CJK.match(ch):
            total += len(core) * p["cjk"]
        elif ch.isalpha():
            n = len(core)
            if ch.isascii():
                total += 1 if n <= p["short"] else 1 + (n - p["short"]) / p["div"]
            else:
                total += n * p["nonascii"]
        else:
            total += max(1.0, len(core) * p["punct"]) if ch.isascii() else len(core) * p["sym"]
    return max(1, round(total))


@lru_cache(maxsize=4)
def _encoding(family: str) -> object | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base" if family == "o200k" else "cl100k_base")
    except Exception:  # not installed, or the encoding data is not available offline
        return None


def exact_count(text: str, family: str) -> int | None:
    """The real tiktoken count for the OpenAI families, or ``None`` when tiktoken or its data is unavailable."""
    if family not in ("o200k", "cl100k"):
        return None
    enc = _encoding(family)
    return len(enc.encode(text)) if enc is not None else None  # type: ignore[attr-defined]


def count(text: str, model: str = "gpt-4o", *, prefer_exact: bool = False) -> TokenCount:
    family = family_for_model(model)
    if prefer_exact and (n := exact_count(text, family)) is not None:
        return TokenCount(n, False, family)
    return TokenCount(estimate(text, family), True, family)


def count_messages(messages: list[dict[str, object]], model: str = "gpt-4o") -> TokenCount:
    """Tokens in a chat request: the content of each message plus the framing the chat format adds."""
    family = family_for_model(model)
    total = REPLY_PRIMING
    for m in messages:
        total += MESSAGE_OVERHEAD
        content = m.get("content")
        if isinstance(content, str):
            total += estimate(content, family)
        elif isinstance(content, list):  # multi-part content: count the text parts
            total += sum(estimate(str(p.get("text", "")), family) for p in content if isinstance(p, dict))
        if m.get("name"):
            total += 1
    return TokenCount(total, True, family)
