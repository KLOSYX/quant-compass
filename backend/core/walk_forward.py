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
from core.decision import ExecutionContext, PortfolioState, StrategySpec, plan_month
from core.domain import SubstitutionGroup
from core.ledger import AccountUnitLedger
from core.execution_optimizer import (
    build_execution_covariance,
    strategic_fund_codes,
)
from core.frontier import calculate_efficient_frontier
from core.portfolio import decompose_selected_weights
from core.risk import calculate_cvar_loss, calculate_drawdown_from_returns


WALK_FORWARD_STRATEGIES = (
    "equal_weight_dca",
    "fixed_weight_dca",
    "full_strategy",
)


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
        segments[name] = {"fixed_20": fixed, "ledoit_wolf": candidate}

    return {
        "evaluation_scope": "complete_executable_strategy",
        "default_method": "fixed_20",
        "candidate_method": "ledoit_wolf",
        "promotion_status": "descriptive_only",
        "auto_switched": False,
        "segments": segments,
    }


@dataclass
class _StrategyState:
    ledger: AccountUnitLedger
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
    reusable_cash: float = 0.0

    @property
    def shares(self):
        return pd.Series(
            {
                code: amount
                for code, amount in self.ledger.shares.items()
                if code != "RiskFree"
            },
            dtype=float,
        )

    @property
    def risk_free_shares(self):
        return self.ledger.shares.get("RiskFree", 0.0)

    @property
    def cash(self):
        return self.ledger.cash

    @property
    def units(self):
        return self.ledger.total_units


