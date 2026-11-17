"""Spend anomaly detection: robust statistics, so one spike does not hide the next and a noisy series is not over-flagged."""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

MIN_BASELINE = 7
MAD_TO_SIGMA = 1.4826  # scales the median absolute deviation to a standard deviation for normal data


@dataclass(frozen=True, slots=True)
class Anomaly:
    day: int  # days since the epoch
    value: float
    expected: float
    score: float  # robust z-score: signed, in "sigmas"
    direction: str  # "spike" or "drop"

    @property
    def ratio(self) -> float:
        return self.value / self.expected if self.expected else float("inf")


def detect(
    series: Sequence[tuple[int, float]],
    *,
    threshold: float = 3.5,
    window: int = 14,
    min_change: float = 0.25,
    floor: float = 0.0,
) -> list[Anomaly]:
    """Flags days that depart from the trailing ``window`` days.

    Each day is compared with the median and MAD of the days *before* it (so a spike never inflates its own baseline).
    A day is flagged only if it is more than ``threshold`` robust sigmas away AND at least ``min_change`` (25%) away in
    relative terms AND above ``floor`` in absolute terms, so tiny absolute wobbles on near-zero series are ignored.
    """
    pts = sorted(series)
    out: list[Anomaly] = []
    for i, (day, value) in enumerate(pts):
        base = [v for _, v in pts[max(0, i - window) : i]]
        if len(base) < MIN_BASELINE:
            continue
        med = statistics.median(base)
        mad = statistics.median(abs(v - med) for v in base)
        sigma = max(
            mad * MAD_TO_SIGMA, abs(med) * 0.05, 1e-9
        )  # a flat series still has a little natural noise
        score = (value - med) / sigma
        if abs(score) < threshold or abs(value - med) < max(floor, abs(med) * min_change):
            continue
        out.append(Anomaly(day, value, med, round(score, 2), "spike" if score > 0 else "drop"))
    return out
