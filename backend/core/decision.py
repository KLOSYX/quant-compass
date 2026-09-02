"""Deterministic decision contract shared by online and replay callers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd

from core.domain import MarketSnapshot, SubstitutionGroup, canonical_hash
from core.execution import ExecutionResult, execute_monthly_plan
from core.portfolio import decompose_selected_weights
from core.validation import non_negative_number, unit_interval_number


DECISION_ENGINE_VERSION = "2.0"


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
    tactical_deployment_ratio: float

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise ValueError("strategy policy id is required")
        object.__setattr__(
            self,
            "tactical_deployment_ratio",
            unit_interval_number(
                self.tactical_deployment_ratio, "tactical_deployment_ratio"
            ),
        )


@dataclass(frozen=True)
class ExecutionContext:
    monthly_budget: float
    buy_fees: Mapping[str, float] = field(default_factory=dict)
    sell_fees: Mapping[str, float] = field(default_factory=dict)
    investment_limits: Mapping[str, object] = field(default_factory=dict)
    substitution_groups: tuple[SubstitutionGroup, ...] = ()
    minimum_cash_reserve: float = 0.0
    allow_sales: bool = False
    exit_fund_codes: tuple[str, ...] = ()
    reuse_settled_sale_proceeds: bool = False
    min_purchase_amount: float = 0.0
    amount_step: float = 0.01
    planned_purchase_days: int | None = None
    allocation_method: str = "proportional_gap"
    execution_covariance: pd.DataFrame | None = None

    def __post_init__(self) -> None:
        for name in (
            "monthly_budget",
            "minimum_cash_reserve",
            "min_purchase_amount",
            "amount_step",
        ):
            object.__setattr__(
                self, name, non_negative_number(getattr(self, name), name)
            )
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

    columns = list(snapshot.price_series)
    selected = decompose_selected_weights(
        dict(decision_input.strategy_spec.target_weights), columns
    )
    base_ratio = float(selected["base_non_riskfree_fund_ratio"])
    base_safe_ratio = float(selected["base_risk_free_ratio"])
    holdings = dict(decision_input.portfolio_state.holdings)
    current_risk_free = holdings.pop("RiskFree", 0.0)
    total_wealth = (
        sum(holdings.values())
        + current_risk_free
        + decision_input.portfolio_state.cash
        + decision_input.execution_context.monthly_budget
    )
    reserve_ratio = (
        decision_input.execution_context.minimum_cash_reserve / total_wealth
        if total_wealth > 0
        else 0.0
    )
    final_ratio = min(
        base_ratio * decision_input.strategy_spec.tactical_deployment_ratio,
        max(0.0, 1.0 - base_safe_ratio - reserve_ratio),
    )
    risky_weights = selected["risky_weights"]
    target_total = total_wealth * final_ratio
    targets = {
        code: target_total * float(risky_weights.get(code, 0.0))
        for code in risky_weights.index
    }
    substitute_for = {
        substitute: group.primary_fund
        for group in decision_input.execution_context.substitution_groups
        for substitute in group.substitutes
    }
    context = decision_input.execution_context
    target_risk_free = total_wealth * base_safe_ratio
    target_cash = max(0.0, total_wealth - target_total - target_risk_free)
    execution = execute_monthly_plan(
        fund_codes=list(risky_weights.index),
        current_holdings=holdings,
        target_holdings=targets,
        target_weights=risky_weights.to_dict(),
        current_cash=decision_input.portfolio_state.cash,
        monthly_budget=context.monthly_budget,
        buy_fees=dict(context.buy_fees),
        sell_fees=dict(context.sell_fees),
        investment_limits=dict(context.investment_limits),
        timestamp=decision_input.as_of,
        minimum_cash_reserve=context.minimum_cash_reserve,
        exit_fund_codes=context.exit_fund_codes if context.allow_sales else (),
        reuse_settled_sale_proceeds=context.reuse_settled_sale_proceeds,
        current_risk_free=current_risk_free,
        target_risk_free=target_risk_free,
        target_cash=target_cash,
        can_manage_risk_free="RiskFree" in columns and context.allow_sales,
        target_has_risk_free=float(selected["base_risk_free_ratio"]) > 0,
        allocation_method=context.allocation_method,
        execution_covariance=context.execution_covariance,
        substitute_for=substitute_for,
        planned_purchase_days=context.planned_purchase_days,
    )
    quality_ok = all(
        item.status in {"verified", "reconciled"}
        and item.corporate_action_coverage_complete
        for item in snapshot.return_quality.values()
    )
    warnings = () if quality_ok else ("return_data_quality_requires_research_mode",)
    residuals = {
        "accounting_error": float(execution.accounting_error),
        "aggregate_net_gap_respected": bool(
            execution.allocation_diagnostics.get("constraint_residuals", {}).get(
                "feasible", True
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
        target_allocation=targets,
        execution_plan=execution,
        constraint_residuals=residuals,
        warnings=warnings,
        decision_readiness="manual_review_required" if quality_ok else "research_only",
    )
