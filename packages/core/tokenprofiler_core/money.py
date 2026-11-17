"""Money is integer micro-dollars (1e-6 USD) everywhere it is stored or summed.

Token prices are quoted in USD per million tokens, so ``tokens * price_per_million`` is already a micro-dollar amount.
Summing integers is exact; summing floats of fractions of a cent is not.
"""

from __future__ import annotations

MICRO = 1_000_000


def usd_to_micro(usd: float) -> int:
    return round(usd * MICRO)


def micro_to_usd(micro: int | float) -> float:
    return micro / MICRO


def format_usd(micro: int | float | None) -> str:
    """ "$0.0042", "$12.40", "$1.2k": fractions of a cent keep four decimals, large sums are abbreviated."""
    if micro is None:
        return "-"
    usd = micro / MICRO
    if usd == 0:
        return "$0.00"
    if abs(usd) < 0.0001:
        return "<$0.0001" if usd > 0 else "-<$0.0001"
    if abs(usd) < 0.01:
        return f"${usd:.4f}"
    if abs(usd) < 1000:
        return f"${usd:,.2f}"
    if abs(usd) < 1_000_000:
        return f"${usd / 1000:.1f}k"
    return f"${usd / 1_000_000:.1f}M"