def _select_train_point(frontier: list[dict], risk_free_rate: float) -> dict:
    """Use the same historical minimum-risk reference as candidate generation."""
    return min(frontier, key=lambda point: float(point["risk"]))


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
        ledger=AccountUnitLedger.initialize(
            {
                **shares.to_dict(),
                **(
                    {"RiskFree": risk_free_shares}
                    if "RiskFree" in df_nav.columns
                    else {}
                ),
            },
            float(initial_cash or 0.0),
            signal_nav.to_dict(),
        ),
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
    planning_period_days: int = 30,
    planned_purchase_days: int | None = None,
    execution_nav: pd.DataFrame | None = None,
    corporate_actions=(),
    account_distribution_policy="cash",
    min_purchase_amount=0.0,
    amount_step=0.01,
    rebalance_enabled=True,
    rebalance_band=0.02,
    available_existing_cash=0.0,
    redemption_limits=None,
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
    account_nav = (
        df_nav
        if execution_nav is None
        else execution_nav.reindex(index=df_nav.index, columns=df_nav.columns)
    )
    if account_nav.isna().any().any() or (account_nav <= 0).any().any():
        raise ValueError("execution prices must cover every replay date and asset")
    first_signal_position = min_train_months - 1
    strategy_states = {
        name: _initialize_state(
            account_nav,
            first_signal_position,
            risky_codes,
            initial_holdings,
            initial_cash,
        )
        for name in WALK_FORWARD_STRATEGIES
    }
    states = dict(strategy_states)
    if include_covariance_ablation:
        states["full_strategy_ledoit_wolf"] = _initialize_state(
            account_nav,
            first_signal_position,
            risky_codes,
            initial_holdings,
            initial_cash,
        )
    allocation_baseline_name = None
    if execution_allocation_method == "constrained_tracking":
        allocation_baseline_name = "full_strategy_proportional_gap"
        states[allocation_baseline_name] = _initialize_state(
            account_nav,
            first_signal_position,
            risky_codes,
            initial_holdings,
            initial_cash,
        )
    if not strategic_codes:
        raise ValueError("at least one strategic fund is required")
    equal_weights = {code: 1.0 / len(strategic_codes) for code in strategic_codes}
    fixed_weights = None
    for state in states.values():
        state.reusable_cash = min(available_existing_cash, initial_cash)

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
        if len(frontier_train_df) < 3:
            # No covariance estimate: use an explicit equal-weight accounting baseline.
            dynamic_weights = equal_weights
        else:
            frontier = calculate_efficient_frontier(frontier_train_df, dict(fund_fees))
            if not frontier:
                continue
            dynamic_weights = _select_train_point(frontier, risk_free_rate)["weights"]
        candidate_weights = None
        if include_covariance_ablation and len(frontier_train_df) >= 3:
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
            "full_strategy": dynamic_weights,
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
            settled_sales = state.ledger.receivables.pop("sale_proceeds", 0.0)
            state.ledger.cash += settled_sales
            state.reusable_cash += settled_sales
            signal_nav = account_nav.iloc[signal_position]
            realized_nav = account_nav.iloc[realized_position]
            current_assets = state.shares * signal_nav[state.shares.index]
            current_risk_free = (
                state.risk_free_shares * float(signal_nav["RiskFree"])
                if "RiskFree" in df_nav.columns
                else 0.0
            )
            wealth_before_flow = float(
                current_assets.sum()
                + current_risk_free
                + state.cash
                + sum(state.ledger.receivables.values())
            )
            unit_nav_before = (
                wealth_before_flow / state.units if state.units > 0 else 1.0
            )
            if monthly_investment > 0:
                state.ledger.contribute(
                    signal_date, monthly_investment, signal_nav.to_dict()
                )
                state.external_contributions += monthly_investment
            total_wealth = wealth_before_flow + monthly_investment

            selected = decompose_selected_weights(weights, list(df_nav.columns))
            risky_weights = selected["risky_weights"]
            execution = plan_month(
                columns=list(df_nav.columns),
                state=PortfolioState(
                    holdings={
                        **current_assets.to_dict(),
                        "RiskFree": current_risk_free,
                    },
                    cash=state.cash - monthly_investment,
                ),
                strategy=StrategySpec(strategy_name, weights),
                context=ExecutionContext(
                    monthly_budget=monthly_investment,
                    rebalance_enabled=rebalance_enabled
                    and strategy_name != "equal_weight_dca",
                    rebalance_band=rebalance_band,
                    redemption_limits=redemption_limits or {},
                    available_existing_cash=min(
                        state.reusable_cash, state.cash - monthly_investment
                    ),
                    buy_fees=buy_fees,
                    sell_fees=sell_fees,
                    investment_limits=fund_investment_limits or {},
                    minimum_cash_reserve=minimum_cash_reserve,
                    allocation_method="proportional_gap"
                    if strategy_name == allocation_baseline_name
                    else execution_allocation_method,
                    execution_covariance=build_execution_covariance(
                        train_df,
                        risky_codes,
                        estimation_window=estimation_window,
                        covariance_method="ledoit_wolf"
                        if strategy_name == "full_strategy_ledoit_wolf"
                        else "fixed_20",
                    ),
                    substitution_groups=tuple(
                        SubstitutionGroup(
                            primary,
                            primary,
                            tuple(
                                code
                                for code, owner in (substitute_for or {}).items()
                                if owner == primary
                            ),
                        )
                        for primary in sorted(set((substitute_for or {}).values()))
                        if primary
                    ),
                    planning_period_days=planning_period_days,
                    planned_purchase_days=planned_purchase_days,
                    min_purchase_amount=min_purchase_amount,
                    amount_step=amount_step,
                ),
                as_of=signal_date,
            )
            target_risky_ratio = (
                sum(item.target_holding for item in execution.funds.values())
                / total_wealth
                if total_wealth > 0
                else 0.0
            )
            state.ledger.apply_execution(execution, signal_nav.to_dict())
            state.reusable_cash = max(
                0.0,
                state.reusable_cash
                - max(
                    0.0,
                    execution.total_gross_buy
                    + execution.risk_free_gross_buy
                    - monthly_investment,
                ),
            )

            state.total_fee_cost += execution.transaction_fees
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
                + execution.pending_sale_proceeds
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
                    **{
                        code: item.target_holding
                        for code, item in execution.funds.items()
                    },
                    "RiskFree": execution.target_risk_free,
                    "Cash": execution.target_cash,
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

            state.ledger.process_actions(
                corporate_actions,
                signal_date,
                realized_date,
                daily_nav if daily_nav is not None else account_nav,
                account_distribution_policy,
            )
            realized_assets = state.shares * realized_nav[state.shares.index]
            realized_risk_free = (
                state.risk_free_shares * float(realized_nav["RiskFree"])
                if "RiskFree" in df_nav.columns
                else 0.0
            )
            realized_wealth = float(
                realized_assets.sum()
                + realized_risk_free
                + state.cash
                + sum(state.ledger.receivables.values())
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
            state.audit.append(
                {
                    "training_end": signal_date.strftime("%Y-%m-%d"),
                    "execution_date": signal_date.strftime("%Y-%m-%d"),
                    "realized_date": realized_date.strftime("%Y-%m-%d"),
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
        "selection_rule": "training_only_minimum_variance_reference",
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
        "covariance_ablation": covariance_ablation,
        "allocation_ablation": allocation_ablation,
    }
