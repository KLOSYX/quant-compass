from typing import Dict, List, Literal

import numpy as np
import pandas as pd
from scipy.optimize import linprog, minimize

from core.constants import (
    COVARIANCE_SHRINKAGE,
    DEFAULT_CVAR_CONFIDENCE,
    MIN_WALK_FORWARD_TRAIN_MONTHS,
)
from core.portfolio import (
    get_frontier_initial_guess,
    normalize_weights,
)
from core.risk import calculate_cvar_loss, calculate_drawdown_from_returns

CovarianceMethod = Literal["sample", "fixed_20", "ledoit_wolf"]


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
    if method == "sample":
        shrunk, intensity = empirical, 0.0
    elif method == "fixed_20":
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


def _solve_minimum_variance(covariance, means=None, minimum_return=None, initial=None):
    """Long-only convex QP, validated by a first-order global gap bound.

    For convex f, f(w)-f(w*) <= grad(f)(w) @ (w-s), where s minimizes
    the linearization over the same feasible polytope. Tolerances here
    are numerical tolerances, not investment preferences.
    """
    covariance = np.asarray(covariance, dtype=float)
    size = len(covariance)
    if size == 0 or not np.isfinite(covariance).all():
        return None
    scale = float(np.trace(covariance) / size)
    q = covariance / scale if scale > 0 else covariance.copy()
    q = (q + q.T) / 2
    if np.linalg.eigvalsh(q).min() < -1e-10:
        return None
    bounds = [(0.0, 1.0)] * size
    vector = None
    floor = None
    if minimum_return is not None:
        means = np.asarray(means, dtype=float)
        spread = float(np.ptp(means))
        if spread > 0:
            vector = (means - means.min()) / spread
            floor = float((minimum_return - means.min()) / spread)
            if floor > 1 + 1e-10:
                return None
            if floor >= 1 - 1e-12:
                # On the maximum-return face the return constraint is redundant.
                bounds = [(0.0, 1.0 if value >= 1 - 1e-12 else 0.0) for value in vector]
                vector = None
        elif minimum_return > float(means[0]) + 1e-12:
            return None
    linear_kwargs = dict(
        A_eq=np.ones((1, size)),
        b_eq=[1.0],
        bounds=bounds,
        A_ub=None if vector is None else -vector.reshape(1, -1),
        b_ub=None if vector is None else [-floor],
        method="highs",
    )
    feasible = linprog(np.zeros(size), **linear_kwargs)
    if not feasible.success:
        return None

    def is_feasible(weights):
        return (
            np.isfinite(weights).all()
            and abs(weights.sum() - 1) <= 1e-8
            and all(
                lower - 1e-9 <= value <= upper + 1e-9
                for value, (lower, upper) in zip(weights, bounds)
            )
            and (vector is None or weights @ vector >= floor - 1e-8)
        )

    start = np.asarray(initial) if initial is not None else feasible.x
    if not is_feasible(start):
        start = feasible.x
    constraints = [
        {"type": "eq", "fun": lambda w: w.sum() - 1, "jac": lambda w: np.ones(size)}
    ]
    if vector is not None:
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda w: w @ vector - floor,
                "jac": lambda w: vector,
            }
        )
    answer = minimize(
        lambda w: float(w @ q @ w),
        start,
        jac=lambda w: 2 * q @ w,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-14, "maxiter": 1000},
    )
    if not answer.success:
        return None
    weights = np.asarray(answer.x, dtype=float)
    if not is_feasible(weights):
        return None
    weights = np.maximum(weights, 0)
    weights /= weights.sum()
    if not is_feasible(weights):
        return None
    gradient = 2 * q @ weights
    certificate = linprog(gradient, **linear_kwargs)
    if not certificate.success:
        return None
    gap = max(0.0, float(gradient @ weights - certificate.fun))
    if gap > 1e-7:
        return None
    return weights, gap


