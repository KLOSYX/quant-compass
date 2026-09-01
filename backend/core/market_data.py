"""Corporate-action-aware total-return reconstruction."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd

from core.domain import (
    AssetPriceSeries,
    AssetTotalReturnSeries,
    CorporateAction,
    MarketSnapshot,
    ReturnQuality,
)


def reconstruct_total_return_index(
    price_series: pd.Series,
    corporate_actions: Iterable[CorporateAction] = (),
) -> pd.Series:
    prices = price_series.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    prices.index = pd.to_datetime(prices.index)
    prices = prices.sort_index()
    if prices.empty or (prices <= 0).any():
        raise ValueError("price series must contain positive finite observations")
    distributions: dict[pd.Timestamp, float] = {}
    splits: dict[pd.Timestamp, float] = {}
    for action in corporate_actions:
        if action.ex_date is None:
            continue
        event_date = (
            prices.index[prices.index.searchsorted(action.ex_date)]
            if action.ex_date <= prices.index[-1]
            else None
        )
        if event_date is None:
            continue
        if action.kind == "cash_distribution":
            distributions[event_date] = (
                distributions.get(event_date, 0.0) + action.cash_per_share
            )
        elif action.kind == "split":
            splits[event_date] = splits.get(event_date, 1.0) * action.split_ratio
    levels = [1.0]
    for position in range(1, len(prices)):
        timestamp = prices.index[position]
        previous_price = float(prices.iloc[position - 1])
        current_value_per_previous_share = float(prices.iloc[position]) * splits.get(
            timestamp, 1.0
        ) + distributions.get(timestamp, 0.0)
        period_return = current_value_per_previous_share / previous_price - 1.0
        if not np.isfinite(period_return) or period_return <= -1.0:
            raise ValueError(
                "corporate-action reconstruction produced an invalid return"
            )
        levels.append(levels[-1] * (1.0 + period_return))
    return pd.Series(levels, index=prices.index, dtype=float, name="total_return_index")


def build_market_snapshot(
    *,
    price_series: Mapping[str, pd.Series],
    corporate_actions: Iterable[CorporateAction],
    return_quality: Mapping[str, ReturnQuality],
    source: str,
    fetched_at,
    available_at: Mapping[str, object] | None = None,
) -> MarketSnapshot:
    actions = tuple(corporate_actions)
    prices = {
        code: AssetPriceSeries(
            asset_code=code,
            values=series,
            source=source,
            observed_at=pd.Timestamp(fetched_at),
            availability_quality=return_quality[code].availability_quality,
        )
        for code, series in price_series.items()
    }
    total_returns = {
        code: AssetTotalReturnSeries(
            asset_code=code,
            values=reconstruct_total_return_index(
                series, [action for action in actions if action.asset_code == code]
            ),
            source=source,
        )
        for code, series in price_series.items()
    }
    availability = {
        code: pd.Timestamp((available_at or {}).get(code, fetched_at))
        for code in price_series
    }
    return MarketSnapshot(
        price_series=prices,
        total_return_series=total_returns,
        corporate_actions=actions,
        return_quality=return_quality,
        fetched_at=pd.Timestamp(fetched_at),
        available_at=availability,
        source=source,
    )
