from collections import defaultdict
from typing import Mapping, Sequence


ASSET_CATEGORIES = (
    "equity",
    "bond",
    "commodity",
    "gold",
    "money_market",
    "cash_equivalent",
    "other",
)
DEFAULT_ASSET_CATEGORY = "other"
RISK_ASSET_CATEGORIES = frozenset({"equity", "bond", "commodity", "gold", "other"})


def normalize_asset_categories(
    fund_codes: Sequence[str], asset_categories: Mapping[str, str] | None
) -> dict[str, str]:
    codes = list(dict.fromkeys(fund_codes))
    code_set = set(codes)
    supplied = asset_categories or {}
    unknown_codes = sorted(set(supplied) - code_set)
    if unknown_codes:
        raise ValueError(
            "asset_categories contain assets outside the current analysis universe: "
            + ", ".join(unknown_codes)
        )

    normalized = {}
    for code in codes:
        default = "cash_equivalent" if code == "RiskFree" else DEFAULT_ASSET_CATEGORY
        category = str(supplied.get(code, default)).strip()
        if category not in ASSET_CATEGORIES:
            raise ValueError(f"invalid asset category for {code}: {category!r}")
        normalized[code] = category
    return normalized


def aggregate_category_values(
    asset_values: Mapping[str, float], asset_categories: Mapping[str, str]
) -> dict[str, float]:
    totals = defaultdict(float)
    for code, raw_value in asset_values.items():
        value = float(raw_value or 0.0)
        if value <= 0:
            continue
        category = asset_categories.get(code, DEFAULT_ASSET_CATEGORY)
        if category not in ASSET_CATEGORIES:
            raise ValueError(f"invalid asset category for {code}: {category!r}")
        totals[category] += value
    return dict(totals)


def calculate_exposure_metrics(
    asset_values: Mapping[str, float],
    *,
    total_wealth: float,
    asset_categories: Mapping[str, str],
) -> dict[str, object]:
    category_values = aggregate_category_values(asset_values, asset_categories)
    denominator = max(0.0, float(total_wealth or 0.0))
    category_exposures = {
        category: value / denominator if denominator > 0 else 0.0
        for category, value in category_values.items()
    }
    return {
        "category_values": category_values,
        "category_exposures": category_exposures,
        "equity_exposure": category_exposures.get("equity", 0.0),
        "risk_asset_exposure": sum(
            exposure
            for category, exposure in category_exposures.items()
            if category in RISK_ASSET_CATEGORIES
        ),
    }
