from typing import Dict, List

import numpy as np
import pandas as pd

from core.portfolio import shrink_frontier_expected_returns


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
    var_loss = float(losses.quantile(confidence))
    tail_losses = losses[losses >= var_loss]
    if tail_losses.empty:
        return max(0.0, var_loss)
    return max(0.0, float(tail_losses.mean()))


def calculate_drawdown_from_returns(returns: pd.Series) -> float:
    if returns.empty:
        return 0.0
    nav = (1 + returns.astype(float)).cumprod()
    return abs(float(calculate_max_drawdown(nav)))
