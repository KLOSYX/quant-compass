from typing import Dict, List

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.constants import (
    COVARIANCE_SHRINKAGE,
    MIN_WALK_FORWARD_TRAIN_MONTHS,
    MIN_WEIGHT_THRESHOLD,
)
from core.portfolio import (
    get_effective_single_weight_cap,  # noqa: F401
    get_frontier_initial_guess,
    get_frontier_weight_bounds,
    get_max_return_weights,
    normalize_risky_weights,
    normalize_weights,
    shrink_frontier_expected_returns,
)
from core.risk import calculate_drawdown_from_returns


def append_frontier_stability_warnings(
    warnings: List[str], df_nav: pd.DataFrame, fund_fees: Dict[str, float]
):
    risky_df = df_nav.drop(columns=["RiskFree"], errors="ignore")
    if len(risky_df.columns) == 0:
        return

    warnings.append(
        "有效前沿权重仍基于全样本静态估计，不属于样本外结果；实际投入请优先参考 Kelly/VA 回测而不是理论前沿本身。"
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


def calculate_efficient_frontier(df, fund_fees):
    if list(df.columns) == ["RiskFree"]:
        monthly_returns = df.pct_change().fillna(0)
        expected_return = monthly_returns["RiskFree"].mean()
        return [
            {"risk": 0, "return": expected_return * 12, "weights": {"RiskFree": 1.0}}
        ]

    if len(df.columns) == 1:
        monthly_returns = df.pct_change().fillna(0)
        code = df.columns[0]
        expected_return = float(monthly_returns[code].mean() * 12)
        risk = float(monthly_returns[code].std(ddof=0) * np.sqrt(12))
        return [{"risk": risk, "return": expected_return, "weights": {code: 1.0}}]

    monthly_returns = df.pct_change().fillna(0)

    raw_expected_returns = monthly_returns.mean()
    cov_matrix = monthly_returns.cov()
    if "RiskFree" in cov_matrix.columns:
        cov_matrix["RiskFree"] = 0
        cov_matrix.loc["RiskFree"] = 0

    # Simple shrinkage makes the frontier less sensitive to small-sample noise.
    expected_returns = shrink_frontier_expected_returns(raw_expected_returns)
    diagonal_target = pd.DataFrame(
        np.diag(np.diag(cov_matrix.values)),
        index=cov_matrix.index,
        columns=cov_matrix.columns,
    )
    cov_matrix = (
        1 - COVARIANCE_SHRINKAGE
    ) * cov_matrix + COVARIANCE_SHRINKAGE * diagonal_target

    columns = list(df.columns)
    bounds = get_frontier_weight_bounds(columns)
    initial_guess = get_frontier_initial_guess(columns)

    def clean_weights(raw_weights):
        """Zero out tiny weights and renormalize to keep allocations executable."""
        weights = pd.Series(raw_weights, index=columns, dtype=float).clip(lower=0.0)
        total = float(weights.sum())
        if total <= 0:
            return pd.Series(raw_weights, index=columns, dtype=float)

        weights /= total

        cleaned = weights.copy()
        cleaned[(cleaned > 0) & (cleaned < MIN_WEIGHT_THRESHOLD)] = 0.0
        cleaned_total = float(cleaned.sum())
        if cleaned_total <= 0:
            return weights

        cleaned /= cleaned_total
        upper_bounds = pd.Series(
            [upper for _, upper in bounds], index=columns, dtype=float
        )
        if (cleaned > upper_bounds + 1e-9).any():
            return weights
        return cleaned

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
            # Use raw optimizer weights for risk to keep the frontier smooth;
            # clean_weights only for the displayed/rebalancing allocation.
            optimized_weights = np.asarray(best_result.x, dtype=float)
            risk = np.sqrt(
                np.dot(optimized_weights.T, np.dot(cov_matrix, optimized_weights))
            ) * np.sqrt(12)
            cleaned_weights = clean_weights(optimized_weights)
            ret = float(np.sum(expected_returns * cleaned_weights) * 12)
            frontier_points.append(
                {"risk": risk, "return": ret, "weights": cleaned_weights.to_dict()}
            )

    return frontier_points


def calculate_frontier_walk_forward_metrics(
    df_nav: pd.DataFrame,
    fund_fees: Dict[str, float],
    *,
    min_train_months: int = MIN_WALK_FORWARD_TRAIN_MONTHS,
):
    full_frontier = calculate_efficient_frontier(df_nav, fund_fees)
    if not full_frontier:
        return []

    returns = df_nav.pct_change().fillna(0)
    metrics = [
        {"oos_returns": [], "weight_drifts": []} for _ in range(len(full_frontier))
    ]
    full_risky_weights = [
        normalize_risky_weights(point["weights"], list(df_nav.columns))
        for point in full_frontier
    ]

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
            train_risky_weights = normalize_risky_weights(
                train_point["weights"], list(df_nav.columns)
            )
            target_risky_weights = full_risky_weights[point_index]

            drift = float((train_risky_weights - target_risky_weights).abs().sum() / 2)
            metrics[point_index]["weight_drifts"].append(drift)

            if float(train_risky_weights.sum()) > 0:
                oos_return = float(
                    (
                        next_returns[train_risky_weights.index] * train_risky_weights
                    ).sum()
                )
            else:
                oos_return = 0.0
            metrics[point_index]["oos_returns"].append(oos_return)

    summarized_metrics = []
    for point_index, point_metrics in enumerate(metrics):
        oos_returns = pd.Series(point_metrics["oos_returns"], dtype=float)
        observations = len(oos_returns)
        if observations == 0:
            summarized_metrics.append(
                {
                    "walk_forward_observations": 0,
                    "walk_forward_annualized_return": None,
                    "walk_forward_volatility": None,
                    "walk_forward_sharpe": None,
                    "walk_forward_max_drawdown": None,
                    "walk_forward_weight_stability": None,
                    "robust_score": None,
                }
            )
            continue

        ann_return = (
            float((1 + oos_returns).prod() ** (12 / observations) - 1)
            if (1 + oos_returns).min() > 0
            else -1.0
        )
        volatility = float(oos_returns.std(ddof=0) * np.sqrt(12))
        sharpe = float(ann_return / volatility) if volatility > 1e-9 else 0.0
        max_drawdown = float(calculate_drawdown_from_returns(oos_returns))
        avg_drift = (
            float(np.mean(point_metrics["weight_drifts"]))
            if point_metrics["weight_drifts"]
            else 1.0
        )
        weight_stability = float(max(0.0, 1.0 - avg_drift))
        robust_score = float(sharpe - max_drawdown)

        summarized_metrics.append(
            {
                "walk_forward_observations": observations,
                "walk_forward_annualized_return": ann_return,
                "walk_forward_volatility": volatility,
                "walk_forward_sharpe": sharpe,
                "walk_forward_max_drawdown": max_drawdown,
                "walk_forward_weight_stability": weight_stability,
                "robust_score": robust_score,
            }
        )

    return summarized_metrics
