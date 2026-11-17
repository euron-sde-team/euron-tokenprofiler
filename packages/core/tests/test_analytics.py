from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from tokenprofiler_core.anomaly import detect
from tokenprofiler_core.budgets import BudgetIn, evaluate
from tokenprofiler_core.forecast import DAY_MS, month_bounds, project
from tokenprofiler_core.pricing import PriceBook
from tokenprofiler_core.prompts import (
    analyze,
    compact_json_blocks,
    normalize_whitespace,
    price_table,
)
from tokenprofiler_core.tokens import estimate


# ── anomalies ────────────────────────────────────────────────────────────────────────────────────────────────
def flat(n: int = 20, v: float = 100.0) -> list[tuple[int, float]]:
    return [(d, v + (d % 3) * 2.0) for d in range(n)]


def test_a_spike_after_a_stable_period_is_flagged() -> None:
    series = [*flat(20), (20, 400.0)]
    (a,) = detect(series)
    assert a.day == 20 and a.direction == "spike" and a.score > 3.5 and a.ratio == pytest.approx(3.9, abs=0.2)


def test_a_drop_is_flagged_too() -> None:
    (a,) = detect([*flat(20), (20, 5.0)])
    assert a.direction == "drop" and a.value == 5.0


def test_normal_variation_is_not_flagged() -> None:
    assert detect(flat(40)) == []
    noisy = [(d, 100 + ((d * 37) % 17) - 8) for d in range(40)]
    assert detect(noisy) == []


def test_one_spike_does_not_hide_the_next_because_it_never_enters_its_own_baseline() -> None:
    series = [*flat(20), (20, 500.0), *[(d, 100.0) for d in range(21, 24)], (24, 520.0)]
    assert [a.day for a in detect(series)] == [20, 24]


def test_a_new_series_with_too_little_history_is_not_judged() -> None:
    assert detect([(d, 10.0 * float(d + 1) ** 3) for d in range(6)]) == []


def test_small_absolute_wobbles_on_a_tiny_series_are_ignored_by_the_floor() -> None:
    series = [*[(d, 0.001) for d in range(20)], (20, 0.01)]
    assert detect(series, floor=0.5) == []


def test_a_gradual_ramp_is_not_a_spike() -> None:
    assert detect([(d, 100.0 + d * 3) for d in range(40)]) == []


# ── forecast ─────────────────────────────────────────────────────────────────────────────────────────────────
NOW = int(datetime(2025, 3, 11, 12, 0, tzinfo=UTC).timestamp() * 1000)  # day 11 of a 31-day month, noon


def days_of(values: dict[int, int]) -> dict[int, int]:
    today = NOW // DAY_MS
    return {today + k: v for k, v in values.items()}


def test_month_bounds() -> None:
    start, end, days = month_bounds(NOW)
    assert days == 31 and datetime.fromtimestamp(start / 1000, UTC).day == 1 and end - start == 31 * DAY_MS
    assert month_bounds(int(datetime(2024, 2, 10, tzinfo=UTC).timestamp() * 1000))[2] == 29


def test_a_steady_spend_projects_to_the_month_total() -> None:
    today = NOW // DAY_MS
    first = today - 10
    daily = dict.fromkeys(range(first, today), 1000000) | {today: 500_000}
    f = project(daily, NOW)
    assert f.month_to_date == 10 * 1_000_000 + 500_000
    assert f.run_rate_per_day == 1_000_000
    assert f.projected_month_total == pytest.approx(10_500_000 + 1_000_000 * (31 - 10.5), rel=0.001)
    assert f.trend_pct is None, "10 days of history cannot support a week-over-week trend"
    assert f.confidence == "medium"


def test_the_run_rate_is_a_median_so_one_spike_does_not_inflate_the_month() -> None:
    today = NOW // DAY_MS
    daily = dict.fromkeys(range(today - 10, today), 1000000) | {today - 3: 50_000_000}
    assert project(daily, NOW).run_rate_per_day == 1_000_000


def test_a_growing_series_reports_a_positive_trend() -> None:
    today = NOW // DAY_MS
    daily = {d: 1_000_000 + (d - (today - 20)) * 100_000 for d in range(today - 20, today)}
    f = project(daily, NOW)
    assert f.trend_pct is not None and f.trend_pct > 0.2 and f.confidence == "high"


def test_forecast_with_no_data_is_zero_and_low_confidence() -> None:
    f = project({}, NOW)
    assert (f.month_to_date, f.projected_month_total, f.confidence) == (0, 0, "low")


def test_yesterdays_month_does_not_leak_into_month_to_date() -> None:
    today = NOW // DAY_MS
    daily = {today - 20: 99_000_000, today - 1: 1_000_000}  # 20 days ago was last month
    assert project(daily, NOW).month_to_date == 1_000_000


# ── budgets ──────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("spent", "projected", "status"),
    [
        (100, 400, "ok"),
        (850, 900, "warning"),
        (400, 1_300, "projected_overrun"),
        (1_000, 1_000, "exceeded"),
        (1_500, 2_000, "exceeded"),
        (0, 0, "ok"),
    ],
)
def test_budget_status(spent: int, projected: int, status: str) -> None:
    assert evaluate(1_000, 0.8, spent, projected, run_rate_per_day=50.0).status == status


