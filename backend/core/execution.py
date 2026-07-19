from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

from core.execution_optimizer import allocate_buy_amounts


EXECUTION_EPSILON = 1e-9


@dataclass(frozen=True)
class FundExecution:
    code: str
    current_holding: float
    target_holding: float
    executable_holding: float
    allocation_state: str
    gross_buy: float
    gross_sell: float
    net_sell_proceeds: float
    buy_fee_rate: float
    sell_fee_rate: float
    execution_role: str
    buy_source: str

    @property
    def gap(self) -> float:
        return self.target_holding - self.current_holding

    @property
    def action(self) -> str:
        if self.gross_sell > EXECUTION_EPSILON:
            return "Sell"
        if self.gross_buy > EXECUTION_EPSILON:
            return "Buy"
        return "Hold"


@dataclass(frozen=True)
class ExecutionResult:
    funds: Mapping[str, FundExecution]
    total_gross_buy: float
    total_gross_sell: float
    total_net_sell_proceeds: float
    reused_sale_proceeds: float
    cash_after: float
    current_risk_free: float
    target_risk_free: float
    risk_free_after: float
    target_cash: float
    accounting_error: float
    allocation_diagnostics: Mapping[str, object]


def _non_negative(value, name: str) -> float:
    parsed = float(value or 0.0)
    if parsed < -EXECUTION_EPSILON:
        raise ValueError(f"{name} must be non-negative")
    return max(0.0, parsed)


def _fee_rate(value, name: str) -> float:
    parsed = _non_negative(value, name)
    if parsed >= 1.0:
        raise ValueError(f"{name} must be less than 1")
    return parsed


