from typing import Dict, List

import numpy as np
import pandas as pd

from core.portfolio import shrink_frontier_expected_returns


def estimate_account_risk(
    total_return_nav,
    holdings,
    total_wealth,
    *,
    confidence=0.95,
    horizon=21,
    data_verified=True,
):
    """Historical scenario proxy at projected exposures, not an account backtest.

    Cash and receivables carry zero modeled return; real fund sleeves use their
    own observed returns. Insufficient evidence is unavailable, never zero risk.
    """
    missing = {
        "basis": "projected_account_frozen_exposures",
        "status": "unavailable",
        "cvar_loss": None,
        "max_drawdown": None,
        "annualized_volatility": None,
        "horizon_days": horizon,
    }
    active = {c: v for c, v in holdings.items() if v > 1e-9}
    if not data_verified or total_return_nav is None or total_wealth <= 0 or not active:
        return missing
    if set(active) - set(total_return_nav.columns):
        return {**missing, "reason": "missing_held_asset_returns"}
    returns = total_return_nav[list(active)].pct_change(fill_method=None).dropna()
    if len(returns) < horizon + 1:
        return {**missing, "reason": "insufficient_observations"}
    account_returns = returns.mul(pd.Series(active) / total_wealth).sum(axis=1)
    horizon_returns = (1 + account_returns).rolling(horizon).apply(
        np.prod, raw=True
    ) - 1
    scenarios = horizon_returns.dropna()
    diagnostic = calculate_cvar_diagnostics(
        scenarios,
        confidence,
        risk_horizon_days=horizon,
        minimum_tail_observations=5,
        observation_stride=horizon,
    )
    return {
        "basis": "projected_account_frozen_exposures",
        "status": diagnostic["confidence_status"],
        "cvar_loss": calculate_cvar_loss(scenarios, confidence),
        "max_drawdown": calculate_drawdown_from_returns(account_returns),
        "annualized_volatility": float(account_returns.std(ddof=1) * np.sqrt(252)),
        "observations": len(returns),
        "horizon_days": horizon,
        "effective_tail_count": diagnostic["cvar_effective_tail_count"],
        "cash_and_receivables_return": 0.0,
    }


def calculate_nav_max_drawdown(nav_series: pd.Series) -> float:
    nav = pd.Series(nav_series, dtype=float)
    if nav.empty:
        return 0.0
    rolling_max = nav.cummax().replace(0, np.nan)
    drawdowns = nav / rolling_max - 1
    finite_drawdowns = drawdowns.replace([np.inf, -np.inf], np.nan).dropna()
    if finite_drawdowns.empty:
        return 0.0
    return float(abs(finite_drawdowns.min()))


