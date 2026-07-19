import math
from typing import Dict, Mapping, Optional

import pandas as pd


def business_days_in_month(timestamp) -> int:
    ts = pd.Timestamp(timestamp)
    start = ts.replace(day=1)
    end = start + pd.offsets.MonthEnd(0)
    return max(1, len(pd.bdate_range(start=start, end=end)))


def monthly_investment_limit(
    limit_config, timestamp, *, planned_purchase_days: int | None = None
) -> float:
    if limit_config is None:
        return math.inf

    if hasattr(limit_config, "model_dump"):
        limit_config = limit_config.model_dump()
    elif hasattr(limit_config, "dict"):
        limit_config = limit_config.dict()

    daily_limit = (
        limit_config.get("daily_limit") if isinstance(limit_config, dict) else None
    )
    monthly_limit = (
        limit_config.get("monthly_limit") if isinstance(limit_config, dict) else None
    )
    candidates = []

    if daily_limit is not None and daily_limit != "":
        daily_limit = float(daily_limit)
        if daily_limit < 0:
            raise ValueError("daily investment limit must be non-negative")
        purchase_days = (
            business_days_in_month(timestamp)
            if planned_purchase_days is None
            else int(planned_purchase_days)
        )
        if purchase_days < 1:
            raise ValueError("planned_purchase_days must be at least 1")
        candidates.append(daily_limit * purchase_days)

    if monthly_limit is not None and monthly_limit != "":
        monthly_limit = float(monthly_limit)
        if monthly_limit < 0:
            raise ValueError("monthly investment limit must be non-negative")
        candidates.append(monthly_limit)

    return min(candidates) if candidates else math.inf


def get_monthly_investment_limits(
    fund_codes,
    fund_investment_limits: Optional[Mapping[str, object]],
    timestamp,
    *,
    planned_purchase_days: int | None = None,
) -> Dict[str, float]:
    limits = fund_investment_limits or {}
    return {
        code: monthly_investment_limit(
            limits.get(code),
            timestamp,
            planned_purchase_days=planned_purchase_days,
        )
        for code in fund_codes
    }


def allocate_capped_buy_amounts(
    fund_codes,
    fund_gaps: Mapping[str, float],
    risky_weights: Mapping[str, float],
    buy_fees: Optional[Mapping[str, float]],
    max_cash_to_spend: float,
    fund_investment_limits: Optional[Mapping[str, object]],
    timestamp,
    *,
    planned_purchase_days: int | None = None,
) -> Dict[str, float]:
    """Allocate gross buy cash across funds while respecting per-fund monthly caps."""
    if max_cash_to_spend <= 0:
        return {code: 0.0 for code in fund_codes}

    buy_fees = buy_fees or {}
    monthly_limits = get_monthly_investment_limits(
        fund_codes,
        fund_investment_limits,
        timestamp,
        planned_purchase_days=planned_purchase_days,
    )
    allocations = {code: 0.0 for code in fund_codes}
    remaining_cash = float(max_cash_to_spend)
    active = {
        code
        for code in fund_codes
        if fund_gaps.get(code, 0.0) > 1e-9 and risky_weights.get(code, 0.0) > 1e-12
    }

    while active and remaining_cash > 1e-9:
        total_gap = sum(max(0.0, float(fund_gaps.get(code, 0.0))) for code in active)
        if total_gap <= 1e-12:
            break

        spent_this_round = 0.0
        saturated = set()

        for code in list(active):
            fee = max(0.0, float(buy_fees.get(code, 0.0)))
            gross_gap = max(0.0, float(fund_gaps.get(code, 0.0))) * (1 + fee)
            capacity = min(
                gross_gap - allocations[code],
                monthly_limits.get(code, math.inf) - allocations[code],
            )
            if capacity <= 1e-9:
                saturated.add(code)
                continue

            proposed = remaining_cash * (
                max(0.0, float(fund_gaps.get(code, 0.0))) / total_gap
            )
            amount = min(proposed, capacity)
            if amount > 1e-9:
                allocations[code] += amount
                spent_this_round += amount
            if capacity - amount <= 1e-9:
                saturated.add(code)

        remaining_cash -= spent_this_round
        active -= saturated
        if spent_this_round <= 1e-9:
            break

    return allocations
