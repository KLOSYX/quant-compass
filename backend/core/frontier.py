from typing import Dict, List, Literal

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.constants import (
    COVARIANCE_SHRINKAGE,
    DEFAULT_CVAR_CONFIDENCE,
    MIN_WALK_FORWARD_TRAIN_MONTHS,
)
from core.portfolio import (
    get_frontier_initial_guess,
    get_frontier_weight_bounds,
    get_max_return_weights,
    normalize_weights,
    shrink_frontier_expected_returns,
)
from core.risk import calculate_cvar_loss, calculate_drawdown_from_returns

CovarianceMethod = Literal["fixed_20", "ledoit_wolf"]


def estimate_covariance(
    monthly_returns: pd.DataFrame,
    method: CovarianceMethod = "fixed_20",
) -> tuple[pd.DataFrame, float]:
    """Estimate covariance and return the applied shrinkage intensity."""
    clean_returns = monthly_returns.astype(float).replace([np.inf, -np.inf], np.nan)
    clean_returns = clean_returns.dropna(how="any")
    columns = list(monthly_returns.columns)
    if clean_returns.empty:
        return pd.DataFrame(0.0, index=columns, columns=columns), 0.0

    risky_columns = [code for code in columns if code != "RiskFree"]
    result = pd.DataFrame(0.0, index=columns, columns=columns)
    if not risky_columns:
        return result, 0.0

    risky_returns = clean_returns[risky_columns]
    empirical = risky_returns.cov()
    if method == "fixed_20":
        target = pd.DataFrame(
            np.diag(np.diag(empirical.values)),
            index=risky_columns,
            columns=risky_columns,
        )
        shrunk = (1 - COVARIANCE_SHRINKAGE) * empirical + COVARIANCE_SHRINKAGE * target
        intensity = COVARIANCE_SHRINKAGE
    elif method == "ledoit_wolf":
        values = risky_returns.to_numpy(dtype=float)
        values -= values.mean(axis=0, keepdims=True)
        observations, dimensions = values.shape
        empirical_values = values.T @ values / max(observations, 1)
        mu = float(np.trace(empirical_values) / dimensions)
        delta = float(
            np.sum((empirical_values - mu * np.eye(dimensions)) ** 2) / dimensions
        )
        squared = values**2
        beta = float(
            np.sum(squared.T @ squared / max(observations, 1) - empirical_values**2)
            / max(dimensions * observations, 1)
        )
        beta = min(max(beta, 0.0), delta)
        intensity = float(beta / delta) if delta > 1e-18 else 0.0
        shrunk_values = (1 - intensity) * empirical_values + intensity * mu * np.eye(
            dimensions
        )
        shrunk = pd.DataFrame(shrunk_values, index=risky_columns, columns=risky_columns)
    else:
        raise ValueError(f"unsupported covariance method: {method}")

    result.loc[risky_columns, risky_columns] = shrunk
    return result, float(intensity)


