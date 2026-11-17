from __future__ import annotations

import pytest
from helpers import BOOK, T0, ev
from pydantic import ValidationError
from tokenprofiler_core.events import EventIn, now_ms
from tokenprofiler_core.ingest import IngestError, fingerprint, resolve, template_hash


def test_reported_tokens_are_used_exactly_and_costed() -> None:
    e = ev(prompt_tokens=1_000, completion_tokens=500)
    assert (e.prompt_tokens, e.completion_tokens, e.estimated) == (1_000, 500, False)
    assert (e.cost_input, e.cost_output, e.cost_total) == (2_500, 5_000, 7_500) and e.priced


def test_missing_counts_are_estimated_from_text_and_flagged() -> None:
    e = resolve(
        EventIn(
            model="gpt-4o",
            prompt_text="Summarize this support ticket for the on-call engineer.",
            completion_text="Intermittent 502s after the deploy.",
        ),
        BOOK,
    )
    assert e.estimated is True and e.prompt_tokens > 5 and e.completion_tokens > 3 and e.cost_total > 0


def test_reported_counts_win_over_text() -> None:
    e = resolve(
        EventIn(
            model="gpt-4o", prompt_tokens=7, completion_tokens=3, prompt_text="a very long prompt " * 100
        ),
        BOOK,
    )
    assert (e.prompt_tokens, e.completion_tokens, e.estimated) == (7, 3, False)


def test_an_event_with_no_token_information_is_refused() -> None:
    with pytest.raises(IngestError, match="prompt_tokens"):
        resolve(EventIn(model="gpt-4o", completion_tokens=3), BOOK)
    with pytest.raises(IngestError, match="completion_tokens"):
        resolve(EventIn(model="gpt-4o", prompt_tokens=3), BOOK)


def test_a_failed_call_with_no_output_costs_its_prompt_only() -> None:
    e = ev(
        status="error",
        prompt_tokens=1_000,
        completion_tokens=None,
        http_status=500,
        error_type="server_error",
    )
    assert e.completion_tokens == 0 and e.cost_total == 2_500 and e.status == "error"


def test_cache_hits_spend_nothing_and_record_what_was_saved() -> None:
    e = ev(cache_hit=True)
    assert e.cost_total == 0 and e.saved_micro == 7_500 and e.cache_hit


def test_cached_tokens_cannot_exceed_an_estimated_prompt() -> None:
    e = resolve(EventIn(model="gpt-4o", prompt_text="hi", completion_tokens=1, cached_tokens=500), BOOK)
    assert e.prompt_tokens >= 500


def test_unknown_models_are_stored_unpriced_with_a_normalized_name() -> None:
    e = ev(model="brand-new-model-2026-01-02", provider=None)
    assert (
        e.priced is False and e.cost_total == 0 and e.provider == "unknown" and e.model == "brand-new-model"
    )


def test_names_are_normalized_but_the_raw_name_is_kept() -> None:
    e = ev(model="gpt-4o-2024-08-06")
    assert (e.model, e.model_raw, e.provider) == ("gpt-4o", "gpt-4o-2024-08-06", "openai")


def test_ids_default_to_unique_values_and_ts_to_now() -> None:
    a = resolve(EventIn(model="gpt-4o", prompt_tokens=1, completion_tokens=1), BOOK)
    b = resolve(EventIn(model="gpt-4o", prompt_tokens=1, completion_tokens=1), BOOK)
    assert a.id != b.id and abs(a.ts - now_ms()) < 5_000


def test_fingerprint_ignores_whitespace_but_not_words() -> None:
    assert fingerprint("Hello   world\n") == fingerprint("Hello world")
    assert fingerprint("Hello world") != fingerprint("Hello World")


def test_template_hash_groups_the_same_prompt_filled_with_different_values() -> None:
    a = template_hash('Summarize ticket 4821 for "Acme Corp" about invoice 2025-03-14')
    b = template_hash('Summarize ticket 9 for "Globex" about invoice 2024-12-01')
    c = template_hash("Translate this paragraph into French")
    assert a == b and a != c


def test_prompt_text_gives_an_automatic_prompt_id_and_fingerprint_unless_supplied() -> None:
    e = resolve(EventIn(model="gpt-4o", prompt_text="Answer question 12", completion_tokens=5), BOOK)
    assert e.prompt_id and e.prompt_id.startswith("auto:") and e.prompt_hash
    named = resolve(
        EventIn(model="gpt-4o", prompt_text="Answer question 12", completion_tokens=5, prompt_id="faq-bot"),
        BOOK,
    )
    assert named.prompt_id == "faq-bot"


def test_prompt_text_is_never_stored() -> None:
    e = resolve(EventIn(model="gpt-4o", prompt_text="my secret prompt", completion_tokens=5), BOOK)
    assert "secret" not in repr(e)


@pytest.mark.parametrize(
    "bad",
    [
        {"model": ""}, {"model": "x" * 200}, {"model": "gpt-4o", "app": "has space"}, {"model": "gpt-4o", "app": "../etc"},
        {"model": "gpt-4o", "prompt_tokens": -1}, {"model": "gpt-4o", "prompt_tokens": 10**12},
        {"model": "gpt-4o", "prompt_tokens": 10, "cached_tokens": 11}, {"model": "gpt-4o", "ts": 1_700_000_000},
        {"model": "gpt-4o", "ts": now_ms() + 10**9}, {"model": "gpt-4o", "tags": {f"k{i}": "v" for i in range(11)}},
        {"model": "gpt-4o", "tags": {"bad key": "v"}}, {"model": "gpt-4o", "tags": {"k": "v" * 65}},
        {"model": "gpt-4o", "unknown_field": 1}, {"model": "gpt-4o", "latency_ms": -5}, {"model": "gpt-4o", "id": "has spaces"},
        {"model": "gpt-4o", "status": "weird"}, {"model": "gpt-4o", "status": "ok", "http_status": 500},
        {"model": "gpt-4o", "prompt_hash": "NOT-HEX"}, {"model": "gpt-4o", "temperature": 99},
    ],
)  # fmt: skip
def test_invalid_events_are_rejected(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EventIn(**bad)


def test_prompt_text_has_a_size_limit() -> None:
    with pytest.raises(ValidationError):
        EventIn(model="gpt-4o", prompt_text="x" * 1_100_000)


def test_timestamps_far_in_the_past_are_accepted() -> None:
    assert ev(ts=T0 - 400 * 86_400_000).ts < T0