def execute_monthly_plan(
    *,
    fund_codes: Sequence[str],
    current_holdings: Mapping[str, float],
    target_holdings: Mapping[str, float],
    target_weights: Mapping[str, float],
    current_cash: float,
    monthly_budget: float,
    buy_fees: Mapping[str, float] | None,
    sell_fees: Mapping[str, float] | None,
    investment_limits: Mapping[str, object] | None,
    timestamp,
    minimum_cash_reserve: float = 0.0,
    exit_fund_codes: Sequence[str] | None = None,
    reuse_settled_sale_proceeds: bool = False,
    current_risk_free: float = 0.0,
    target_risk_free: float = 0.0,
    target_cash: float = 0.0,
    can_manage_risk_free: bool = False,
    target_has_risk_free: bool = False,
    allocation_method: str = "proportional_gap",
    execution_covariance: pd.DataFrame | None = None,
    fund_roles: Mapping[str, str] | None = None,
    substitution_groups: Mapping[str, str] | None = None,
    proxy_penalties: Mapping[str, float] | None = None,
    planned_purchase_days: int | None = None,
) -> ExecutionResult:
    """Execute one fixed-budget contribution against theoretical target values."""
    codes = list(dict.fromkeys(fund_codes))
    code_set = set(codes)
    exits = set(exit_fund_codes or [])
    buy_fees = buy_fees or {}
    sell_fees = sell_fees or {}

    unknown_current = sorted(
        code
        for code, value in current_holdings.items()
        if code not in code_set and _non_negative(value, f"holding {code}") > 0
    )
    if unknown_current:
        raise ValueError(
            "current_holdings contain assets outside the execution universe: "
            + ", ".join(unknown_current)
        )
    unknown_targets = sorted(
        code
        for code, value in target_holdings.items()
        if code not in code_set and _non_negative(value, f"target {code}") > 0
    )
    if unknown_targets:
        raise ValueError(
            "target_holdings contain assets outside the execution universe: "
            + ", ".join(unknown_targets)
        )
    unknown_exits = sorted(exits - code_set)
    if unknown_exits:
        raise ValueError(
            "exit_fund_codes contain assets outside the current analysis universe: "
            + ", ".join(unknown_exits)
        )

    current = {
        code: _non_negative(current_holdings.get(code, 0.0), f"holding {code}")
        for code in codes
    }
    targets = {
        code: _non_negative(target_holdings.get(code, 0.0), f"target {code}")
        for code in codes
    }
    weights = {
        code: _non_negative(target_weights.get(code, 0.0), f"weight {code}")
        for code in codes
    }
    conflicting_exits = sorted(
        code
        for code in exits
        if targets[code] > EXECUTION_EPSILON or weights[code] > EXECUTION_EPSILON
    )
    if conflicting_exits:
        raise ValueError(
            "exit_fund_codes must have zero target weight: "
            + ", ".join(conflicting_exits)
        )

    cash_before = _non_negative(current_cash, "current_cash")
    external_budget = _non_negative(monthly_budget, "monthly_budget")
    cash_reserve = _non_negative(minimum_cash_reserve, "minimum_cash_reserve")
    risk_free_before = _non_negative(current_risk_free, "current_risk_free")
    risk_free_target = _non_negative(target_risk_free, "target_risk_free")
    cash_target = _non_negative(target_cash, "target_cash")

    sell_amounts = {code: current[code] if code in exits else 0.0 for code in codes}
    sell_fee_rates = {
        code: _fee_rate(sell_fees.get(code, 0.0), f"sell fee {code}") for code in codes
    }
    net_sell_proceeds = {
        code: sell_amounts[code] * (1.0 - sell_fee_rates[code]) for code in codes
    }
    total_gross_sell = sum(sell_amounts.values())
    total_net_sell = sum(net_sell_proceeds.values())
    reusable_sales = total_net_sell if reuse_settled_sale_proceeds else 0.0

    aggregate_gap = sum(targets.values()) - sum(current.values())
    contribution_for_funds = (
        external_budget if aggregate_gap > EXECUTION_EPSILON else 0.0
    )
    requested_buy_budget = contribution_for_funds + reusable_sales
    reserve_liquidity = cash_before + external_budget + total_net_sell
    if can_manage_risk_free:
        reserve_liquidity += risk_free_before
    spendable_liquidity = max(0.0, reserve_liquidity - cash_reserve)
    max_cash_to_spend = min(requested_buy_budget, spendable_liquidity)

    post_sell_holdings = {code: current[code] - sell_amounts[code] for code in codes}
    buy_fee_rates = {
        code: _fee_rate(buy_fees.get(code, 0.0), f"buy fee {code}") for code in codes
    }
    allocation = allocate_buy_amounts(
        fund_codes=codes,
        post_sell_holdings=post_sell_holdings,
        target_holdings=targets,
        target_weights=weights,
        buy_fees=buy_fee_rates,
        max_cash_to_spend=max_cash_to_spend,
        investment_limits=investment_limits,
        timestamp=timestamp,
        allocation_method=allocation_method,
        covariance=execution_covariance,
        fund_roles=fund_roles,
        substitution_groups=substitution_groups,
        proxy_penalties=proxy_penalties,
        planned_purchase_days=planned_purchase_days,
    )
    buy_allocations = allocation.gross_allocations
    normalized_roles = {
        code: str((fund_roles or {}).get(code, "strategic")) for code in codes
    }

    fund_results = {}
    for code in codes:
        gross_buy = float(buy_allocations.get(code, 0.0))
        net_buy_holding = gross_buy / (1.0 + buy_fee_rates[code])
        executable_holding = post_sell_holdings[code] + net_buy_holding
        is_substitute_buy = (
            normalized_roles[code] == "substitute" and gross_buy > EXECUTION_EPSILON
        )
        allocation_state = (
            "EXIT"
            if code in exits
            else "ACTIVE_SUBSTITUTE"
            if is_substitute_buy
            else "ACTIVE"
            if weights[code] > EXECUTION_EPSILON
            else "NO_NEW_BUY"
        )
        fund_results[code] = FundExecution(
            code=code,
            current_holding=current[code],
            target_holding=targets[code],
            executable_holding=executable_holding,
            allocation_state=allocation_state,
            gross_buy=gross_buy,
            gross_sell=sell_amounts[code],
            net_sell_proceeds=net_sell_proceeds[code],
            buy_fee_rate=buy_fee_rates[code],
            sell_fee_rate=sell_fee_rates[code],
            execution_role=normalized_roles[code],
            buy_source=(
                "limit_substitute"
                if is_substitute_buy
                else "target_gap"
                if gross_buy > EXECUTION_EPSILON
                else "none"
            ),
        )

    total_gross_buy = sum(item.gross_buy for item in fund_results.values())
    cash_after = cash_before + external_budget + total_net_sell - total_gross_buy
    risk_free_after = risk_free_before

    if can_manage_risk_free and cash_after < cash_target:
        redeem_amount = min(cash_target - cash_after, risk_free_after)
        risk_free_after -= redeem_amount
        cash_after += redeem_amount
    if target_has_risk_free and cash_after > cash_target:
        risk_free_buy = cash_after - cash_target
        risk_free_after += risk_free_buy
        cash_after -= risk_free_buy

    opening_wealth = (
        sum(current.values()) + risk_free_before + cash_before + external_budget
    )
    buy_fee_cost = sum(
        item.gross_buy - item.gross_buy / (1.0 + item.buy_fee_rate)
        for item in fund_results.values()
    )
    sell_fee_cost = total_gross_sell - total_net_sell
    closing_wealth = (
        sum(item.executable_holding for item in fund_results.values())
        + risk_free_after
        + cash_after
    )
    accounting_error = opening_wealth - buy_fee_cost - sell_fee_cost - closing_wealth
    if abs(accounting_error) > 1e-6:
        raise RuntimeError(
            f"monthly execution accounting invariant failed: {accounting_error}"
        )

    return ExecutionResult(
        funds=fund_results,
        total_gross_buy=total_gross_buy,
        total_gross_sell=total_gross_sell,
        total_net_sell_proceeds=total_net_sell,
        reused_sale_proceeds=reusable_sales,
        cash_after=cash_after,
        current_risk_free=risk_free_before,
        target_risk_free=risk_free_target,
        risk_free_after=risk_free_after,
        target_cash=cash_target,
        accounting_error=accounting_error,
        allocation_diagnostics=allocation.diagnostics.to_dict(),
    )
