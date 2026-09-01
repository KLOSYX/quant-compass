from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from core.classification import (
    ASSET_CATEGORIES,
    calculate_exposure_metrics,
    normalize_asset_categories,
)
from core.constants import (
    DEFAULT_RISK_HORIZON_DAYS,
    MIN_COVARIANCE_ABLATION_SEGMENT_MONTHS,
    MIN_CVAR_TAIL_OBSERVATIONS,
    MIN_SORTINO_DOWNSIDE_OBSERVATIONS,
    MIN_WALK_FORWARD_EVALUATION_MONTHS,
    MIN_WALK_FORWARD_TRAIN_MONTHS,
)
from core.execution import execute_monthly_plan
from core.execution_optimizer import (
    build_execution_covariance,
    strategic_fund_codes,
)
from core.frontier import calculate_efficient_frontier
from core.portfolio import decompose_selected_weights
from core.reference import build_reference_basket
from core.risk import calculate_cvar_loss, calculate_drawdown_from_returns
from core.strategy import calculate_target_ratio_optimized


WALK_FORWARD_STRATEGIES = (
    "equal_weight_dca",
    "fixed_weight_dca",
    "no_kelly",
    "full_strategy",
)
KELLY_WINDOW_MONTHS = (24, 36, 48, 60)


def _select_stable_kelly_platform(window_metrics: dict[int, dict]) -> dict:
    """Choose the central long-window result, rather than the highest score."""
    eligible = {
        window: metrics
        for window, metrics in window_metrics.items()
        if metrics["observations"] > 0 and window >= 36
    }
    if len(eligible) < 2:
        return {
            "status": "insufficient_data",
            "selected_window_months": None,
            "selection_rule": "median_long_window_platform",
        }

    fields = ("annualized_return", "sharpe", "max_drawdown")
    medians = {
        field: float(np.median([metrics[field] for metrics in eligible.values()]))
        for field in fields
    }
    scales = {
        field: max(
            max(metrics[field] for metrics in eligible.values())
            - min(metrics[field] for metrics in eligible.values()),
            1e-9,
        )
        for field in fields
    }
    distances = {
        window: sum(
            abs(metrics[field] - medians[field]) / scales[field] for field in fields
        )
        for window, metrics in eligible.items()
    }
    best_distance = min(distances.values())
    selected = (
        36
        if 36 in distances and distances[36] <= best_distance + 0.25
        else min(distances, key=lambda window: (distances[window], window))
    )
    return {
        "status": "selected",
        "selected_window_months": selected,
        "selection_rule": "median_long_window_platform",
        "selection_note": (
            "选择长期窗口中最接近收益、Sharpe 与回撤中位平台的窗口；"
            "36 个月处于同一平台时保留默认值，不追逐单次最高分。"
        ),
    }


def _summarize_ablation_slice(
    returns: list[float],
    weight_drifts: list[float],
    risk_free_rate: float,
    min_observations: int = MIN_COVARIANCE_ABLATION_SEGMENT_MONTHS,
) -> dict:
    series = pd.Series(returns, dtype=float)
    if len(series) < min_observations:
        return {
            "status": "insufficient_data",
            "observations": int(len(series)),
            "minimum_observations": int(min_observations),
        }
    annualized_return = (
        float((1 + series).prod() ** (12 / len(series)) - 1)
        if (series > -1).all()
        else -1.0
    )
    volatility = float(series.std(ddof=0) * np.sqrt(12))
    average_drift = float(np.mean(weight_drifts)) if weight_drifts else 0.0
    return {
        "status": "ok",
        "observations": int(len(series)),
        "annualized_return": annualized_return,
        "sharpe": (
            (annualized_return - risk_free_rate) / volatility
            if volatility > 1e-9
            else 0.0
        ),
        "max_drawdown": float(calculate_drawdown_from_returns(series)),
        "weight_stability": float(max(0.0, 1.0 - average_drift)),
    }


