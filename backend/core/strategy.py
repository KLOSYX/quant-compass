from typing import Optional

import numpy as np
import pandas as pd
from fastapi import HTTPException

from core.constants import (
    DEFAULT_RISK_HORIZON_DAYS,
    MIN_CVAR_TAIL_OBSERVATIONS,
    OPTIMIZED_SIGNAL_EPSILON,
    RISK_RATIO_GRID_STEP,
    VALID_STRATEGY_MODES,
)
from core.risk import (
    calculate_cvar_diagnostics,
    calculate_cvar_loss,
    calculate_drawdown_from_returns,
    calculate_rolling_horizon_returns,
)


def calculate_target_ratio(current_price, ma_value, min_weight, max_weight):
    if ma_value == 0:
        return min_weight, "neutral"

    # Simple linear interpolation between low bias (0.8) and high bias (1.2)
    low_bias = 0.8
    high_bias = 1.2
    bias = current_price / ma_value

    if bias <= low_bias:
        return max_weight, "undervalued"
    if bias >= high_bias:
        return min_weight, "overvalued"

    # Interpolate
    # Ratio = Slope * (Bias - LowBias) + MaxWeight
    slope = (min_weight - max_weight) / (high_bias - low_bias)
    ratio = slope * (bias - low_bias) + max_weight
    return ratio, infer_valuation_signal(current_price, ma_value)


def infer_valuation_signal(current_price: float, ma_value: float) -> str:
    if ma_value == 0:
        return "neutral"
    bias = current_price / ma_value
    if bias < 0.95:
        return "undervalued"
    if bias > 1.05:
        return "overvalued"
    return "neutral"


def validate_strategy_params(
    *,
    strategy_mode: str,
    min_weight: Optional[float],
    max_weight: Optional[float],
    kelly_fraction: float,
    estimation_window: int,
    minimum_cash_reserve: float,
    enable_cvar_constraint: bool,
    cvar_confidence: float,
    cvar_limit: float,
    enable_drawdown_constraint: bool,
    max_drawdown_limit: float,
    allow_auto_bounds: bool = False,
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS,
):
    if strategy_mode not in VALID_STRATEGY_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"strategy_mode must be one of {sorted(VALID_STRATEGY_MODES)}",
        )

    if allow_auto_bounds and (min_weight is None) != (max_weight is None):
        raise HTTPException(
            status_code=400,
            detail="min_weight and max_weight must either both be provided or both be null",
        )

    if min_weight is not None and not (0 <= min_weight <= 1):
        raise HTTPException(status_code=400, detail="min_weight must be within [0, 1]")
    if max_weight is not None and not (0 <= max_weight <= 1):
        raise HTTPException(status_code=400, detail="max_weight must be within [0, 1]")
    if min_weight is not None and max_weight is not None and min_weight > max_weight:
        raise HTTPException(
            status_code=400, detail="min_weight cannot be greater than max_weight"
        )

    if not (0 < kelly_fraction <= 1):
        raise HTTPException(status_code=400, detail="kelly_fraction must be in (0, 1]")
    if estimation_window < 6:
        raise HTTPException(status_code=400, detail="estimation_window must be >= 6")
    if minimum_cash_reserve < 0:
        raise HTTPException(status_code=400, detail="minimum_cash_reserve must be >= 0")
    if not isinstance(enable_cvar_constraint, bool):
        raise HTTPException(
            status_code=400, detail="enable_cvar_constraint must be a boolean"
        )
    if not isinstance(enable_drawdown_constraint, bool):
        raise HTTPException(
            status_code=400, detail="enable_drawdown_constraint must be a boolean"
        )
    if not (0.5 < cvar_confidence < 0.999):
        raise HTTPException(
            status_code=400, detail="cvar_confidence must be in (0.5, 0.999)"
        )
    if not (0 < cvar_limit < 1):
        raise HTTPException(status_code=400, detail="cvar_limit must be in (0, 1)")
    if not (0 < max_drawdown_limit < 1):
        raise HTTPException(
            status_code=400, detail="max_drawdown_limit must be in (0, 1)"
        )
    if not (5 <= risk_horizon_days <= 63):
        raise HTTPException(
            status_code=400, detail="risk_horizon_days must be within [5, 63]"
        )


