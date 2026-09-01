"""Cash-flow-aware performance metrics."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


def calculate_xirr(cash_flows: Iterable[tuple[object, float]]) -> float | None:
    flows = [(pd.Timestamp(date), float(amount)) for date, amount in cash_flows]
    if (
        not flows
        or not any(amount < 0 for _, amount in flows)
        or not any(amount > 0 for _, amount in flows)
    ):
        return None
    origin = min(date for date, _ in flows)

    def npv(rate: float) -> float:
        return sum(
            amount / ((1.0 + rate) ** ((date - origin).days / 365.25))
            for date, amount in flows
        )

    lower, upper = -0.999999, 10.0
    low_value, high_value = npv(lower), npv(upper)
    while low_value * high_value > 0 and upper < 1_000_000:
        upper *= 10.0
        high_value = npv(upper)
    if (
        not np.isfinite(low_value)
        or not np.isfinite(high_value)
        or low_value * high_value > 0
    ):
        return None
    for _ in range(160):
        middle = (lower + upper) / 2.0
        middle_value = npv(middle)
        if abs(middle_value) < 1e-10:
            return middle
        if low_value * middle_value <= 0:
            upper, high_value = middle, middle_value
        else:
            lower, low_value = middle, middle_value
    return (lower + upper) / 2.0


def performance_summary(
    unit_nav: pd.Series,
    external_contributions: Iterable[tuple[object, float]],
    final_value: float,
) -> dict[str, float | None | str | int]:
    series = unit_nav.astype(float).dropna()
    flows = [(date, -abs(float(amount))) for date, amount in external_contributions]
    if not series.empty:
        flows.append((series.index[-1], float(final_value)))
    return {
        "fee_after_twr": float(series.iloc[-1] - 1.0) if not series.empty else 0.0,
        "xirr": calculate_xirr(flows),
        "return_frequency": "monthly",
        "drawdown_metric": "monthly_max_drawdown",
        "observations": int(len(series)),
    }