def _compare_complete_covariance_states(
    fixed_state: "_StrategyState",
    candidate_state: "_StrategyState",
    risk_free_rate: float,
) -> dict:
    observations = min(len(fixed_state.returns), len(candidate_state.returns))
    midpoint = observations // 2
    slices = {
        "full_sample": (0, observations),
        "first_half": (0, midpoint),
        "second_half": (midpoint, observations),
    }
    segments = {}
    qualifying_segments = 0
    strict_improvements = 0
    for name, (start, end) in slices.items():
        fixed = _summarize_ablation_slice(
            fixed_state.returns[start:end],
            fixed_state.weight_drifts[start : max(start, end - 1)],
            risk_free_rate,
        )
        candidate = _summarize_ablation_slice(
            candidate_state.returns[start:end],
            candidate_state.weight_drifts[start : max(start, end - 1)],
            risk_free_rate,
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
        segments[name] = {
            "fixed_20": fixed,
            "ledoit_wolf": candidate,
            "candidate_qualifies": qualifies,
        }

    available_segments = sum(
        item["fixed_20"]["status"] == "ok" and item["ledoit_wolf"]["status"] == "ok"
        for item in segments.values()
    )
    candidate_ready = (
        available_segments == len(segments)
        and qualifying_segments == len(segments)
        and strict_improvements >= 2
    )
    return {
        "evaluation_scope": "complete_executable_strategy",
        "default_method": "fixed_20",
        "candidate_method": "ledoit_wolf",
        "promotion_status": "candidate" if candidate_ready else "retain_fixed",
        "auto_switched": False,
        "segments": segments,
    }


@dataclass
class _StrategyState:
    shares: pd.Series
    risk_free_shares: float
    cash: float
    units: float
    initial_wealth: float
    external_contributions: float = 0.0
    returns: list[float] = field(default_factory=list)
    unit_nav_history: dict = field(default_factory=dict)
    wealth_history: dict = field(default_factory=dict)
    audit: list[dict] = field(default_factory=list)
    cash_exposures: list[float] = field(default_factory=list)
    execution_deviations: list[float] = field(default_factory=list)
    weight_drifts: list[float] = field(default_factory=list)
    actual_weight_drifts: list[float] = field(default_factory=list)
    frontier_change_transmissions: list[float] = field(default_factory=list)
    category_exposures: list[dict[str, float]] = field(default_factory=list)
    total_trade_value: float = 0.0
    total_fee_cost: float = 0.0
    total_unfilled_gap: float = 0.0
    sell_count: int = 0
    previous_weights: pd.Series | None = None
    previous_actual_weights: pd.Series | None = None
    purchase_lots: list[tuple[int, float]] = field(default_factory=list)
    total_unspent_execution_budget: float = 0.0
    total_substitute_purchases: float = 0.0
    tracking_errors_before: list[float] = field(default_factory=list)
    tracking_errors_after: list[float] = field(default_factory=list)
    allocation_fallback_count: int = 0


def _select_train_point(frontier: list[dict], risk_free_rate: float) -> dict:
    """Apply a fixed, training-only maximum excess-Sharpe selection rule."""

    def rank(point):
        risk = float(point.get("risk", 0.0))
        excess_return = float(point.get("return", 0.0)) - risk_free_rate
        sharpe = (
            excess_return / risk if risk > 1e-9 else (1e6 if excess_return > 0 else 0)
        )
        return sharpe, -risk

    return max(frontier, key=rank)


def _initialize_state(
    df_nav: pd.DataFrame,
    signal_position: int,
    risky_codes: list[str],
    initial_holdings: Mapping[str, float],
    initial_cash: float,
) -> _StrategyState:
    signal_nav = df_nav.iloc[signal_position]
    shares = pd.Series(0.0, index=risky_codes, dtype=float)
    for code in risky_codes:
        holding = float(initial_holdings.get(code, 0.0) or 0.0)
        if holding > 0:
            shares[code] = holding / float(signal_nav[code])
    risk_free_holding = float(initial_holdings.get("RiskFree", 0.0) or 0.0)
    risk_free_shares = (
        risk_free_holding / float(signal_nav["RiskFree"])
        if "RiskFree" in df_nav.columns and risk_free_holding > 0
        else 0.0
    )
    initial_wealth = float(
        sum(float(value or 0.0) for value in initial_holdings.values())
    ) + float(initial_cash or 0.0)
    state = _StrategyState(
        shares=shares,
        risk_free_shares=risk_free_shares,
        cash=float(initial_cash or 0.0),
        units=initial_wealth,
        initial_wealth=initial_wealth,
    )
    initial_invested = sum(
        float(initial_holdings.get(code, 0.0) or 0.0)
        for code in [*risky_codes, "RiskFree"]
    )
    if initial_invested > 0:
        state.purchase_lots.append((0, initial_invested))
    return state


def _summarize_state(
    state: _StrategyState, risk_free_rate: float, cvar_confidence: float
) -> dict:
    returns = pd.Series(state.returns, dtype=float)
    observations = len(returns)
    downside_observations = 0
    cvar_tail_observations = 0
    if observations:
        annualized_return = (
            float((1 + returns).prod() ** (12 / observations) - 1)
            if (1 + returns).min() > 0
            else -1.0
        )
        volatility = float(returns.std(ddof=0) * np.sqrt(12))
        sharpe = (
            float((annualized_return - risk_free_rate) / volatility)
            if volatility > 1e-9
            else 0.0
        )
        monthly_risk_free = (1 + risk_free_rate) ** (1 / 12) - 1
        downside = np.minimum(returns - monthly_risk_free, 0.0)
        downside_observations = int((returns < monthly_risk_free).sum())
        downside_deviation = float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(12))
        sortino = (
            float((annualized_return - risk_free_rate) / downside_deviation)
            if downside_deviation > 1e-9
            else None
        )
        worst_month = float(returns.min())
        cvar_tail_observations = max(
            1, int(np.ceil(observations * (1 - cvar_confidence)))
        )
        cvar_loss = float(calculate_cvar_loss(returns, cvar_confidence))
        average_monthly_log_return = (
            float(np.log1p(returns).mean()) if (returns > -1).all() else None
        )
        max_drawdown = float(calculate_drawdown_from_returns(returns))
    else:
        annualized_return = 0.0
        volatility = 0.0
        sharpe = 0.0
        sortino = None
        worst_month = 0.0
        cvar_loss = 0.0
        average_monthly_log_return = None
        max_drawdown = 0.0
    final_wealth = (
        float(next(reversed(state.wealth_history.values())))
        if state.wealth_history
        else state.initial_wealth
    )
    average_wealth = (
        float(np.mean(list(state.wealth_history.values())))
        if state.wealth_history
        else max(state.initial_wealth, 1.0)
    )
    average_category_exposures = {
        category: float(
            np.mean(
                [exposures.get(category, 0.0) for exposures in state.category_exposures]
            )
        )
        for category in ASSET_CATEGORIES
    }
    target_change_months = len(state.weight_drifts)
    transmitted_months = len(state.frontier_change_transmissions)
    evidence_reasons = []
    if observations < MIN_WALK_FORWARD_EVALUATION_MONTHS:
        evidence_reasons.append("short_evaluation_window")
    if cvar_tail_observations < MIN_CVAR_TAIL_OBSERVATIONS:
        evidence_reasons.append("insufficient_cvar_tail")
    if downside_observations < MIN_SORTINO_DOWNSIDE_OBSERVATIONS:
        evidence_reasons.append("insufficient_sortino_downside_months")
    return {
        "observations": observations,
        "metric_basis": "unit_nav_after_external_cash_flows",
        "external_cash_flow_timing": "start_of_period_unit_issuance",
        "annualized_return": annualized_return,
        "average_monthly_log_return": average_monthly_log_return,
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "sortino_downside_observations": downside_observations,
        "sortino_confidence_status": (
            "adequate"
            if downside_observations >= MIN_SORTINO_DOWNSIDE_OBSERVATIONS
            else "low"
        ),
        "worst_month": worst_month,
        "cvar_loss": cvar_loss,
        "cvar_confidence": cvar_confidence,
        "cvar_tail_observations": cvar_tail_observations,
        "cvar_confidence_status": (
            "adequate"
            if cvar_tail_observations >= MIN_CVAR_TAIL_OBSERVATIONS
            else "low"
        ),
        "evidence_quality": {
            "status": "low" if evidence_reasons else "adequate",
            "evaluation_months": observations,
            "minimum_evaluation_months": MIN_WALK_FORWARD_EVALUATION_MONTHS,
            "reasons": evidence_reasons,
        },
        "max_drawdown": max_drawdown,
        "final_wealth": final_wealth,
        "total_external_capital": state.initial_wealth + state.external_contributions,
        "total_turnover": state.total_trade_value / max(average_wealth, 1.0),
        "total_transaction_fees": state.total_fee_cost,
        "average_cash_exposure": float(np.mean(state.cash_exposures))
        if state.cash_exposures
        else 0.0,
        "max_cash_exposure": max(state.cash_exposures, default=0.0),
        "total_unfilled_target_gap": state.total_unfilled_gap,
        "average_execution_deviation": float(np.mean(state.execution_deviations))
        if state.execution_deviations
        else 0.0,
        "max_execution_deviation": max(state.execution_deviations, default=0.0),
        "average_weight_drift": float(np.mean(state.weight_drifts))
        if state.weight_drifts
        else 0.0,
        "average_frontier_target_weight_change": (
            float(np.mean(state.weight_drifts)) if state.weight_drifts else 0.0
        ),
        "average_actual_basket_weight_change": (
            float(np.mean(state.actual_weight_drifts))
            if state.actual_weight_drifts
            else 0.0
        ),
        "average_frontier_change_transmission": (
            float(np.mean(state.frontier_change_transmissions))
            if state.frontier_change_transmissions
            else 0.0
        ),
        "months_with_frontier_target_change": sum(
            change > 1e-9 for change in state.weight_drifts
        ),
        "months_with_executed_frontier_change": sum(
            change > 1e-9 for change in state.actual_weight_drifts
        ),
        "frontier_change_observations": target_change_months,
        "frontier_transmission_observations": transmitted_months,
        "average_asset_category_exposures": average_category_exposures,
        "sell_count": state.sell_count,
        "total_unspent_execution_budget": state.total_unspent_execution_budget,
        "total_substitute_purchases": state.total_substitute_purchases,
        "average_tracking_error_before": (
            float(np.mean(state.tracking_errors_before))
            if state.tracking_errors_before
            else None
        ),
        "average_tracking_error_after": (
            float(np.mean(state.tracking_errors_after))
            if state.tracking_errors_after
            else None
        ),
        "allocation_fallback_count": state.allocation_fallback_count,
        "average_holding_period_months": (
            sum(
                (observations - month) * amount for month, amount in state.purchase_lots
            )
            / sum(amount for _, amount in state.purchase_lots)
            if state.purchase_lots
            else None
        ),
        "unit_nav_history": state.unit_nav_history,
        "wealth_history": state.wealth_history,
        "data_access_audit": state.audit,
    }