def _infer_signal_from_bounds(
    target_ratio: float, lower_bound: float, upper_bound: float, epsilon: float
) -> str:
    if upper_bound - lower_bound <= epsilon:
        return "neutral"
    if target_ratio >= upper_bound - epsilon:
        return "undervalued"
    if target_ratio <= lower_bound + epsilon:
        return "overvalued"
    return "neutral"


def get_monthly_rf_return(risk_free_rate: float) -> float:
    if risk_free_rate <= -1:
        return -1.0
    return (1 + risk_free_rate) ** (1 / 12) - 1


def calculate_max_feasible_risk_ratio(
    hist_risky_returns: pd.Series,
    rf_monthly: float,
    effective_upper: float,
    enable_cvar_constraint: bool,
    cvar_confidence: float,
    cvar_limit: float,
    enable_drawdown_constraint: bool,
    max_drawdown_limit: float,
    cvar_risky_returns: Optional[pd.Series] = None,
    drawdown_risky_returns: Optional[pd.Series] = None,
    cvar_rf_return: Optional[float] = None,
    drawdown_rf_return: Optional[float] = None,
):
    if effective_upper <= 0:
        return {
            "max_feasible_ratio_by_cvar": 0.0,
            "max_feasible_ratio_by_drawdown": 0.0,
            "max_feasible_ratio_by_risk": 0.0,
        }

    if hist_risky_returns.empty:
        return {
            "max_feasible_ratio_by_cvar": float(effective_upper),
            "max_feasible_ratio_by_drawdown": float(effective_upper),
            "max_feasible_ratio_by_risk": float(effective_upper),
        }

    cvar_returns = (
        cvar_risky_returns
        if cvar_risky_returns is not None and not cvar_risky_returns.empty
        else hist_risky_returns
    )
    drawdown_returns = (
        drawdown_risky_returns
        if drawdown_risky_returns is not None and not drawdown_risky_returns.empty
        else hist_risky_returns
    )
    cvar_rf = rf_monthly if cvar_rf_return is None else cvar_rf_return
    drawdown_rf = rf_monthly if drawdown_rf_return is None else drawdown_rf_return

    grid = np.arange(0.0, effective_upper + RISK_RATIO_GRID_STEP, RISK_RATIO_GRID_STEP)
    if len(grid) == 0 or grid[-1] < effective_upper:
        grid = np.append(grid, effective_upper)

    max_feasible_cvar = 0.0
    max_feasible_dd = 0.0
    max_feasible_both = 0.0

    for ratio in grid:
        ratio = float(min(max(ratio, 0.0), effective_upper))
        cvar_portfolio_returns = ratio * cvar_returns + (1 - ratio) * cvar_rf
        drawdown_portfolio_returns = (
            ratio * drawdown_returns + (1 - ratio) * drawdown_rf
        )
        cvar_loss = calculate_cvar_loss(cvar_portfolio_returns, cvar_confidence)
        dd_loss = calculate_drawdown_from_returns(drawdown_portfolio_returns)

        cvar_ok = (not enable_cvar_constraint) or (cvar_loss <= cvar_limit)
        dd_ok = (not enable_drawdown_constraint) or (dd_loss <= max_drawdown_limit)

        if not enable_cvar_constraint or cvar_ok:
            max_feasible_cvar = ratio
        if not enable_drawdown_constraint or dd_ok:
            max_feasible_dd = ratio
        if cvar_ok and dd_ok:
            max_feasible_both = ratio

    max_feasible_risk = float(
        min(max_feasible_both, effective_upper)
        if (enable_cvar_constraint or enable_drawdown_constraint)
        else effective_upper
    )
    return {
        "max_feasible_ratio_by_cvar": float(
            min(max_feasible_cvar, effective_upper)
            if enable_cvar_constraint
            else effective_upper
        ),
        "max_feasible_ratio_by_drawdown": float(
            min(max_feasible_dd, effective_upper)
            if enable_drawdown_constraint
            else effective_upper
        ),
        "max_feasible_ratio_by_risk": max_feasible_risk,
    }