def test_days_until_exhausted_only_when_it_will_run_out() -> None:
    s = evaluate(1_000, 0.8, 400, 1_300, run_rate_per_day=100.0)
    assert s.days_until_exhausted == 6.0 and s.remaining_micro == 600
    assert evaluate(1_000, 0.8, 400, 900, 100.0).days_until_exhausted is None


def test_budget_input_validation() -> None:
    ok = BudgetIn(name="Support team", limit_usd=500, scope={"app": "support-bot", "tag:team": "support"})
    f = ok.filters(1, 2)
    assert f.eq == {"app": "support-bot"} and f.tags == {"team": "support"}
    for bad in [
        {"name": "", "limit_usd": 1},
        {"name": "x", "limit_usd": 0},
        {"name": "x", "limit_usd": -5},
        {"name": "x", "limit_usd": 1, "scope": {"password": "x"}},
        {"name": "x", "limit_usd": 1, "warn_at": 1.5},
        {"name": "<b>", "limit_usd": 1},
        {"name": "x", "limit_usd": 1, "scope": {"app": ""}},
    ]:
        with pytest.raises(ValidationError):
            BudgetIn(**bad)


# ── prompt analysis ──────────────────────────────────────────────────────────────────────────────────────────
MESSY = (
    "You are a helpful assistant.     Please   answer   carefully   and   concisely   every   time.   \n\n\n\n"
    * 12
    + "I would like you to be concise.\n"
    + "Rules apply to every answer you give here.\n" * 4
)
PRETTY_JSON = json.dumps(
    {"users": [{"id": i, "name": f"user-{i}", "roles": ["a", "b"]} for i in range(12)]}, indent=4
)


def test_whitespace_normalization_keeps_structure_and_saves_tokens() -> None:
    out = normalize_whitespace(MESSY)
    assert (
        "     " not in out and "\n\n\n" not in out and not any(line.endswith(" ") for line in out.split("\n"))
    )
    assert estimate(out) < estimate(MESSY)
    code = "def f():\n    if x:\n        return 1\n"
    assert normalize_whitespace(code) == code.strip("\n"), (
        "indentation is meaning in code and lists, so it is kept"
    )


def test_json_compaction_never_changes_the_value() -> None:
    text = f"Here is the data:\n```json\n{PRETTY_JSON}\n```\nSummarize it."
    out, n = compact_json_blocks(text)
    assert n == 1 and len(out) < len(text)
    inner = out.split("```json\n")[1].split("\n```")[0]
    assert json.loads(inner) == json.loads(PRETTY_JSON)
    assert compact_json_blocks("```json\n{not valid json}\n```") == ("```json\n{not valid json}\n```", 0)
    whole, n2 = compact_json_blocks(PRETTY_JSON)
    assert n2 == 1 and json.loads(whole) == json.loads(PRETTY_JSON)


def test_analysis_finds_waste_and_the_optimized_text_is_smaller() -> None:
    a = analyze(text=MESSY + PRETTY_JSON.replace("\n", "\n") + "\n```json\n" + PRETTY_JSON + "\n```")
    kinds = {f.kind for f in a.findings}
    assert {"whitespace", "duplicate_content", "filler", "json_pretty_printed"} <= kinds
    assert a.tokens_saved > 0 and a.optimized_tokens < a.total_tokens and a.optimized_text is not None
    assert a.estimated is True


def test_a_clean_prompt_has_no_actionable_findings() -> None:
    a = analyze(text="Classify the sentiment of the review as positive, negative or neutral.")
    assert [f for f in a.findings if f.tokens_saved > 0] == [] and a.tokens_saved == 0


def test_opaque_blobs_are_called_out() -> None:
    blob = "QUJD" * 100
    a = analyze(text=f"Describe this image: {blob}")
    f = next(x for x in a.findings if x.kind == "opaque_blob")
    assert f.severity == "high" and f.tokens_saved > 50


def test_segments_break_a_chat_down_by_role_and_shares_sum_to_about_one() -> None:
    msgs = [
        {"role": "system", "content": "You are terse. " * 80},
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello!"},
        {"role": "user", "content": "Weather?"},
    ]
    a = analyze(messages=msgs)
    by_role = {s.role: s for s in a.segments}
    assert by_role["system"].share > 0.8 and set(by_role) == {"system", "user", "assistant"}
    assert sum(s.share for s in a.segments) == pytest.approx(1.0, abs=0.05)
    assert a.optimized_text is None, "multi-message requests have no single optimized text"


def test_analysis_requires_input_and_survives_hostile_text() -> None:
    with pytest.raises(ValueError, match="messages or text"):
        analyze()
    assert analyze(text="\x00" * 5000 + "a" * 600_000).total_tokens > 0


def test_price_table_is_sorted_cheapest_first_and_flags_context_overflow() -> None:
    rows = price_table(2_000, 500, PriceBook(), requests=1_000)
    costs = [r["total_micro"] for r in rows]
    assert costs == sorted(costs) and rows[0]["model"] in {
        "text-embedding-3-small",
        "gemini-1.5-flash",
        "gemini-2.0-flash",
        "gpt-4.1-nano",
    }
    assert rows[0]["total_micro"] == rows[0]["per_request_micro"] * 1_000
    big = price_table(500_000, 100, PriceBook())
    assert (
        next(r for r in big if r["model"] == "gpt-4o")["fits"] is False
        and next(r for r in big if r["model"] == "gemini-1.5-pro")["fits"] is True
    )
    assert all(r["provider"] not in ("self-hosted", "test") for r in rows)