def append_frontier_stability_warnings(
    warnings: List[str], df_nav: pd.DataFrame, fund_fees: Dict[str, float]
):
    risky_df = df_nav.drop(columns=["RiskFree"], errors="ignore")
    if len(risky_df.columns) == 0:
        return

    warnings.append(
        "有效前沿权重仍基于全样本静态估计；前沿 Walk-forward 只诊断基础篮子，不能替代完整可执行策略的样本外对比。"
    )

    if len(risky_df) < 24:
        warnings.append("样本少于24个月，前沿权重稳定性较弱。")
        return

    full_frontier = calculate_efficient_frontier(df_nav, fund_fees)
    first_half = calculate_efficient_frontier(
        df_nav.iloc[: len(df_nav) // 2], fund_fees
    )
    second_half = calculate_efficient_frontier(
        df_nav.iloc[len(df_nav) // 2 :], fund_fees
    )
    if not full_frontier or not first_half or not second_half:
        return

    def _mid_weights(frontier):
        mid = frontier[len(frontier) // 2]["weights"]
        return normalize_weights(mid, list(mid.keys()))

    first_weights = _mid_weights(first_half)
    second_weights = _mid_weights(second_half)

    weight_drift = float((first_weights - second_weights).abs().sum() / 2)
    if weight_drift >= 0.20:
        warnings.append(
            f"前沿中位点在前后半样本的权重漂移约为 {weight_drift * 100:.1f}%，说明基础配置对时间窗口较敏感。"
        )


def calculate_efficient_frontier(
    df,
    fund_fees,
    *,
    covariance_method: CovarianceMethod = "fixed_20",
):
    if list(df.columns) == ["RiskFree"]:
        monthly_returns = df.pct_change().dropna()
        expected_return = monthly_returns["RiskFree"].mean()
        return [
            {"risk": 0, "return": expected_return * 12, "weights": {"RiskFree": 1.0}}
        ]

    if len(df.columns) == 1:
        monthly_returns = df.pct_change().dropna()
        code = df.columns[0]
        expected_return = float(monthly_returns[code].mean() * 12)
        risk = float(monthly_returns[code].std(ddof=0) * np.sqrt(12))
        return [{"risk": risk, "return": expected_return, "weights": {code: 1.0}}]

    monthly_returns = df.pct_change().dropna()

    raw_expected_returns = monthly_returns.mean()
    # Simple shrinkage makes the frontier less sensitive to small-sample noise.
    expected_returns = shrink_frontier_expected_returns(raw_expected_returns)
    cov_matrix, _ = estimate_covariance(monthly_returns, covariance_method)

    columns = list(df.columns)
    bounds = get_frontier_weight_bounds(columns)
    initial_guess = get_frontier_initial_guess(columns)

    def clean_weights(raw_weights):
        """Normalize numerical roundoff; preserve small theoretical positions."""
        weights = pd.Series(raw_weights, index=columns, dtype=float).clip(lower=0.0)
        total = float(weights.sum())
        return weights / total if total > 0 else weights

    def portfolio_variance(w):
        weights = np.asarray(w, dtype=float)
        return np.dot(weights.T, np.dot(cov_matrix, weights))

    def frontier_initial_guesses(current_guess: np.ndarray) -> List[np.ndarray]:
        guesses = [
            current_guess,
            initial_guess,
            np.full(len(columns), 1.0 / len(columns), dtype=float),
        ]
        risk_free_index = columns.index("RiskFree") if "RiskFree" in columns else None
        for idx, code in enumerate(columns):
            one_hot = np.zeros(len(columns), dtype=float)
            one_hot[idx] = 1.0
            guesses.append(one_hot)
            if risk_free_index is not None and idx != risk_free_index:
                pair_guess = np.zeros(len(columns), dtype=float)
                pair_guess[idx] = 0.35
                pair_guess[risk_free_index] = 0.65
                guesses.append(pair_guess)

        deduped = []
        seen = set()
        for guess in guesses:
            key = tuple(np.round(guess, 8))
            if key in seen:
                continue
            seen.add(key)
            deduped.append(guess)
        return deduped

    mvp_constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1}
    mvp_result = minimize(
        portfolio_variance,
        initial_guess,
        method="SLSQP",
        bounds=bounds,
        constraints=mvp_constraints,
    )
    mvp_weights = mvp_result.x if mvp_result.success else initial_guess
    mvp_return = float(np.sum(expected_returns * mvp_weights))
    max_return_weights = get_max_return_weights(expected_returns, bounds)
    max_feasible_return = float(np.sum(expected_returns * max_return_weights))

    frontier_returns = np.linspace(mvp_return, max_feasible_return, 20)
    frontier_points = []
    current_guess = mvp_weights

    for target in frontier_returns:
        constraints = (
            {"type": "eq", "fun": lambda w: np.sum(expected_returns * w) - target},
            {"type": "eq", "fun": lambda w: np.sum(w) - 1},
        )
        best_result = None
        best_variance = None
        for guess in frontier_initial_guesses(current_guess):
            result = minimize(
                portfolio_variance,
                guess,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
            )
            if not result.success:
                continue
            variance = float(portfolio_variance(result.x))
            if best_result is None or variance < best_variance:
                best_result = result
                best_variance = variance

        if best_result is not None:
            current_guess = best_result.x
            cleaned_weights = clean_weights(best_result.x)
            optimized_weights = cleaned_weights.to_numpy(dtype=float)
            risk = np.sqrt(
                np.dot(optimized_weights.T, np.dot(cov_matrix, optimized_weights))
            ) * np.sqrt(12)
            ret = float(np.sum(expected_returns * cleaned_weights) * 12)
            frontier_points.append(
                {"risk": risk, "return": ret, "weights": cleaned_weights.to_dict()}
            )

    if not frontier_points:
        # A numerical failure must not erase an otherwise feasible allocation.
        # This is a deterministic feasible target, not a solved frontier point.
        if (
            not np.isfinite(expected_returns).all()
            or not np.isfinite(cov_matrix).all().all()
        ):
            raise ValueError(
                "Insufficient finite observations for frontier risk estimates"
            )
        weights = clean_weights(initial_guess)
        frontier_points.append(
            {
                "risk": float(
                    np.sqrt(max(0.0, portfolio_variance(weights))) * np.sqrt(12)
                ),
                "return": float(np.sum(expected_returns * weights) * 12),
                "weights": weights.to_dict(),
                "solver_fallback_used": True,
                "solver_status": "no_converged_frontier_point",
            }
        )
    return frontier_points


def _minimum_variance_weights(
    train_returns: pd.DataFrame, covariance_method: CovarianceMethod
) -> tuple[pd.Series, float]:
    covariance, intensity = estimate_covariance(train_returns, covariance_method)
    columns = list(train_returns.columns)
    bounds = get_frontier_weight_bounds(columns)
    initial_guess = get_frontier_initial_guess(columns)

    def variance(weights):
        return float(weights.T @ covariance.values @ weights)

    result = minimize(
        variance,
        initial_guess,
        method="SLSQP",
        bounds=bounds,
        constraints={"type": "eq", "fun": lambda weights: np.sum(weights) - 1},
    )
    weights = result.x if result.success else initial_guess
    return pd.Series(weights, index=columns, dtype=float), intensity


def _evaluate_covariance_segment(
    df_nav: pd.DataFrame,
    covariance_method: CovarianceMethod,
    min_train_months: int,
) -> dict:
    risky_nav = df_nav.drop(columns=["RiskFree"], errors="ignore")
    if len(risky_nav.columns) < 2 or len(risky_nav) <= min_train_months:
        return {"status": "insufficient_data", "observations": 0}

    returns = risky_nav.pct_change().dropna()
    realized_returns = []
    weight_drifts = []
    intensities = []
    previous_weights = None
    for split in range(min_train_months, len(risky_nav)):
        train_returns = risky_nav.iloc[:split].pct_change().dropna()
        if len(train_returns) < 2:
            continue
        weights, intensity = _minimum_variance_weights(train_returns, covariance_method)
        next_return = float((returns.iloc[split - 1] * weights).sum())
        realized_returns.append(next_return)
        intensities.append(intensity)
        if previous_weights is not None:
            weight_drifts.append(float((weights - previous_weights).abs().sum() / 2))
        previous_weights = weights

    series = pd.Series(realized_returns, dtype=float)
    if series.empty:
        return {"status": "insufficient_data", "observations": 0}
    annualized_return = (
        float((1 + series).prod() ** (12 / len(series)) - 1)
        if (series > -1).all()
        else -1.0
    )
    volatility = float(series.std(ddof=0) * np.sqrt(12))
    return {
        "status": "ok",
        "observations": int(len(series)),
        "annualized_return": annualized_return,
        "annualized_volatility": volatility,
        "sharpe": annualized_return / volatility if volatility > 1e-9 else 0.0,
        "max_drawdown": float(calculate_drawdown_from_returns(series)),
        "average_weight_drift": float(np.mean(weight_drifts)) if weight_drifts else 0.0,
        "weight_stability": float(
            max(0.0, 1.0 - (np.mean(weight_drifts) if weight_drifts else 0.0))
        ),
        "average_shrinkage_intensity": float(np.mean(intensities)),
    }


def evaluate_covariance_shrinkage_ablation(
    df_nav: pd.DataFrame,
    *,
    min_train_months: int = MIN_WALK_FORWARD_TRAIN_MONTHS,
) -> dict:
    """Compare covariance estimators without changing the production default."""
    midpoint = len(df_nav) // 2
    segments = {
        "full_sample": df_nav,
        "first_half": df_nav.iloc[:midpoint],
        "second_half": df_nav.iloc[midpoint:],
    }
    comparisons = {}
    qualifying_segments = 0
    strict_improvements = 0
    for segment_name, segment_nav in segments.items():
        fixed = _evaluate_covariance_segment(segment_nav, "fixed_20", min_train_months)
        candidate = _evaluate_covariance_segment(
            segment_nav, "ledoit_wolf", min_train_months
        )
        qualifies = (
            fixed["status"] == "ok"
            and candidate["status"] == "ok"
            and candidate["sharpe"] >= fixed["sharpe"] - 0.05
            and candidate["weight_stability"] >= fixed["weight_stability"] - 0.01
            and candidate["max_drawdown"] <= fixed["max_drawdown"] + 0.01
        )
        strict = (
            qualifies
            and candidate["sharpe"] > fixed["sharpe"] + 0.02
            and candidate["weight_stability"] > fixed["weight_stability"]
        )
        qualifying_segments += int(qualifies)
        strict_improvements += int(strict)
        comparisons[segment_name] = {
            "fixed_20": fixed,
            "ledoit_wolf": candidate,
            "candidate_qualifies": qualifies,
        }

    available_segments = sum(
        comparison["fixed_20"]["status"] == "ok"
        and comparison["ledoit_wolf"]["status"] == "ok"
        for comparison in comparisons.values()
    )
    candidate_ready = (
        available_segments == len(segments)
        and qualifying_segments == len(segments)
        and strict_improvements >= 2
    )
    return {
        "default_method": "fixed_20",
        "candidate_method": "ledoit_wolf",
        "promotion_status": "candidate" if candidate_ready else "retain_fixed",
        "auto_switched": False,
        "selection_rule": (
            "all_segments_non_inferior_and_at_least_two_strict_improvements"
        ),
        "segments": comparisons,
    }


def calculate_frontier_walk_forward_metrics(
    df_nav: pd.DataFrame,
    fund_fees: Dict[str, float],
    *,
    min_train_months: int = MIN_WALK_FORWARD_TRAIN_MONTHS,
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE,
    annual_risk_free_rate: float | None = None,
):
    full_frontier = calculate_efficient_frontier(df_nav, fund_fees)
    if not full_frontier:
        return []

    returns = df_nav.pct_change().fillna(0)
    metrics = [
        {"oos_returns": [], "oos_excess_returns": [], "weight_drifts": []}
        for _ in range(len(full_frontier))
    ]
    full_weights = [
        normalize_weights(point["weights"], list(df_nav.columns))
        for point in full_frontier
    ]
    monthly_risk_free_return = (
        (1 + annual_risk_free_rate) ** (1 / 12) - 1
        if annual_risk_free_rate is not None
        else 0.0
    )

    for split_end in range(min_train_months, len(df_nav)):
        train_df = df_nav.iloc[:split_end]
        train_frontier = calculate_efficient_frontier(train_df, fund_fees)
        if not train_frontier:
            continue

        next_returns = returns.iloc[split_end]
        for point_index, target_point in enumerate(full_frontier):
            if len(train_frontier) == 1 or len(full_frontier) == 1:
                train_index = min(point_index, len(train_frontier) - 1)
            else:
                quantile = point_index / (len(full_frontier) - 1)
                train_index = int(round(quantile * (len(train_frontier) - 1)))

            train_point = train_frontier[train_index]
            train_weights = normalize_weights(
                train_point["weights"], list(df_nav.columns)
            )
            target_weights = full_weights[point_index]

            drift = float((train_weights - target_weights).abs().sum() / 2)
            metrics[point_index]["weight_drifts"].append(drift)

            oos_return = float(
                (next_returns[train_weights.index] * train_weights).sum()
            )
            if annual_risk_free_rate is None and "RiskFree" in next_returns.index:
                period_risk_free_return = float(next_returns["RiskFree"])
            else:
                period_risk_free_return = monthly_risk_free_return
            metrics[point_index]["oos_returns"].append(oos_return)
            metrics[point_index]["oos_excess_returns"].append(
                oos_return - period_risk_free_return
            )

    summarized_metrics = []
    for point_index, point_metrics in enumerate(metrics):
        oos_returns = pd.Series(point_metrics["oos_returns"], dtype=float)
        observations = len(oos_returns)
        if observations == 0:
            summarized_metrics.append(
                {
                    "frontier_walk_forward_observations": 0,
                    "frontier_walk_forward_annualized_return": None,
                    "frontier_walk_forward_annualized_excess_return": None,
                    "frontier_walk_forward_volatility": None,
                    "frontier_walk_forward_sharpe": None,
                    "frontier_walk_forward_max_drawdown": None,
                    "frontier_walk_forward_cvar_loss": None,
                    "frontier_walk_forward_weight_stability": None,
                }
            )
            continue

        ann_return = (
            float((1 + oos_returns).prod() ** (12 / observations) - 1)
            if (1 + oos_returns).min() > 0
            else -1.0
        )
        volatility = float(oos_returns.std(ddof=0) * np.sqrt(12))
        annualized_excess_return = float(
            pd.Series(point_metrics["oos_excess_returns"], dtype=float).mean() * 12
        )
        sharpe = (
            float(annualized_excess_return / volatility) if volatility > 1e-9 else 0.0
        )
        max_drawdown = float(calculate_drawdown_from_returns(oos_returns))
        cvar_loss = float(calculate_cvar_loss(oos_returns, cvar_confidence))
        avg_drift = (
            float(np.mean(point_metrics["weight_drifts"]))
            if point_metrics["weight_drifts"]
            else 1.0
        )
        weight_stability = float(max(0.0, 1.0 - avg_drift))
        summarized_metrics.append(
            {
                "frontier_walk_forward_observations": observations,
                "frontier_walk_forward_annualized_return": ann_return,
                "frontier_walk_forward_annualized_excess_return": (
                    annualized_excess_return
                ),
                "frontier_walk_forward_volatility": volatility,
                "frontier_walk_forward_sharpe": sharpe,
                "frontier_walk_forward_max_drawdown": max_drawdown,
                "frontier_walk_forward_cvar_loss": cvar_loss,
                "frontier_walk_forward_weight_stability": weight_stability,
            }
        )

    return summarized_metrics