def calculate_target_ratio_optimized(
    reference_portfolio_nav: pd.Series,
    timestamp,
    min_weight: float,
    max_weight: float,
    kelly_fraction: float,
    estimation_window: int,
    risk_free_rate: float,
    total_wealth: float,
    minimum_cash_reserve: float,
    enable_cvar_constraint: bool,
    cvar_confidence: float,
    cvar_limit: float,
    enable_drawdown_constraint: bool,
    max_drawdown_limit: float,
    daily_reference_nav: Optional[pd.Series] = None,
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS,
    minimum_cvar_tail_observations: int = MIN_CVAR_TAIL_OBSERVATIONS,
):
    if total_wealth > 0:
        cash_cap_ratio = float(
            np.clip((total_wealth - minimum_cash_reserve) / total_wealth, 0.0, 1.0)
        )
    else:
        cash_cap_ratio = 0.0

    effective_upper = min(max_weight, cash_cap_ratio)
    lower_bound = min_weight if effective_upper >= min_weight else effective_upper

    hist = (
        reference_portfolio_nav.loc[:timestamp]
        .pct_change()
        .dropna()
        .tail(estimation_window)
    )
    rf_monthly = get_monthly_rf_return(risk_free_rate)
    cvar_risky_returns = hist
    drawdown_risky_returns = hist
    cvar_rf_return = rf_monthly
    drawdown_rf_return = rf_monthly
    cvar_data_source = "monthly_fallback"
    drawdown_data_source = "monthly_fallback"
    daily_return_observations = 0

    if daily_reference_nav is not None:
        daily_history = (
            daily_reference_nav.loc[:timestamp]
            .astype(float)
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
            .sort_index()
        )
        daily_returns = daily_history.pct_change().dropna()
        horizon_returns = calculate_rolling_horizon_returns(
            daily_history, risk_horizon_days
        )
        daily_return_observations = int(len(daily_returns))
        if not horizon_returns.empty:
            cvar_risky_returns = horizon_returns
            cvar_rf_return = (1 + risk_free_rate) ** (risk_horizon_days / 252) - 1
            cvar_data_source = "daily_rolling_horizon"
        if not daily_returns.empty:
            drawdown_risky_returns = daily_returns
            drawdown_rf_return = (1 + risk_free_rate) ** (1 / 252) - 1
            drawdown_data_source = "daily_path"

    cvar_diagnostics = calculate_cvar_diagnostics(
        cvar_risky_returns,
        cvar_confidence,
        risk_horizon_days=(
            risk_horizon_days if cvar_data_source == "daily_rolling_horizon" else 21
        ),
        minimum_tail_observations=minimum_cvar_tail_observations,
        observation_stride=(
            risk_horizon_days if cvar_data_source == "daily_rolling_horizon" else 1
        ),
    )
    cvar_hard_constraint_effective = (
        enable_cvar_constraint and cvar_diagnostics["confidence_status"] == "adequate"
    )

    optimizer_info = {
        "mu_excess": None,
        "sigma2": None,
        "full_kelly": None,
        "fractional_kelly": None,
        "cash_cap_ratio": cash_cap_ratio,
        "cash_constrained": effective_upper < max_weight,
        "max_feasible_ratio_by_cvar": float(effective_upper),
        "max_feasible_ratio_by_drawdown": float(effective_upper),
        "max_feasible_ratio_by_risk": float(effective_upper),
        "cvar_estimate_at_target": None,
        "drawdown_estimate_at_target": None,
        "risk_horizon_days": cvar_diagnostics["risk_horizon_days"],
        "cvar_return_observations": cvar_diagnostics["return_observations"],
        "cvar_effective_return_observations": cvar_diagnostics[
            "effective_return_observations"
        ],
        "cvar_effective_tail_count": cvar_diagnostics["cvar_effective_tail_count"],
        "cvar_confidence_status": cvar_diagnostics["confidence_status"],
        "cvar_data_source": cvar_data_source,
        "cvar_hard_constraint_requested": enable_cvar_constraint,
        "cvar_hard_constraint_effective": cvar_hard_constraint_effective,
        "cvar_warning_only": (
            enable_cvar_constraint and not cvar_hard_constraint_effective
        ),
        "drawdown_data_source": drawdown_data_source,
        "daily_return_observations": daily_return_observations,
        "constraint_applied": False,
        "constraint_binding": "cash" if effective_upper < max_weight else "none",
    }

    if len(hist) < 3:
        target_ratio = min(min_weight, effective_upper)
        allocation_signal = _infer_signal_from_bounds(
            target_ratio, lower_bound, effective_upper, OPTIMIZED_SIGNAL_EPSILON
        )
        return target_ratio, allocation_signal, optimizer_info

    mu_excess = float(hist.mean() - rf_monthly)
    sigma2 = float(max(hist.var(ddof=1), 1e-6))
    full_kelly = mu_excess / sigma2
    fractional_kelly = kelly_fraction * full_kelly
    risk_caps = calculate_max_feasible_risk_ratio(
        hist_risky_returns=hist,
        rf_monthly=rf_monthly,
        effective_upper=effective_upper,
        enable_cvar_constraint=cvar_hard_constraint_effective,
        cvar_confidence=cvar_confidence,
        cvar_limit=cvar_limit,
        enable_drawdown_constraint=enable_drawdown_constraint,
        max_drawdown_limit=max_drawdown_limit,
        cvar_risky_returns=cvar_risky_returns,
        drawdown_risky_returns=drawdown_risky_returns,
        cvar_rf_return=cvar_rf_return,
        drawdown_rf_return=drawdown_rf_return,
    )
    final_upper = min(effective_upper, risk_caps["max_feasible_ratio_by_risk"])
    lower_bound = min_weight if final_upper >= min_weight else 0.0
    target_ratio = float(np.clip(fractional_kelly, lower_bound, final_upper))
    target_cvar_returns = (
        target_ratio * cvar_risky_returns + (1 - target_ratio) * cvar_rf_return
    )
    target_drawdown_returns = (
        target_ratio * drawdown_risky_returns + (1 - target_ratio) * drawdown_rf_return
    )

    constraint_applied = cvar_hard_constraint_effective or enable_drawdown_constraint
    cvar_binding = (
        cvar_hard_constraint_effective
        and risk_caps["max_feasible_ratio_by_cvar"] + 1e-9 < effective_upper
    )
    drawdown_binding = (
        enable_drawdown_constraint
        and risk_caps["max_feasible_ratio_by_drawdown"] + 1e-9 < effective_upper
    )
    if cvar_binding and drawdown_binding:
        binding = "both"
    elif cvar_binding:
        binding = "cvar"
    elif drawdown_binding:
        binding = "drawdown"
    elif effective_upper < max_weight:
        binding = "cash"
    else:
        binding = "none"

    optimizer_info.update(
        {
            "mu_excess": mu_excess,
            "sigma2": sigma2,
            "full_kelly": float(full_kelly),
            "fractional_kelly": float(fractional_kelly),
            "max_feasible_ratio_by_cvar": risk_caps["max_feasible_ratio_by_cvar"],
            "max_feasible_ratio_by_drawdown": risk_caps[
                "max_feasible_ratio_by_drawdown"
            ],
            "max_feasible_ratio_by_risk": risk_caps["max_feasible_ratio_by_risk"],
            "cvar_estimate_at_target": float(
                calculate_cvar_loss(target_cvar_returns, cvar_confidence)
            ),
            "drawdown_estimate_at_target": float(
                calculate_drawdown_from_returns(target_drawdown_returns)
            ),
            "constraint_applied": constraint_applied,
            "constraint_binding": binding,
        }
    )

    allocation_signal = _infer_signal_from_bounds(
        target_ratio, lower_bound, final_upper, OPTIMIZED_SIGNAL_EPSILON
    )
    return target_ratio, allocation_signal, optimizer_info