def evaluate_executable_walk_forward(
    df_nav: pd.DataFrame,
    fund_fees: Mapping[str, float],
    *,
    monthly_investment: float,
    initial_holdings: Mapping[str, float] | None = None,
    initial_cash: float = 0.0,
    buy_fees: Mapping[str, float] | None = None,
    sell_fees: Mapping[str, float] | None = None,
    fund_investment_limits: Mapping[str, object] | None = None,
    min_train_months: int = MIN_WALK_FORWARD_TRAIN_MONTHS,
    min_weight: float = 0.3,
    max_weight: float = 0.8,
    kelly_fraction: float = 0.5,
    estimation_window: int = 36,
    risk_free_rate: float = 0.0,
    minimum_cash_reserve: float = 0.0,
    enable_cvar_constraint: bool = True,
    cvar_confidence: float = 0.95,
    cvar_limit: float = 0.08,
    enable_drawdown_constraint: bool = True,
    max_drawdown_limit: float = 0.2,
    daily_nav: pd.DataFrame | None = None,
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS,
    include_covariance_ablation: bool = False,
    asset_categories: Mapping[str, str] | None = None,
    substitute_for: Mapping[str, str] | None = None,
    execution_allocation_method: str = "proportional_gap",
    planned_purchase_days: int | None = None,
) -> dict:
    """Evaluate executable strategies using only information available at each split."""
    if monthly_investment < 0:
        raise ValueError("monthly_investment must be non-negative")
    if initial_cash < 0:
        raise ValueError("initial_cash must be non-negative")
    initial_holdings = initial_holdings or {}
    unknown_holdings = sorted(
        code
        for code, value in initial_holdings.items()
        if code not in df_nav.columns and float(value or 0.0) > 0
    )
    if unknown_holdings:
        raise ValueError(
            "initial_holdings contain assets outside the walk-forward universe: "
            + ", ".join(unknown_holdings)
        )
    negative_holdings = sorted(
        code for code, value in initial_holdings.items() if float(value or 0.0) < 0
    )
    if negative_holdings:
        raise ValueError(
            "initial_holdings must be non-negative: " + ", ".join(negative_holdings)
        )
    if len(df_nav) <= min_train_months:
        return {
            "status": "insufficient_data",
            "minimum_training_months": min_train_months,
            "available_months": max(0, len(df_nav) - 1),
            "strategies": {},
        }
    buy_fees = buy_fees or {}
    sell_fees = sell_fees or {}
    risky_codes = [code for code in df_nav.columns if code != "RiskFree"]
    strategic_codes = strategic_fund_codes(risky_codes, substitute_for)
    if not risky_codes:
        return {
            "status": "not_applicable",
            "minimum_training_months": min_train_months,
            "available_months": len(df_nav) - 1,
            "strategies": {},
        }
    normalized_asset_categories = normalize_asset_categories(
        list(df_nav.columns), asset_categories
    )
    first_signal_position = min_train_months - 1
    strategy_states = {
        name: _initialize_state(
            df_nav,
            first_signal_position,
            risky_codes,
            initial_holdings,
            initial_cash,
        )
        for name in WALK_FORWARD_STRATEGIES
    }
    comparison_signal_position = max(first_signal_position, max(KELLY_WINDOW_MONTHS))
    window_state_names = (
        {f"full_strategy_{window}m": window for window in KELLY_WINDOW_MONTHS}
        if comparison_signal_position < len(df_nav) - 1
        else {}
    )
    states = {
        **strategy_states,
        **{
            name: _initialize_state(
                df_nav,
                comparison_signal_position,
                risky_codes,
                initial_holdings,
                initial_cash,
            )
            for name in window_state_names
        },
    }
    if include_covariance_ablation:
        states["full_strategy_ledoit_wolf"] = _initialize_state(
            df_nav,
            first_signal_position,
            risky_codes,
            initial_holdings,
            initial_cash,
        )
    allocation_baseline_name = None
    if execution_allocation_method == "constrained_tracking":
        allocation_baseline_name = "full_strategy_proportional_gap"
        states[allocation_baseline_name] = _initialize_state(
            df_nav,
            first_signal_position,
            risky_codes,
            initial_holdings,
            initial_cash,
        )
    if not strategic_codes:
        raise ValueError("at least one strategic fund is required")
    equal_weights = {code: 1.0 / len(strategic_codes) for code in strategic_codes}
    fixed_weights = None

    for realized_position in range(min_train_months, len(df_nav)):
        signal_position = realized_position - 1
        train_df = df_nav.iloc[: signal_position + 1]
        signal_date = df_nav.index[signal_position]
        realized_date = df_nav.index[realized_position]
        frontier_columns = [
            *strategic_codes,
            *(["RiskFree"] if "RiskFree" in train_df.columns else []),
        ]
        frontier_train_df = train_df[frontier_columns]
        frontier = calculate_efficient_frontier(frontier_train_df, dict(fund_fees))
        if not frontier:
            continue
        dynamic_weights = _select_train_point(frontier, risk_free_rate)["weights"]
        candidate_weights = None
        if include_covariance_ablation:
            candidate_frontier = calculate_efficient_frontier(
                frontier_train_df,
                dict(fund_fees),
                covariance_method="ledoit_wolf",
            )
            if candidate_frontier:
                candidate_weights = _select_train_point(
                    candidate_frontier, risk_free_rate
                )["weights"]
        if fixed_weights is None:
            fixed_weights = dict(dynamic_weights)

        strategy_weights = {
            "equal_weight_dca": equal_weights,
            "fixed_weight_dca": fixed_weights,
            "no_kelly": dynamic_weights,
            "full_strategy": dynamic_weights,
            **(
                {name: dynamic_weights for name in window_state_names}
                if signal_position >= comparison_signal_position
                else {}
            ),
            **(
                {"full_strategy_ledoit_wolf": candidate_weights}
                if candidate_weights is not None
                else {}
            ),
            **(
                {allocation_baseline_name: dynamic_weights}
                if allocation_baseline_name is not None
                else {}
            ),
        }
        monthly_execution_snapshots: dict[str, dict] = {}

        for strategy_name, weights in strategy_weights.items():
            state = states[strategy_name]
            signal_nav = df_nav.iloc[signal_position]
            realized_nav = df_nav.iloc[realized_position]
            current_assets = state.shares * signal_nav[state.shares.index]
            current_risk_free = (
                state.risk_free_shares * float(signal_nav["RiskFree"])
                if "RiskFree" in df_nav.columns
                else 0.0
            )
            wealth_before_flow = float(
                current_assets.sum() + current_risk_free + state.cash
            )
            unit_nav_before = (
                wealth_before_flow / state.units if state.units > 0 else 1.0
            )
            if monthly_investment > 0:
                state.units += monthly_investment / max(unit_nav_before, 1e-12)
                state.external_contributions += monthly_investment
            total_wealth = wealth_before_flow + monthly_investment

            selected = decompose_selected_weights(weights, list(df_nav.columns))
            risky_weights = selected["risky_weights"]
            base_risky_ratio = float(selected["base_risky_ratio"])
            base_risk_free_ratio = float(selected["base_risk_free_ratio"])
            cash_cap = (
                float(
                    np.clip(
                        (total_wealth - minimum_cash_reserve) / total_wealth,
                        0.0,
                        1.0,
                    )
                )
                if total_wealth > 0
                else 0.0
            )
            if (
                strategy_name
                in {
                    "full_strategy",
                    "full_strategy_ledoit_wolf",
                    "full_strategy_proportional_gap",
                }
                or strategy_name in window_state_names
            ):
                if float(risky_weights.sum()) <= 1e-12:
                    reference_nav = pd.Series(1.0, index=train_df.index)
                else:
                    reference_nav = build_reference_basket(
                        train_df[risky_weights.index], risky_weights
                    )
                daily_reference_nav = (
                    build_reference_basket(
                        daily_nav.loc[:signal_date, risky_weights.index], risky_weights
                    )
                    if daily_nav is not None
                    and float(risky_weights.sum()) > 1e-12
                    and set(risky_weights.index).issubset(daily_nav.columns)
                    else None
                )
                tactical_ratio, _, optimizer_info = calculate_target_ratio_optimized(
                    reference_portfolio_nav=reference_nav,
                    timestamp=signal_date,
                    min_weight=min_weight,
                    max_weight=max_weight,
                    kelly_fraction=kelly_fraction,
                    estimation_window=window_state_names.get(
                        strategy_name, estimation_window
                    ),
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
            elif strategy_name == "no_kelly":
                tactical_ratio = min(max_weight, cash_cap)
                optimizer_info = {}
            else:
                tactical_ratio = cash_cap
                optimizer_info = {}
            three_bucket_cap = max(0.0, cash_cap - base_risk_free_ratio)
            target_risky_ratio = float(
                np.clip(
                    min(base_risky_ratio * tactical_ratio, three_bucket_cap),
                    0.0,
                    1.0,
                )
            )
            target_risky_value = total_wealth * target_risky_ratio
            targets = risky_weights * target_risky_value
            non_risky_target = max(0.0, total_wealth - target_risky_value)
            target_has_risk_free = (
                "RiskFree" in df_nav.columns and base_risk_free_ratio > 0
            )
            target_risk_free = (
                total_wealth * base_risk_free_ratio if target_has_risk_free else 0.0
            )
            target_cash = max(0.0, non_risky_target - target_risk_free)
            execution = execute_monthly_plan(
                fund_codes=risky_codes,
                current_holdings=current_assets.to_dict(),
                target_holdings=targets.to_dict(),
                target_weights=risky_weights.to_dict(),
                current_cash=state.cash,
                monthly_budget=monthly_investment,
                buy_fees=buy_fees,
                sell_fees=sell_fees,
                investment_limits=fund_investment_limits,
                timestamp=signal_date,
                minimum_cash_reserve=minimum_cash_reserve,
                current_risk_free=current_risk_free,
                target_risk_free=target_risk_free,
                target_cash=target_cash,
                # Replay uses the same no-automatic-sale household policy.
                can_manage_risk_free=False,
                target_has_risk_free=target_has_risk_free,
                allocation_method=(
                    "proportional_gap"
                    if strategy_name == allocation_baseline_name
                    else execution_allocation_method
                ),
                execution_covariance=build_execution_covariance(
                    train_df,
                    risky_codes,
                    estimation_window=estimation_window,
                    covariance_method=(
                        "ledoit_wolf"
                        if strategy_name == "full_strategy_ledoit_wolf"
                        else "fixed_20"
                    ),
                ),
                substitute_for=substitute_for,
                planned_purchase_days=planned_purchase_days,
            )
            for code, item in execution.funds.items():
                state.shares[code] = item.executable_holding / float(signal_nav[code])
            if "RiskFree" in df_nav.columns:
                state.risk_free_shares = execution.risk_free_after / float(
                    signal_nav["RiskFree"]
                )
            state.cash = execution.cash_after

            buy_fee_cost = sum(
                item.gross_buy - item.gross_buy / (1 + item.buy_fee_rate)
                for item in execution.funds.values()
            )
            sell_fee_cost = sum(
                item.gross_sell - item.net_sell_proceeds
                for item in execution.funds.values()
            )
            state.total_fee_cost += buy_fee_cost + sell_fee_cost
            allocation_diagnostics = execution.allocation_diagnostics
            state.total_unspent_execution_budget += float(
                allocation_diagnostics.get("unspent_budget", 0.0) or 0.0
            )
            state.total_substitute_purchases += sum(
                float(value)
                for value in (
                    allocation_diagnostics.get("substitute_purchases", {}) or {}
                ).values()
            )
            tracking_before = allocation_diagnostics.get("tracking_error_before")
            tracking_after = allocation_diagnostics.get("tracking_error_after")
            if tracking_before is not None:
                state.tracking_errors_before.append(float(tracking_before))
            if tracking_after is not None:
                state.tracking_errors_after.append(float(tracking_after))
            state.allocation_fallback_count += int(
                bool(allocation_diagnostics.get("fallback_used"))
            )
            acquired_value = execution.total_gross_buy
            risk_free_purchase = max(
                0.0, execution.risk_free_after - execution.current_risk_free
            )
            if acquired_value + risk_free_purchase > 0:
                state.purchase_lots.append(
                    (len(state.returns) + 1, acquired_value + risk_free_purchase)
                )
            period_trade = execution.total_gross_buy + execution.total_gross_sell
            state.total_trade_value += period_trade
            state.sell_count += sum(
                1 for item in execution.funds.values() if item.gross_sell > 1e-9
            )
            gross_target_gaps = sum(
                max(0.0, item.target_holding - item.current_holding)
                * (1 + item.buy_fee_rate)
                for item in execution.funds.values()
            )
            state.total_unfilled_gap += max(
                0.0, gross_target_gaps - execution.total_gross_buy
            )
            closing_signal_wealth = (
                sum(item.executable_holding for item in execution.funds.values())
                + execution.risk_free_after
                + execution.cash_after
            )
            actual_values = pd.Series(
                {
                    **{
                        code: item.executable_holding
                        for code, item in execution.funds.items()
                    },
                    "RiskFree": execution.risk_free_after,
                    "Cash": execution.cash_after,
                },
                dtype=float,
            )
            target_values = pd.Series(
                {
                    **targets.to_dict(),
                    "RiskFree": target_risk_free,
                    "Cash": target_cash,
                },
                dtype=float,
            )
            denominator = max(total_wealth, closing_signal_wealth, 1.0)
            state.execution_deviations.append(
                float((actual_values - target_values).abs().sum() / (2 * denominator))
            )
            current_weight_vector = risky_weights.reindex(risky_codes).fillna(0.0)
            current_weight_total = float(current_weight_vector.sum())
            if current_weight_total > 0:
                current_weight_vector = current_weight_vector / current_weight_total
            actual_weight_vector = pd.Series(
                {
                    code: execution.funds[code].executable_holding
                    for code in risky_codes
                },
                dtype=float,
            )
            actual_weight_total = float(actual_weight_vector.sum())
            if actual_weight_total > 0:
                actual_weight_vector = actual_weight_vector / actual_weight_total
            else:
                actual_weight_vector[:] = 0.0
            frontier_target_change = 0.0
            actual_basket_change = 0.0
            frontier_change_transmission = None
            if state.previous_weights is not None:
                target_delta = current_weight_vector - state.previous_weights
                actual_delta = actual_weight_vector - state.previous_actual_weights
                frontier_target_change = float(target_delta.abs().sum() / 2)
                actual_basket_change = float(actual_delta.abs().sum() / 2)
                state.weight_drifts.append(frontier_target_change)
                state.actual_weight_drifts.append(actual_basket_change)
                target_move = float(target_delta.abs().sum())
                if target_move > 1e-9:
                    aligned_move = sum(
                        min(
                            abs(float(actual_delta[code])),
                            abs(float(target_delta[code])),
                        )
                        for code in risky_codes
                        if float(actual_delta[code]) * float(target_delta[code]) > 0
                    )
                    frontier_change_transmission = float(
                        np.clip(aligned_move / target_move, 0.0, 1.0)
                    )
                    state.frontier_change_transmissions.append(
                        frontier_change_transmission
                    )
            state.previous_weights = current_weight_vector
            state.previous_actual_weights = actual_weight_vector

            trade_signature = {
                code: {
                    "gross_buy": float(item.gross_buy),
                    "gross_sell": float(item.gross_sell),
                }
                for code, item in execution.funds.items()
            }
            trade_signature["RiskFree"] = {
                "gross_buy": float(
                    max(0.0, execution.risk_free_after - execution.current_risk_free)
                ),
                "gross_sell": float(
                    max(0.0, execution.current_risk_free - execution.risk_free_after)
                ),
            }
            monthly_execution_snapshots[strategy_name] = trade_signature

            realized_assets = state.shares * realized_nav[state.shares.index]
            realized_risk_free = (
                state.risk_free_shares * float(realized_nav["RiskFree"])
                if "RiskFree" in df_nav.columns
                else 0.0
            )
            realized_wealth = float(
                realized_assets.sum() + realized_risk_free + state.cash
            )
            realized_unit_nav = (
                realized_wealth / state.units if state.units > 0 else 1.0
            )
            period_return = realized_unit_nav / unit_nav_before - 1.0
            state.returns.append(float(period_return))
            date_key = realized_date.strftime("%Y-%m")
            state.unit_nav_history[date_key] = realized_unit_nav
            state.wealth_history[date_key] = realized_wealth
            state.cash_exposures.append(
                state.cash / realized_wealth if realized_wealth > 0 else 0.0
            )
            realized_asset_values = {
                **realized_assets.to_dict(),
                **(
                    {"RiskFree": realized_risk_free}
                    if "RiskFree" in df_nav.columns
                    else {}
                ),
            }
            exposure_metrics = calculate_exposure_metrics(
                realized_asset_values,
                total_wealth=realized_wealth,
                asset_categories=normalized_asset_categories,
            )
            state.category_exposures.append(exposure_metrics["category_exposures"])
            no_kelly_signature = monthly_execution_snapshots.get("no_kelly")
            kelly_changed_trade = None
            if strategy_name == "full_strategy" and no_kelly_signature is not None:
                all_trade_codes = set(trade_signature) | set(no_kelly_signature)
                kelly_changed_trade = any(
                    abs(
                        trade_signature.get(code, {}).get(action, 0.0)
                        - no_kelly_signature.get(code, {}).get(action, 0.0)
                    )
                    > 1e-6
                    for code in all_trade_codes
                    for action in ("gross_buy", "gross_sell")
                )
            state.audit.append(
                {
                    "training_end": signal_date.strftime("%Y-%m-%d"),
                    "execution_date": signal_date.strftime("%Y-%m-%d"),
                    "realized_date": realized_date.strftime("%Y-%m-%d"),
                    "full_kelly_raw": optimizer_info.get("full_kelly"),
                    "fractional_kelly_raw": optimizer_info.get("fractional_kelly"),
                    "kelly_clipped_target": float(tactical_ratio),
                    "target_fund_position": target_risky_ratio,
                    "actual_fund_position": (
                        float(
                            sum(
                                item.executable_holding
                                for item in execution.funds.values()
                            )
                            / closing_signal_wealth
                        )
                        if closing_signal_wealth > 0
                        else 0.0
                    ),
                    "kelly_changed_trade": kelly_changed_trade,
                    "frontier_target_weight_change": frontier_target_change,
                    "actual_basket_weight_change": actual_basket_change,
                    "frontier_change_transmission": frontier_change_transmission,
                    "frontier_target_weights": {
                        code: float(current_weight_vector[code]) for code in risky_codes
                    },
                    "actual_basket_weights": {
                        code: float(actual_weight_vector[code]) for code in risky_codes
                    },
                    "allocation_diagnostics": dict(execution.allocation_diagnostics),
                }
            )

    strategies = {
        name: _summarize_state(strategy_states[name], risk_free_rate, cvar_confidence)
        for name in WALK_FORWARD_STRATEGIES
    }
    window_metrics = {
        window: _summarize_state(states[name], risk_free_rate, cvar_confidence)
        for name, window in window_state_names.items()
    }
    covariance_ablation = (
        _compare_complete_covariance_states(
            states["full_strategy"],
            states["full_strategy_ledoit_wolf"],
            risk_free_rate,
        )
        if include_covariance_ablation
        else None
    )
    allocation_ablation = None
    if allocation_baseline_name is not None:
        baseline_metrics = _summarize_state(
            states[allocation_baseline_name], risk_free_rate, cvar_confidence
        )
        candidate_metrics = strategies["full_strategy"]
        candidate_qualifies = (
            candidate_metrics["average_execution_deviation"]
            <= baseline_metrics["average_execution_deviation"] + 1e-9
            and candidate_metrics["max_drawdown"]
            <= baseline_metrics["max_drawdown"] + 0.01
            and candidate_metrics["total_transaction_fees"]
            <= baseline_metrics["total_transaction_fees"] * 1.05 + 1e-9
        )
        allocation_ablation = {
            "evaluation_scope": "complete_executable_strategy",
            "baseline_method": "proportional_gap",
            "candidate_method": "constrained_tracking",
            "auto_switched": False,
            "promotion_status": (
                "candidate" if candidate_qualifies else "retain_proportional"
            ),
            "selection_rule": (
                "lower_execution_deviation_with_noninferior_drawdown_and_fees"
            ),
            "baseline": baseline_metrics,
            "candidate": candidate_metrics,
        }
    return {
        "status": "ok",
        "minimum_training_months": min_train_months,
        "evaluation_months": len(next(iter(states.values())).returns),
        "selection_rule": "training_only_maximum_excess_sharpe",
        "limit_history_assumption": "fixed_user_scenario",
        "cash_flow_policy": "identical_initial_capital_and_monthly_contributions",
        "metric_basis": {
            "return_series": "unit_nav_after_external_cash_flows",
            "external_cash_flow_timing": "start_of_period_unit_issuance",
            "annualized_return": "geometric_from_monthly_unit_nav_returns",
            "sharpe": "annualized_return_minus_risk_free_over_annualized_volatility",
            "max_drawdown": "unit_nav_with_initial_nav_anchor",
            "cvar_horizon": "monthly",
        },
        "strategies": strategies,
        "kelly_window_comparison": {
            "windows": [
                {"window_months": window, **window_metrics[window]}
                for window in KELLY_WINDOW_MONTHS
                if window in window_metrics
            ],
            **_select_stable_kelly_platform(window_metrics),
        },
        "covariance_ablation": covariance_ablation,
        "allocation_ablation": allocation_ablation,
    }
