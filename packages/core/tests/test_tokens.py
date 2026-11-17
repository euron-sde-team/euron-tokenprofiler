from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from tokenprofiler_core.tokens import (
    PARAMS,
    count,
    count_messages,
    estimate,
    exact_count,
    family_for_model,
)

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "token_corpus.json").read_text())
HOLDOUT = [s for s in CORPUS if s["split"] == "holdout"]


def mean_abs_error(samples: list[dict[str, Any]], family: str) -> float:
    return sum(
        abs(estimate(str(s["text"]), family) - int(s[family])) / int(s[family]) for s in samples
    ) / len(samples)


def bias(samples: list[dict[str, Any]], family: str) -> float:
    return sum(estimate(str(s["text"]), family) for s in samples) / sum(int(s[family]) for s in samples) - 1


def test_the_corpus_has_ground_truth_from_the_real_tokenizers() -> None:
    assert len(CORPUS) > 150 and {s["kind"] for s in CORPUS} >= {
        "code",
        "markdown",
        "json",
        "chat",
        "multilingual",
        "emoji",
    }
    assert all(s["o200k"] > 0 and s["cl100k"] > 0 for s in CORPUS)


@pytest.mark.parametrize(("family", "mae", "total_bias"), [("o200k", 0.07, 0.04), ("cl100k", 0.09, 0.05)])
def test_accuracy_on_samples_the_parameters_were_never_fitted_on(
    family: str, mae: float, total_bias: float
) -> None:
    """The estimator was fitted on the 'calibration' half; this is the 'holdout' half, measured against real tiktoken."""
    assert mean_abs_error(HOLDOUT, family) < mae
    assert abs(bias(HOLDOUT, family)) < total_bias, "no systematic over- or under-counting"


def test_every_content_kind_is_within_a_usable_margin() -> None:
    by_kind: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for s in CORPUS:
        by_kind[s["kind"]][0] += estimate(s["text"], "o200k")
        by_kind[s["kind"]][1] += s["o200k"]
    for kind in ("code", "markdown", "json", "chat"):
        est, true = by_kind[kind]
        assert abs(est / true - 1) < 0.10, f"{kind}: {est / true - 1:+.1%}"


def test_estimates_scale_with_length() -> None:
    base = "The quick brown fox jumps over the lazy dog. " * 10
    assert estimate(base * 4) == pytest.approx(estimate(base) * 4, rel=0.03)


@pytest.mark.parametrize("text", ["", " ", "\n\n", "a"])
def test_edge_cases_never_crash_and_empty_is_zero(text: str) -> None:
    n = estimate(text)
    assert n == 0 if text == "" else n >= 1


def test_hostile_and_unusual_input_is_handled() -> None:
    for text in [
        "\x00" * 100,
        "😀" * 500,
        "a" * 100_000,
        chr(0x202E) * 50,
        "".join(chr(0x1D510 + i) for i in range(12)) * 100,
        "<script>" * 1000,
    ]:
        assert estimate(text) > 0


@pytest.mark.parametrize(
    ("model", "family"),
    [("gpt-4o-2024-08-06", "o200k"), ("gpt-4.1-mini", "o200k"), ("o3-mini", "o200k"), ("openai/gpt-4o", "o200k"),
     ("gpt-4-turbo", "cl100k"), ("gpt-3.5-turbo", "cl100k"), ("text-embedding-3-small", "cl100k"),
     ("claude-3-5-sonnet", "generic"), ("gemini-1.5-pro", "generic"), ("llama-3.1-70b", "generic")],
)  # fmt: skip
def test_model_names_map_to_tokenizer_families(model: str, family: str) -> None:
    assert family_for_model(model) == family


def test_counts_are_always_flagged_as_estimates() -> None:
    c = count("hello world", "claude-3-5-sonnet")
    assert c.estimated and c.family == "generic" and c.tokens >= 2


def test_message_framing_is_added() -> None:
    plain = count("Hello there", "gpt-4o").tokens
    framed = count_messages([{"role": "user", "content": "Hello there"}], "gpt-4o").tokens
    assert framed == plain + 3 + 3, "3 per message plus 3 to prime the reply"
    parts = count_messages(
        [{"role": "user", "content": [{"type": "text", "text": "Hello there"}, {"type": "image_url"}]}],
        "gpt-4o",
    )
    assert parts.tokens == framed


def test_exact_counting_is_optional_and_refuses_unknown_families() -> None:
    assert exact_count("anything", "generic") is None
    assert count("x", "claude-3-5-sonnet", prefer_exact=True).estimated is True


@pytest.mark.network
def test_the_estimator_agrees_with_live_tiktoken_when_it_is_installed() -> None:
    exact = exact_count("Hello world, this is a test of the real tokenizer.", "o200k")
    if exact is None:
        pytest.skip("tiktoken or its encoding data is not available")
    assert abs(estimate("Hello world, this is a test of the real tokenizer.", "o200k") - exact) <= 2
    assert count("Hello", "gpt-4o", prefer_exact=True).estimated is False


def test_parameters_exist_for_every_family() -> None:
    assert set(PARAMS) == {"o200k", "cl100k", "generic"}
