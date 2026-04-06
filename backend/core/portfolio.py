from typing import Dict, List

import numpy as np
import pandas as pd
from fastapi import HTTPException

from core.constants import MAX_SINGLE_WEIGHT, RETURN_SHRINKAGE


def normalize_weights(
    weights_dict: Dict[str, float],
    columns: List[str],
    *,
    include_risk_free: bool = True,
) -> pd.Series:
    weights = pd.Series(weights_dict, dtype=float).reindex(columns).fillna(0.0)
    weights = weights.clip(lower=0.0)
    if not include_risk_free and "RiskFree" in weights.index:
        weights["RiskFree"] = 0.0
    total = float(weights.sum())
    if total > 0:
        weights /= total
    return weights


def normalize_risky_weights(
    weights_dict: Dict[str, float], columns: List[str]
) -> pd.Series:
    risky_columns = [code for code in columns if code != "RiskFree"]
    return normalize_weights(weights_dict, risky_columns, include_risk_free=False)


def validate_weight_universe(
    weights_dict: Dict[str, float], columns: List[str]
) -> None:
    unknown_positive_assets = sorted(
        code
        for code, weight in weights_dict.items()
        if code not in columns and float(weight) > 1e-12
    )
    if unknown_positive_assets:
        raise HTTPException(
            status_code=400,
            detail=(
                "weights contain assets outside the current analysis universe: "
                + ", ".join(unknown_positive_assets)
            ),
        )

    available_positive_weight = sum(
        max(float(weights_dict.get(code, 0.0)), 0.0) for code in columns
    )
    if available_positive_weight <= 1e-12:
        raise HTTPException(
            status_code=400,
            detail="weights must allocate positive weight to at least one available asset",
        )


def decompose_selected_weights(
    weights_dict: Dict[str, float], columns: List[str]
) -> Dict[str, pd.Series | float]:
    validate_weight_universe(weights_dict, columns)
    full_weights = normalize_weights(weights_dict, columns)
    base_risk_free_ratio = float(full_weights.get("RiskFree", 0.0))
    base_risky_ratio = float(full_weights.drop("RiskFree", errors="ignore").sum())
    risky_weights = normalize_risky_weights(full_weights.to_dict(), columns)
    return {
        "full_weights": full_weights,
        "risky_weights": risky_weights,
        "base_risky_ratio": base_risky_ratio,
        "base_risk_free_ratio": base_risk_free_ratio,
    }


def get_effective_single_weight_cap(num_assets: int) -> float:
    if num_assets <= 1:
        return 1.0
    return max(MAX_SINGLE_WEIGHT, 1.0 / num_assets)


def get_frontier_weight_bounds(columns: List[str]) -> tuple[tuple[float, float], ...]:
    num_assets = len(columns)
    max_single_weight = get_effective_single_weight_cap(num_assets)
    bounds = []
    for code in columns:
        if code == "RiskFree":
            bounds.append((0.0, 1.0))
        else:
            bounds.append((0.0, max_single_weight))
    return tuple(bounds)


def get_frontier_initial_guess(columns: List[str]) -> np.ndarray:
    if "RiskFree" in columns:
        guess = np.zeros(len(columns), dtype=float)
        guess[columns.index("RiskFree")] = 1.0
        return guess
    return np.full(len(columns), 1.0 / len(columns), dtype=float)


def get_max_return_weights(
    expected_returns: pd.Series, bounds: tuple[tuple[float, float], ...]
) -> np.ndarray:
    weights = np.zeros(len(expected_returns), dtype=float)
    remaining = 1.0
    ranked_indices = sorted(
        range(len(expected_returns)),
        key=lambda idx: float(expected_returns.iloc[idx]),
        reverse=True,
    )

    for idx in ranked_indices:
        lower, upper = bounds[idx]
        if remaining <= 0:
            break
        alloc = min(upper, remaining)
        weights[idx] = max(lower, alloc)
        remaining -= weights[idx]

    if remaining > 1e-12:
        for idx, (lower, upper) in enumerate(bounds):
            available = upper - weights[idx]
            if available <= 0:
                continue
            extra = min(available, remaining)
            weights[idx] += extra
            remaining -= extra
            if remaining <= 1e-12:
                break

    total = weights.sum()
    if total > 0:
        weights /= total
    return weights


def append_fee_warnings(
    warnings: List[str],
    fund_fees: Dict[str, float],
    *,
    apply_fund_fees_to_history: bool,
):
    has_nonzero_fee = any(abs(fee) > 1e-12 for fee in fund_fees.values())
    if not has_nonzero_fee:
        return
    if apply_fund_fees_to_history:
        warnings.append(
            "已按输入管理费对历史净值额外扣减。若使用的是公募基金单位净值，这通常会重复计入管理费。"
        )
    else:
        warnings.append(
            "历史回测默认不额外扣减管理费，因为基金单位净值通常已包含管理费影响。"
        )


def shrink_frontier_expected_returns(raw_expected_returns: pd.Series) -> pd.Series:
    expected_returns = raw_expected_returns.copy()
    if "RiskFree" in raw_expected_returns.index:
        risky_expected_returns = raw_expected_returns.drop("RiskFree")
        if not risky_expected_returns.empty:
            expected_returns.loc[risky_expected_returns.index] = (
                (1 - RETURN_SHRINKAGE) * risky_expected_returns
                + RETURN_SHRINKAGE * risky_expected_returns.mean()
            )
        expected_returns["RiskFree"] = raw_expected_returns["RiskFree"]
    else:
        expected_returns = (
            1 - RETURN_SHRINKAGE
        ) * raw_expected_returns + RETURN_SHRINKAGE * raw_expected_returns.mean()
    return expected_returns
