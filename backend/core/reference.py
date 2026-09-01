"""Scale-invariant reference basket construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd

from core.portfolio import normalize_weights


def _weight_schedule_frame(
    weight_schedule: Mapping[str, float] | pd.DataFrame,
    columns: list[str],
    first_date: pd.Timestamp,
) -> pd.DataFrame:
    if isinstance(weight_schedule, pd.DataFrame):
        schedule = weight_schedule.copy()
        schedule.index = pd.to_datetime(schedule.index)
        schedule = schedule.sort_index()
    else:
        schedule = pd.DataFrame([dict(weight_schedule)], index=[first_date])
    if schedule.empty:
        raise ValueError("weight_schedule must not be empty")
    unknown = sorted(set(schedule.columns) - set(columns))
    if unknown and (schedule[unknown].fillna(0.0).abs() > 1e-12).any().any():
        raise ValueError(
            "weight schedule contains assets outside the reference universe: "
            + ", ".join(unknown)
        )
    normalized_rows = []
    for _, row in schedule.iterrows():
        normalized_rows.append(normalize_weights(row.to_dict(), columns))
    return pd.DataFrame(normalized_rows, index=schedule.index, columns=columns)


def _default_rebalance_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    periods = index.to_period("M")
    return pd.DatetimeIndex(
        [
            index[position]
            for position in range(len(index))
            if position == 0 or periods[position] != periods[position - 1]
        ]
    )


def build_reference_basket(
    total_return_series: pd.DataFrame,
    weight_schedule: Mapping[str, float] | pd.DataFrame,
    rebalance_effective_dates: Sequence | None = None,
    valuation_calendar: Sequence | None = None,
    missing_data_policy: str = "raise",
) -> pd.Series:
    """Build a monthly-rebalanced basket from total-return level series.

    Weights become effective only after valuation on their effective date.  The
    resulting basket therefore uses information available at the rebalance date
    for subsequent returns and never rewrites earlier history.
    """

    if missing_data_policy not in {"raise", "ffill"}:
        raise ValueError("missing_data_policy must be 'raise' or 'ffill'")
    levels = total_return_series.astype(float).copy()
    levels.index = pd.to_datetime(levels.index)
    levels = levels.sort_index()
    if valuation_calendar is not None:
        calendar = pd.DatetimeIndex(
            pd.to_datetime(list(valuation_calendar))
        ).sort_values()
        levels = levels.reindex(calendar)
    levels = levels.replace([np.inf, -np.inf], np.nan)
    if missing_data_policy == "ffill":
        levels = levels.ffill()
    if levels.empty or levels.isna().any().any():
        raise ValueError("total return series contains unavailable observations")
    if (levels <= 0).any().any():
        raise ValueError("total return series must be strictly positive")
    columns = list(levels.columns)
    schedule = _weight_schedule_frame(weight_schedule, columns, levels.index[0])
    if schedule.index[0] > levels.index[0]:
        raise ValueError(
            "weight_schedule must be effective by the first valuation date"
        )
    rebalances = (
        _default_rebalance_dates(levels.index)
        if rebalance_effective_dates is None
        else pd.DatetimeIndex(pd.to_datetime(list(rebalance_effective_dates)))
    )
    rebalance_set = set(rebalances)

    initial_weights = schedule.loc[: levels.index[0]].iloc[-1]
    shares = initial_weights / levels.iloc[0]
    values = {levels.index[0]: 1.0}
    for position in range(1, len(levels)):
        timestamp = levels.index[position]
        prices = levels.iloc[position]
        wealth = float((shares * prices).sum())
        if not np.isfinite(wealth) or wealth <= 0:
            raise ValueError(
                "reference basket wealth became non-finite or non-positive"
            )
        values[timestamp] = wealth
        if timestamp in rebalance_set:
            effective = schedule.loc[:timestamp]
            if effective.empty:
                raise ValueError(f"no reference weights are available at {timestamp}")
            shares = wealth * effective.iloc[-1] / prices
    result = pd.Series(values, dtype=float, name="reference_basket")
    return result / float(result.iloc[0])
