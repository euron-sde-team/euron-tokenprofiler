from __future__ import annotations

import json
from pathlib import Path

import pytest
from tokenprofiler_core.money import format_usd, micro_to_usd, usd_to_micro
from tokenprofiler_core.pricing import ModelPrice, PriceBook, Usage, normalize_model


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [("gpt-4o-2024-08-06", "gpt-4o"), ("GPT-4o", "gpt-4o"), ("openai/gpt-4.1-mini", "gpt-4.1-mini"),
     ("anthropic.claude-3-5-sonnet-20241022-v2:0", "claude-3-5-sonnet"), ("claude-3-5-sonnet-latest", "claude-3-5-sonnet"),
     ("gpt-4o-mini-2024-07-18", "gpt-4o-mini"), ("gemini-2.5-flash-preview-05-20", "gemini-2.5-flash"), ("  gpt-4o  ", "gpt-4o")],
)  # fmt: skip
def test_model_names_are_normalized(raw: str, normalized: str) -> None:
    assert normalize_model(raw) == normalized


def test_dated_and_prefixed_names_resolve_to_the_same_price() -> None:
    book = PriceBook()
    base = book.resolve("gpt-4o")
    assert base is not None
    for alias in ("gpt-4o-2024-11-20", "openai/gpt-4o", "GPT-4O"):
        assert book.resolve(alias) == base


def test_the_longest_prefix_wins_so_mini_is_not_priced_as_the_flagship() -> None:
    book = PriceBook()
    assert book.resolve("gpt-4o-mini").id == "gpt-4o-mini"  # type: ignore[union-attr]
    assert book.resolve("gpt-4.1-nano-2025-04-14").id == "gpt-4.1-nano"  # type: ignore[union-attr]
    assert book.resolve("llama-3.1-70b-instruct").id == "llama-3.1-70b"  # type: ignore[union-attr]


def test_an_unknown_model_is_unpriced_not_free_and_not_guessed() -> None:
    cost = PriceBook().cost("totally-new-model-9", Usage(1000, 1000))
    assert cost.priced is False and cost.total == 0


def test_cost_is_exact_integer_micro_dollars() -> None:
    # gpt-4o: $2.50 in, $10 out per 1M => 2.5 and 10 micro-dollars per token
    c = PriceBook().cost("gpt-4o", Usage(1_000, 500))
    assert (c.input, c.output, c.total) == (2_500, 5_000, 7_500)
    assert micro_to_usd(c.total) == pytest.approx(0.0075)


def test_cached_tokens_are_billed_at_the_cached_rate_inside_the_prompt() -> None:
    c = PriceBook().cost("gpt-4o", Usage(prompt_tokens=1_000, completion_tokens=0, cached_tokens=400))
    assert c.cached == 500 and c.input == 1_500 and c.total == 2_000, "400 cached at 1.25 + 600 fresh at 2.5"


def test_cache_writes_use_the_write_rate_when_the_provider_has_one() -> None:
    c = PriceBook().cost(
        "claude-3-5-sonnet", Usage(prompt_tokens=1_000, completion_tokens=0, cache_write_tokens=1_000)
    )
    assert c.cache_write == 3_750 and c.input == 0


def test_a_model_without_a_cached_price_bills_cached_tokens_as_normal_input() -> None:
    c = PriceBook().cost("gpt-4-turbo", Usage(1_000, 0, cached_tokens=500))
    assert c.cached == 5_000 and c.total == 10_000


def test_batch_pricing_applies_the_providers_discount() -> None:
    book = PriceBook()
    assert (
        book.cost("gpt-4o", Usage(1_000, 1_000), batch=True).total
        == book.cost("gpt-4o", Usage(1_000, 1_000)).total // 2
    )
    assert (
        book.cost("mistral-small", Usage(1_000, 1_000), batch=True).total
        == book.cost("mistral-small", Usage(1_000, 1_000)).total
    )


def test_usage_rejects_impossible_numbers() -> None:
    with pytest.raises(ValueError, match="negative"):
        Usage(-1, 0)
    with pytest.raises(ValueError, match="subsets"):
        Usage(prompt_tokens=10, completion_tokens=0, cached_tokens=11)


def test_local_models_cost_nothing_and_say_so() -> None:
    c = PriceBook().cost("ollama", Usage(1_000_000, 1_000_000))
    assert c.priced is True and c.total == 0


def test_overrides_replace_and_can_be_removed() -> None:
    book = PriceBook()
    original = book.resolve("gpt-4o")
    book.set(ModelPrice(id="gpt-4o", provider="enterprise", input=1.0, output=4.0))
    assert book.cost("gpt-4o", Usage(1_000_000, 0)).total == 1_000_000 and "gpt-4o" in book.overridden
    assert book.remove("gpt-4o") is True and book.resolve("gpt-4o") == original
    assert book.remove("gpt-4o") is False


def test_a_custom_model_can_be_added_and_removed() -> None:
    book = PriceBook()
    book.set(ModelPrice(id="acme-llm", provider="custom", input=0.5, output=1.0))
    assert book.cost("acme-llm-v2", Usage(1_000_000, 1_000_000)).total == 1_500_000
    assert book.remove("acme-llm") and book.resolve("acme-llm") is None


def test_a_price_file_layers_over_the_builtin_book(tmp_path: Path) -> None:
    f = tmp_path / "prices.json"
    f.write_text(
        json.dumps({"gpt-4o": {"input": 1.0, "output": 2.0}, "House-Model-1": {"input": 0.1, "output": 0.2}})
    )
    book = PriceBook.from_file(f)
    assert book.resolve("gpt-4o").input == 1.0  # type: ignore[union-attr]
    assert book.resolve("house-model-1") is not None and book.resolve("claude-3-5-sonnet") is not None


def test_the_builtin_book_is_complete_and_consistent() -> None:
    book = PriceBook()
    ids = {p.id for p in book.all()}
    assert len(ids) >= 35 and book.as_of
    for p in book.all():
        assert p.cheaper is None or p.cheaper in ids, f"{p.id} points at a missing cheaper model"
        if p.cheaper:
            cheaper = book.resolve(p.cheaper)
            assert cheaper is not None and cheaper.input + cheaper.output <= p.input + p.output, (
                f"{p.id}: {p.cheaper} is not cheaper"
            )
        if p.cached_input is not None:
            assert p.cached_input <= p.input


def test_money_formatting_and_conversion() -> None:
    assert usd_to_micro(0.0075) == 7_500 and micro_to_usd(2_500_000) == 2.5
    assert [format_usd(x) for x in (None, 0, 5, 4_200, 12_400_000, 1_234_000_000, 9_000_000_000_000)] == [
        "-",
        "$0.00",
        "<$0.0001",
        "$0.0042",
        "$12.40",
        "$1.2k",
        "$9.0M",
    ]
