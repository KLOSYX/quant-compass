import math
from typing import Dict, Mapping, Optional

import pandas as pd

from core.validation import non_negative_number


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
        daily_limit = non_negative_number(daily_limit, "daily investment limit")
        purchase_days = (
            1 if planned_purchase_days is None else int(planned_purchase_days)
        )
        if purchase_days < 1:
            raise ValueError("planned_purchase_days must be at least 1")
        # A count is a user-supplied plan, not an exchange calendar. Never
        # extrapolate one daily allowance into an unrequested monthly schedule.
        remaining_dates = (
            (pd.Timestamp(timestamp) + pd.offsets.MonthEnd(0)).day
            - pd.Timestamp(timestamp).day
            + 1
        )
        purchase_days = min(purchase_days, remaining_dates)
        used_today = non_negative_number(
            limit_config.get("daily_used", 0), "daily used limit"
        )
        candidates.append(max(0.0, daily_limit * purchase_days - used_today))

    if monthly_limit is not None and monthly_limit != "":
        monthly_limit = non_negative_number(monthly_limit, "monthly investment limit")
        used_month = non_negative_number(
            limit_config.get("monthly_used", 0), "monthly used limit"
        )
        candidates.append(max(0.0, monthly_limit - used_month))

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