def calculate_asset_diagnostics(
    df_nav: pd.DataFrame,
    fund_names: Dict[str, str],
    efficient_frontier: List[Dict],
) -> List[Dict]:
    if df_nav.empty:
        return []

    monthly_returns = df_nav.pct_change().fillna(0)
    raw_expected_returns = monthly_returns.mean()
    optimizer_expected_returns = shrink_frontier_expected_returns(raw_expected_returns)
    annualized_volatility = monthly_returns.std(ddof=0) * np.sqrt(12)
    years = max((df_nav.index[-1] - df_nav.index[0]).days / 365.25, 0.0)
    frontier_weight_series = {
        code: np.array(
            [float(point["weights"].get(code, 0.0)) for point in efficient_frontier],
            dtype=float,
        )
        for code in df_nav.columns
    }

    rf_return = float(optimizer_expected_returns.get("RiskFree", 0.0) * 12)
    risky_sharpes = {}
    for code in df_nav.columns:
        if code == "RiskFree":
            continue
        vol = float(annualized_volatility.get(code, 0.0))
        excess_return = float(
            optimizer_expected_returns.get(code, 0.0) * 12 - rf_return
        )
        if vol <= 1e-12:
            risky_sharpes[code] = float("inf") if excess_return > 0 else 0.0
        else:
            risky_sharpes[code] = excess_return / vol

    sharpe_rank = {
        code: rank
        for rank, (code, _) in enumerate(
            sorted(risky_sharpes.items(), key=lambda item: item[1], reverse=True),
            start=1,
        )
    }

    diagnostics = []
    for code in df_nav.columns:
        nav_series = df_nav[code]
        total_return = float(nav_series.iloc[-1] / nav_series.iloc[0] - 1)
        sample_cagr = (
            float((nav_series.iloc[-1] / nav_series.iloc[0]) ** (1 / years) - 1)
            if years > 0 and nav_series.iloc[0] > 0 and nav_series.iloc[-1] > 0
            else 0.0
        )
        ann_return = float(raw_expected_returns.get(code, 0.0) * 12)
        optimizer_return = float(optimizer_expected_returns.get(code, 0.0) * 12)
        vol = float(annualized_volatility.get(code, 0.0))
        max_drawdown = calculate_nav_max_drawdown(nav_series)
        weights = frontier_weight_series.get(code, np.array([], dtype=float))
        points_used = int(np.sum(weights > 1e-6))
        max_weight = float(weights.max()) if weights.size else 0.0
        avg_weight = float(weights.mean()) if weights.size else 0.0
        sharpe = None if code == "RiskFree" else float(risky_sharpes.get(code, 0.0))
        rank = None if code == "RiskFree" else sharpe_rank.get(code)

        if code == "RiskFree":
            status = "risk_free_anchor"
        elif points_used > 0:
            status = "selected_on_frontier"
        elif optimizer_return <= rf_return + 1e-9:
            status = "below_risk_free"
        elif rank is not None and rank > 1:
            status = "dominated_by_higher_sharpe_assets"
        else:
            status = "unused_in_sample"

        diagnostics.append(
            {
                "code": code,
                "name": fund_names.get(code, code),
                "sample_total_return": total_return,
                "sample_cagr": sample_cagr,
                "sample_annualized_return": ann_return,
                "optimizer_expected_return": optimizer_return,
                "annualized_volatility": vol,
                "max_drawdown": max_drawdown,
                "sharpe_vs_riskfree": sharpe,
                "sharpe_rank": rank,
                "frontier_points_used": points_used,
                "frontier_point_count": len(efficient_frontier),
                "max_frontier_weight": max_weight,
                "avg_frontier_weight": avg_weight,
                "status": status,
            }
        )

    return diagnostics


def calculate_max_drawdown(nav_series: pd.Series) -> float:
    """Return max drawdown as a decimal (e.g., 0.2 for -20%)."""
    if nav_series.empty:
        return 0.0
    rolling_max = nav_series.cummax()
    drawdowns = (nav_series - rolling_max) / rolling_max
    return drawdowns.min()


def calculate_cvar_loss(returns: pd.Series, confidence: float) -> float:
    if returns.empty:
        return 0.0
    losses = -returns.astype(float)
    tail_count = max(1, int(np.ceil(len(losses) * (1 - confidence))))
    tail_losses = losses.nlargest(tail_count)
    return max(0.0, float(tail_losses.mean()))


def calculate_cvar_diagnostics(
    returns: pd.Series,
    confidence: float,
    *,
    risk_horizon_days: int,
    minimum_tail_observations: int,
    observation_stride: int = 1,
) -> dict:
    clean_returns = returns.astype(float).replace([np.inf, -np.inf], np.nan).dropna()
    effective_observations = (
        int(np.ceil(len(clean_returns) / max(1, observation_stride)))
        if not clean_returns.empty
        else 0
    )
    if effective_observations == 0:
        tail_count = 0
    else:
        tail_count = max(1, int(np.ceil(effective_observations * (1 - confidence))))
    return {
        "return_observations": int(len(clean_returns)),
        "effective_return_observations": effective_observations,
        "cvar_effective_tail_count": tail_count,
        "risk_horizon_days": int(risk_horizon_days),
        "cvar_confidence": float(confidence),
        "cvar_loss": float(calculate_cvar_loss(clean_returns, confidence)),
        "minimum_tail_observations": int(minimum_tail_observations),
        "confidence_status": (
            "adequate" if tail_count >= minimum_tail_observations else "low"
        ),
    }


def calculate_rolling_horizon_returns(
    daily_nav: pd.Series, risk_horizon_days: int
) -> pd.Series:
    if risk_horizon_days < 1:
        raise ValueError("risk_horizon_days must be at least 1")
    clean_nav = (
        daily_nav.astype(float).replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    )
    return clean_nav.pct_change(periods=risk_horizon_days).dropna()


def calculate_drawdown_from_returns(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    # Include the initial unit NAV so a loss in the first observed period is
    # measured against the actual starting peak of 1.0.
    nav = pd.concat(
        [
            pd.Series([1.0], dtype=float),
            (1 + returns.astype(float)).cumprod().reset_index(drop=True),
        ],
        ignore_index=True,
    )
    return abs(float(calculate_max_drawdown(nav)))