WINDOW_ROBUSTNESS_WINDOWS = (24, 36, 48, 60)
WINDOW_ROBUSTNESS_SPREAD_LIMIT = 0.15


def assess_kelly_window_robustness(
    *,
    reference_portfolio_nav: pd.Series,
    min_weight: float,
    max_weight: float,
    kelly_fraction: float,
    risk_free_rate: float,
    total_wealth: float,
    minimum_cash_reserve: float,
    enable_cvar_constraint: bool,
    cvar_confidence: float,
    cvar_limit: float,
    enable_drawdown_constraint: bool,
    max_drawdown_limit: float,
    daily_reference_nav: Optional[pd.Series] = None,
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS,
):
    """Diagnose whether the fixed 36-month Kelly estimate is window-stable."""
    clean_nav = reference_portfolio_nav.dropna()
    history_count = max(0, len(clean_nav) - 1)
    measurements = []
    for window in WINDOW_ROBUSTNESS_WINDOWS:
        if history_count < window:
            measurements.append(
                {
                    "window_months": window,
                    "available": False,
                    "reason": f"需要至少 {window} 个月收益数据，当前只有 {history_count} 个月",
                }
            )
            continue
        ratio, signal, optimizer_info = calculate_target_ratio_optimized(
            reference_portfolio_nav=clean_nav,
            timestamp=clean_nav.index[-1],
            min_weight=min_weight,
            max_weight=max_weight,
            kelly_fraction=kelly_fraction,
            estimation_window=window,
            risk_free_rate=risk_free_rate,
            total_wealth=total_wealth,
            minimum_cash_reserve=minimum_cash_reserve,
            enable_cvar_constraint=enable_cvar_constraint,
            cvar_confidence=cvar_confidence,
            cvar_limit=cvar_limit,
            enable_drawdown_constraint=enable_drawdown_constraint,
            max_drawdown_limit=max_drawdown_limit,
            daily_reference_nav=daily_reference_nav,
            risk_horizon_days=risk_horizon_days,
        )
        measurements.append(
            {
                "window_months": window,
                "available": True,
                "target_risky_ratio": ratio,
                "allocation_signal": signal,
                "optimizer_info": optimizer_info,
            }
        )

    available = [item for item in measurements if item["available"]]
    base = next((item for item in available if item["window_months"] == 36), None)
    long_windows = [item for item in available if item["window_months"] in {36, 48, 60}]
    if base is None or len(long_windows) < 2:
        return {
            "status": "insufficient_data",
            "base_window_months": 36,
            "measurements": measurements,
            "message": "基金共同历史不足，无法验证 3 年窗口是否稳健；未使用代理历史回填。",
        }

    ratios = [item["target_risky_ratio"] for item in long_windows]
    ratio_spread = float(max(ratios) - min(ratios))
    signals = {item["allocation_signal"] for item in long_windows}
    unstable = ratio_spread > WINDOW_ROBUSTNESS_SPREAD_LIMIT or len(signals) > 1
    return {
        "status": "unstable" if unstable else "stable",
        "base_window_months": 36,
        "measurements": measurements,
        "target_ratio_spread": ratio_spread,
        "signal_consistent": len(signals) == 1,
        "message": (
            "36、48、60 个月窗口给出的 Kelly 配比差异较大；本月应降低对 Kelly 倾斜的信任，优先按固定 DCA 执行。"
            if unstable
            else "36、48、60 个月窗口的 Kelly 配比较接近，3 年窗口目前处于稳定平台。"
        ),
    }
