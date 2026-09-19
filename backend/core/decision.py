"""Deterministic decision contract shared by online and replay callers."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

import pandas as pd

from core.domain import MarketSnapshot, SubstitutionGroup, canonical_hash
from core.execution import ExecutionResult, execute_monthly_plan
from core.portfolio import decompose_selected_weights
from core.rebalancing import plan_rebalance_sales
from core.validation import non_negative_number, unit_interval_number


DECISION_ENGINE_VERSION = "4.1"


@dataclass(frozen=True)
class PortfolioState:
    holdings: Mapping[str, float]
    cash: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "holdings",
            {
                code: non_negative_number(value, f"holding {code}")
                for code, value in self.holdings.items()
            },
        )
        object.__setattr__(self, "cash", non_negative_number(self.cash, "cash"))


@dataclass(frozen=True)
class StrategySpec:
    policy_id: str
    target_weights: Mapping[str, float]

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("strategy policy id is required")


@dataclass(frozen=True)
class ExecutionContext:
    monthly_budget: float
    buy_fees: Mapping[str, float] = field(default_factory=dict)
    sell_fees: Mapping[str, float] = field(default_factory=dict)
    investment_limits: Mapping[str, object] = field(default_factory=dict)
    substitution_groups: tuple[SubstitutionGroup, ...] = ()
    minimum_cash_reserve: float = 0.0
    allow_sales: bool = False
    repair_cash_reserve: bool = True
    exit_fund_codes: tuple[str, ...] = ()
    reuse_settled_sale_proceeds: bool = False
    min_purchase_amount: float = 0.0
    amount_step: float = 0.01
    planning_period_days: int = 30
    planned_purchase_days: int | None = None
    allocation_method: str = "proportional_gap"
    execution_covariance: pd.DataFrame | None = None
    rebalance_enabled: bool = True
    rebalance_band: float = 0.02
    available_existing_cash: float = 0.0
    pending_sale_proceeds: float = 0.0
    pending_sell_amounts: Mapping[str, float] = field(default_factory=dict)
    redemption_limits: Mapping[str, float] = field(default_factory=dict)
    planned_purchase_dates: tuple = ()

    def __post_init__(self) -> None:
        for name in (
            "monthly_budget",
            "minimum_cash_reserve",
            "min_purchase_amount",
            "amount_step",
            "available_existing_cash",
            "pending_sale_proceeds",
        ):
            object.__setattr__(
                self, name, non_negative_number(getattr(self, name), name)
            )
        unit_interval_number(self.rebalance_band, "rebalance_band")
        owners: dict[str, str] = {}
        for group in self.substitution_groups:
            for substitute in group.substitutes:
                if substitute in owners:
                    raise ValueError(
                        f"substitute {substitute} belongs to overlapping groups "
                        f"{owners[substitute]} and {group.group_id}"
                    )
                owners[substitute] = group.group_id


@dataclass(frozen=True)
class DecisionInput:
    as_of: pd.Timestamp
    portfolio_state: PortfolioState
    market_snapshot: MarketSnapshot
    strategy_spec: StrategySpec
    execution_context: ExecutionContext

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", pd.Timestamp(self.as_of))


@dataclass(frozen=True)
class DecisionResult:
    strategy_policy_id: str
    strategy_spec_hash: str
    market_snapshot_hash: str
    portfolio_state_hash: str
    decision_engine_version: str
    solver_version: str
    signal_cutoff: pd.Timestamp
    target_allocation: Mapping[str, float]
    execution_plan: ExecutionResult
    constraint_residuals: Mapping[str, float | bool]
    warnings: tuple[str, ...]
    decision_readiness: str


def plan_month(
    *,
    columns,
    state: PortfolioState,
    strategy: StrategySpec,
    context: ExecutionContext,
    as_of,
) -> ExecutionResult:
    """One target-to-trade policy for live decisions and historical replay.

    The theoretical target is preserved in StrategySpec; execution reserves Cash
    first, caps the safe sleeve and explicitly reports any remaining shortfall.
    """
    selected = decompose_selected_weights(dict(strategy.target_weights), columns)
    base_ratio = float(selected["base_non_riskfree_fund_ratio"])
    base_safe_ratio = float(selected["base_risk_free_ratio"])
    holdings = dict(state.holdings)
    current_risk_free = holdings.pop("RiskFree", 0.0)
    total_wealth = (
        sum(holdings.values())
        + current_risk_free
        + state.cash
        + context.monthly_budget
        + context.pending_sale_proceeds
    )
    accessible = min(
        context.monthly_budget + context.available_existing_cash,
        max(0.0, state.cash + context.monthly_budget - context.minimum_cash_reserve),
    )
    reserve_deficit = max(
        0.0, context.minimum_cash_reserve - state.cash - context.monthly_budget
    )
    capital = max(
        0.0,
        sum(holdings.values())
        + current_risk_free
        + accessible
        + context.pending_sale_proceeds
        - reserve_deficit,
    )
    risky_weights = selected["risky_weights"]
    target_total = capital * base_ratio
    targets = {
        code: target_total * float(risky_weights.get(code, 0.0))
        for code in risky_weights.index
    }
    substitute_for = {
        substitute: group.primary_fund
        for group in context.substitution_groups
        for substitute in group.substitutes
    }
    target_risk_free = capital * base_safe_ratio
    target_cash = max(0.0, total_wealth - target_total - target_risk_free)
    purchase_days = (
        context.planned_purchase_days
        if context.planned_purchase_days is not None
        else 21
    )
    if context.planned_purchase_dates:
        dates = [pd.Timestamp(value).date() for value in context.planned_purchase_dates]
        today = pd.Timestamp(as_of).date()
        if len(set(dates)) != len(dates) or any(
            d < today
            or d
            >= (
                pd.Timestamp(today) + pd.Timedelta(days=context.planning_period_days)
            ).date()
            for d in dates
        ):
            raise ValueError(
                "purchase dates must be distinct dates within the planning period"
            )
        purchase_days = len(dates)
    if purchase_days > context.planning_period_days or context.planning_period_days < 1:
        raise ValueError("planned_purchase_days cannot exceed planning_period_days")
    kwargs = dict(
        fund_codes=list(risky_weights.index),
        current_holdings=holdings,
        target_holdings=targets,
        target_weights=risky_weights.to_dict(),
        current_cash=state.cash,
        monthly_budget=context.monthly_budget,
        buy_fees=dict(context.buy_fees),
        sell_fees=dict(context.sell_fees),
        investment_limits=dict(context.investment_limits),
        timestamp=as_of,
        minimum_cash_reserve=context.minimum_cash_reserve,
        exit_fund_codes=context.exit_fund_codes if context.allow_sales else (),
        reuse_settled_sale_proceeds=context.reuse_settled_sale_proceeds,
        current_risk_free=current_risk_free,
        target_risk_free=target_risk_free,
        target_cash=target_cash,
        can_manage_risk_free=False,
        target_has_risk_free=float(selected["base_risk_free_ratio"]) > 0,
        allocation_method=context.allocation_method,
        execution_covariance=context.execution_covariance,
        substitute_for=substitute_for,
        planned_purchase_days=purchase_days,
        min_purchase_amount=context.min_purchase_amount,
        amount_step=context.amount_step,
        repair_cash_reserve=context.repair_cash_reserve,
        available_existing_cash=context.available_existing_cash,
        redemption_limits=context.redemption_limits,
        pending_sell_amounts=context.pending_sell_amounts,
        existing_pending_proceeds=context.pending_sale_proceeds,
    )
    execution = execute_monthly_plan(**kwargs)
    rebalance = {"status": "disabled", "band": context.rebalance_band}
    if context.rebalance_enabled:
        if context.pending_sale_proceeds > 0 or any(
            context.pending_sell_amounts.values()
        ):
            rebalance = {
                "status": "awaiting_existing_orders",
                "band": context.rebalance_band,
            }
        elif execution.total_gross_sell > 0 or execution.cash_reserve_shortfall > 0:
            rebalance = {
                "status": "cash_repair_or_exit_first",
                "band": context.rebalance_band,
            }
        else:
            all_targets = {
                **targets,
                **({"RiskFree": target_risk_free} if "RiskFree" in columns else {}),
            }
            values = {c: f.executable_holding for c, f in execution.funds.items()}
            buys = {c: f.gross_buy for c, f in execution.funds.items()}
            if "RiskFree" in columns:
                values["RiskFree"] = execution.risk_free_after
                buys["RiskFree"] = execution.risk_free_gross_buy
            sales, rebalance = plan_rebalance_sales(
                holdings=state.holdings,
                targets=all_targets,
                baseline_values=values,
                baseline_buys=buys,
                buy_fees=context.buy_fees,
                sell_fees=context.sell_fees,
                investment_limits=context.investment_limits,
                redemption_limits=context.redemption_limits,
                available_budget=accessible,
                band=context.rebalance_band,
                timestamp=as_of,
                planned_purchase_days=purchase_days,
            )
            if sales:
                # A projection may use future proceeds; today's recommendation may not.
                preview = execute_monthly_plan(
                    **{
                        **kwargs,
                        "planned_sales": sales,
                        "reuse_settled_sale_proceeds": True,
                    }
                )
                extra_buys = (
                    preview.total_gross_buy
                    + preview.risk_free_gross_buy
                    - sum(buys.values())
                )
                if preview.total_net_sell_proceeds <= extra_buys + 0.01:
                    execution = execute_monthly_plan(
                        **{
                            **kwargs,
                            "planned_sales": sales,
                            "reuse_settled_sale_proceeds": False,
                        }
                    )
                    rebalance["conditional_buys_after_settlement"] = {
                        c: max(0.0, f.gross_buy - execution.funds[c].gross_buy)
                        for c, f in preview.funds.items()
                        if f.gross_buy > execution.funds[c].gross_buy + 0.01
                    }
                    if (
                        preview.risk_free_gross_buy
                        > execution.risk_free_gross_buy + 0.01
                    ):
                        rebalance["conditional_buys_after_settlement"]["RiskFree"] = (
                            preview.risk_free_gross_buy - execution.risk_free_gross_buy
                        )
                    rebalance["condition"] = (
                        "卖出净款实际到账后，更新持仓和可用现金，重新核验购买日期、资格与剩余额度"
                    )
                else:
                    rebalance["status"] = "rounded_purchase_capacity_insufficient"
    diagnostics = {
        **execution.allocation_diagnostics,
        "rebalancing": rebalance,
        "purchase_dates": [str(d) for d in context.planned_purchase_dates],
        "planned_purchase_days": purchase_days,
        "planning_period_days": context.planning_period_days,
        "schedule_status": "user_supplied_dates_require_channel_confirmation"
        if context.planned_purchase_dates
        else "estimated_trading_days_no_calendar",
    }
    return replace(
        execution,
        allocation_diagnostics=diagnostics,
        pending_sale_proceeds=execution.pending_sale_proceeds
        + context.pending_sale_proceeds,
    )


def decide(decision_input: DecisionInput) -> DecisionResult:
    """Create one deterministic, no-I/O monthly decision."""

    snapshot = decision_input.market_snapshot
    future_assets = sorted(
        code
        for code, available_at in snapshot.available_at.items()
        if pd.Timestamp(available_at) > decision_input.as_of
    )
    if future_assets:
        raise ValueError(
            "market data is not available as of decision time: "
            + ", ".join(future_assets)
        )

    execution = plan_month(
        columns=list(snapshot.price_series),
        state=decision_input.portfolio_state,
        strategy=decision_input.strategy_spec,
        context=decision_input.execution_context,
        as_of=decision_input.as_of,
    )
    quality_ok = all(
        item.status in {"verified", "reconciled"}
        and item.corporate_action_coverage_complete
        for item in snapshot.return_quality.values()
    )
    warnings = () if quality_ok else ("return_data_quality_requires_research_mode",)
    residuals = {
        "cash_reserve_shortfall": execution.cash_reserve_shortfall,
        "accounting_error": float(execution.accounting_error),
        "aggregate_net_gap_respected": bool(
            execution.allocation_diagnostics.get("constraint_residuals", {}).get(
                "feasible", False
            )
        ),
    }
    return DecisionResult(
        strategy_policy_id=decision_input.strategy_spec.policy_id,
        strategy_spec_hash=canonical_hash(decision_input.strategy_spec),
        market_snapshot_hash=snapshot.data_hash,
        portfolio_state_hash=canonical_hash(decision_input.portfolio_state),
        decision_engine_version=DECISION_ENGINE_VERSION,
        solver_version=str(
            execution.allocation_diagnostics.get("solver", "deterministic-waterfill-v2")
        ),
        signal_cutoff=max(snapshot.available_at.values()),
        target_allocation={
            code: item.target_holding for code, item in execution.funds.items()
        },
        execution_plan=execution,
        constraint_residuals=residuals,
        warnings=warnings,
        decision_readiness="manual_review_required" if quality_ok else "research_only",
    )
