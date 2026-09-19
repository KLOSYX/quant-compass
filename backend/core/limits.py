import math
from typing import Dict, Mapping, Optional

from core.validation import non_negative_number


def monthly_investment_limit(
    limit_config, timestamp, *, planned_purchase_days=None
) -> float:
    """Approximate period capacity using user-configured trading days, not a calendar."""
    if limit_config is None:
        return math.inf
    config = (
        limit_config.model_dump()
        if hasattr(limit_config, "model_dump")
        else dict(limit_config)
    )
    days = 21 if planned_purchase_days is None else int(planned_purchase_days)
    if days < 1:
        raise ValueError("planned_purchase_days must be at least 1")
    candidates = []
    if config.get("daily_limit") not in (None, ""):
        daily = non_negative_number(config["daily_limit"], "daily investment limit")
        used = non_negative_number(config.get("daily_used", 0), "daily used limit")
        candidates.append(max(0.0, daily * days - used))
    if config.get("monthly_limit") not in (None, ""):
        monthly = non_negative_number(
            config["monthly_limit"], "monthly investment limit"
        )
        used = non_negative_number(config.get("monthly_used", 0), "monthly used limit")
        candidates.append(max(0.0, monthly - used))
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