def calculate_efficient_frontier(
    df, fund_fees, *, covariance_method: CovarianceMethod = "fixed_20"
):
    """Historical mean/variance frontier, not a forecast or a trading policy."""
    returns = (
        df.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan).dropna()
    )
    known_risk_free_only = list(df.columns) == ["RiskFree"] and len(returns) >= 1
    if (len(returns) < 2 and not known_risk_free_only) or not len(df.columns):
        raise ValueError("At least two complete return observations are required")
    means = returns.mean().to_numpy(dtype=float)
    covariance, _ = estimate_covariance(returns, covariance_method)
    matrix = covariance.to_numpy(dtype=float)
    columns = list(df.columns)
    initial = get_frontier_initial_guess(columns)

    def point(weights, fraction, gap=None):
        return {
            "risk": float(np.sqrt(max(0.0, weights @ matrix @ weights) * 12)),
            "return": float(weights @ means * 12),
            "weights": dict(zip(columns, weights.tolist())),
            "return_basis": "historical_arithmetic_mean",
            "forecast_available": False,
            "estimation_observations": len(returns),
            "frontier_fraction": float(fraction),
            "solver_optimality_gap": gap,
            "max_asset_weight": float(weights.max()),
        }

    minimum = _solve_minimum_variance(matrix, initial=initial)
    if minimum is None:
        fallback = point(initial, 0)
        fallback.update(
            solver_fallback_used=True, solver_status="no_verified_frontier_solution"
        )
        return [fallback]
    weights, gap = minimum
    minimum_return = float(weights @ means)
    maximum_return = float(means.max())
    points = [point(weights, 0, gap)]
    if maximum_return - minimum_return <= 1e-12:
        return points
    rejected_points = 0
    for fraction in np.linspace(0, 1, 20)[1:]:
        target = minimum_return + fraction * (maximum_return - minimum_return)
        solution = _solve_minimum_variance(matrix, means, target, weights)
        if solution is None:
            rejected_points += 1
            continue
        weights, gap = solution
        points.append(point(weights, fraction, gap))
    for item in points:
        item["rejected_frontier_points"] = rejected_points
    return points


def _minimum_variance_weights(
    train_returns: pd.DataFrame, covariance_method: CovarianceMethod
) -> tuple[pd.Series, float]:
    covariance, intensity = estimate_covariance(train_returns, covariance_method)
    columns = list(train_returns.columns)
    initial_guess = get_frontier_initial_guess(columns)
    solution = _solve_minimum_variance(covariance.to_numpy(), initial=initial_guess)
    if solution is None:
        raise ValueError("Covariance diagnostic solver did not pass optimality checks")
    return pd.Series(solution[0], index=columns, dtype=float), intensity


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
        try:
            weights, intensity = _minimum_variance_weights(
                train_returns, covariance_method
            )
        except ValueError:
            return {"status": "solver_failed", "observations": len(realized_returns)}
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
    for segment_name, segment_nav in segments.items():
        comparisons[segment_name] = {
            method: _evaluate_covariance_segment(segment_nav, method, min_train_months)
            for method in ("fixed_20", "ledoit_wolf")
        }
    return {
        "default_method": "fixed_20",
        "candidate_method": "ledoit_wolf",
        "promotion_status": "descriptive_only",
        "auto_switched": False,
        "selection_rule": "no_automatic_promotion",
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
    evaluation_dates = []
    previous_weights = [None] * len(full_frontier)
    monthly_risk_free_return = (
        (1 + annual_risk_free_rate) ** (1 / 12) - 1
        if annual_risk_free_rate is not None
        else 0.0
    )

    for split_end in range(min_train_months, len(df_nav)):
        train_df = df_nav.iloc[:split_end]
        train_frontier = calculate_efficient_frontier(train_df, fund_fees)
        if not train_frontier or any(
            p.get("solver_fallback_used") for p in train_frontier
        ):
            continue

        evaluation_dates.append(df_nav.index[split_end].strftime("%Y-%m-%d"))
        next_returns = returns.iloc[split_end]
        for point_index, target_point in enumerate(full_frontier):
            if len(train_frontier) == 1 or len(full_frontier) == 1:
                train_index = min(point_index, len(train_frontier) - 1)
            else:
                quantile = target_point.get(
                    "frontier_fraction", point_index / (len(full_frontier) - 1)
                )
                train_index = min(
                    range(len(train_frontier)),
                    key=lambda i: abs(
                        train_frontier[i].get(
                            "frontier_fraction", i / (len(train_frontier) - 1)
                        )
                        - quantile
                    ),
                )

            train_point = train_frontier[train_index]
            train_weights = normalize_weights(
                train_point["weights"], list(df_nav.columns)
            )
            prior = previous_weights[point_index]
            if prior is not None:
                drift = float((train_weights - prior).abs().sum() / 2)
                metrics[point_index]["weight_drifts"].append(drift)
            previous_weights[point_index] = train_weights

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

    evaluation_basis = {
        "frontier_walk_forward_basis": "monthly_reestimated_frontier_fraction",
        "frontier_walk_forward_start_date": evaluation_dates[0]
        if evaluation_dates
        else None,
        "frontier_walk_forward_end_date": evaluation_dates[-1]
        if evaluation_dates
        else None,
    }
    summarized_metrics = []
    for point_index, point_metrics in enumerate(metrics):
        oos_returns = pd.Series(point_metrics["oos_returns"], dtype=float)
        observations = len(oos_returns)
        if observations == 0:
            summarized_metrics.append(
                {
                    **evaluation_basis,
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
                **evaluation_basis,
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
