from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Mapping, Sequence

import pandas as pd

from core.execution_optimizer import allocate_buy_amounts
from core.limits import monthly_investment_limit
from core.validation import non_negative_number, unit_interval_number


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
    cash_reserve_shortfall: float = 0.0
    pending_sale_proceeds: float = 0.0
    risk_free_gross_buy: float = 0.0
    risk_free_gross_sell: float = 0.0
    transaction_fees: float = 0.0


def _non_negative(value, name: str) -> float:
    return non_negative_number(value, name, epsilon=EXECUTION_EPSILON)


def _fee_rate(value, name: str) -> float:
    return unit_interval_number(value, name, include_one=False)


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
    substitute_for: Mapping[str, str] | None = None,
    planned_purchase_days: int | None = None,
    min_purchase_amount: float = 0.0,
    amount_step: float = 0.01,
    repair_cash_reserve: bool = False,
    available_existing_cash: float = 0.0,
    planned_sales: Mapping[str, float] | None = None,
    redemption_limits: Mapping[str, float] | None = None,
    pending_sell_amounts: Mapping[str, float] | None = None,
    existing_pending_proceeds: float = 0.0,
) -> ExecutionResult:
    """Execute one fixed-budget contribution against theoretical target values."""
    codes = list(dict.fromkeys(fund_codes))
    code_set = set(codes)
    exits = set(exit_fund_codes or [])
    buy_fees = buy_fees or {}
    sell_fees = sell_fees or {}
    planned_sales = planned_sales or {}
    pending_sell_amounts = pending_sell_amounts or {}
    redemption_limits = redemption_limits or {}

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
    available_existing_cash = _non_negative(
        available_existing_cash, "available_existing_cash"
    )
    existing_pending_proceeds = _non_negative(
        existing_pending_proceeds, "existing_pending_proceeds"
    )
    if available_existing_cash > cash_before + EXECUTION_EPSILON:
        raise ValueError("available_existing_cash cannot exceed current_cash")
    for mapping in (planned_sales, pending_sell_amounts, redemption_limits):
        if set(mapping) - (code_set | {"RiskFree"}):
            raise ValueError(
                "sell settings contain assets outside the execution universe"
            )

    def available_to_sell(code, holding):
        pending = _non_negative(
            pending_sell_amounts.get(code, 0), f"pending sell {code}"
        )
        if pending > holding + EXECUTION_EPSILON:
            raise ValueError(f"pending sell {code} exceeds current holding")
        limit = _non_negative(
            redemption_limits.get(code, holding), f"redemption limit {code}"
        )
        return max(0.0, min(holding - pending, limit))

    sellable = {code: available_to_sell(code, value) for code, value in current.items()}
    safe_sellable = available_to_sell("RiskFree", risk_free_before)
    cash_target = max(cash_reserve, _non_negative(target_cash, "target_cash"))
    minimum_purchase = _non_negative(min_purchase_amount, "min_purchase_amount")
    step = _non_negative(amount_step, "amount_step")
    if step <= 0:
        raise ValueError("amount_step must be positive")

    def executable_amount(value):
        units = (
            Decimal(str(max(0.0, value) + EXECUTION_EPSILON)) / Decimal(str(step))
        ).to_integral_value(rounding=ROUND_FLOOR)
        amount = float(units * Decimal(str(step)))
        return amount if amount >= minimum_purchase else 0.0

    sell_amounts = {
        code: sellable[code]
        if code in exits
        else min(
            sellable[code],
            _non_negative(planned_sales.get(code, 0), f"planned sale {code}"),
        )
        for code in codes
    }
    sell_fee_rates = {
        code: _fee_rate(sell_fees.get(code, 0.0), f"sell fee {code}") for code in codes
    }
    risk_free_sell_fee = _fee_rate(sell_fees.get("RiskFree", 0.0), "RiskFree sell fee")
    risk_free_buy_fee = _fee_rate(buy_fees.get("RiskFree", 0.0), "RiskFree buy fee")
    risk_free_sale = min(
        safe_sellable,
        _non_negative(planned_sales.get("RiskFree", 0), "planned safe sale"),
    )
    if repair_cash_reserve:
        # Sell only the amount needed to repair the reserve, prioritizing lower
        # configured fees. Proceeds remain pending unless settlement is confirmed.
        shortage = max(
            0.0,
            cash_reserve
            - cash_before
            - external_budget
            - existing_pending_proceeds
            - sum(
                _non_negative(v, f"pending sell {c}")
                * (1 - _fee_rate(sell_fees.get(c, 0), f"sell fee {c}"))
                for c, v in pending_sell_amounts.items()
            )
            - sum(sell_amounts[code] * (1 - sell_fee_rates[code]) for code in codes),
        )
        risk_free_sale = max(
            risk_free_sale, min(safe_sellable, shortage / (1 - risk_free_sell_fee))
        )
        shortage = max(0.0, shortage - risk_free_sale * (1 - risk_free_sell_fee))
        for code in sorted(codes, key=lambda item: (sell_fee_rates[item], item)):
            additional = min(
                sellable[code] - sell_amounts[code],
                shortage / (1 - sell_fee_rates[code]),
            )
            sell_amounts[code] += additional
            shortage = max(0.0, shortage - additional * (1 - sell_fee_rates[code]))
            if shortage <= EXECUTION_EPSILON:
                break
    net_sell_proceeds = {
        code: sell_amounts[code] * (1.0 - sell_fee_rates[code]) for code in codes
    }
    total_gross_sell = sum(sell_amounts.values()) + risk_free_sale
    total_net_sell = sum(net_sell_proceeds.values()) + risk_free_sale * (
        1 - risk_free_sell_fee
    )
    reusable_sales = total_net_sell if reuse_settled_sale_proceeds else 0.0

    aggregate_gap = sum(targets.values()) - sum(current.values())
    contribution_for_funds = (
        external_budget if aggregate_gap > EXECUTION_EPSILON else 0.0
    )
    requested_buy_budget = (
        contribution_for_funds + available_existing_cash + reusable_sales
    )
    reserve_liquidity = cash_before + external_budget + reusable_sales
    if can_manage_risk_free:
        reserve_liquidity += (risk_free_before - risk_free_sale) * (
            1 - risk_free_sell_fee
        )
    spendable_liquidity = max(0.0, reserve_liquidity - cash_reserve)
    max_cash_to_spend = min(requested_buy_budget, spendable_liquidity)

    post_sell_holdings = {code: current[code] - sell_amounts[code] for code in codes}
    investment_limits = dict(investment_limits or {})
    for code, pending in pending_sell_amounts.items():
        if pending > EXECUTION_EPSILON:
            investment_limits[code] = {"monthly_limit": 0}
    for code, sold in {**sell_amounts, "RiskFree": risk_free_sale}.items():
        if sold > EXECUTION_EPSILON:
            investment_limits[code] = {"monthly_limit": 0}
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
        substitute_for=substitute_for,
        planned_purchase_days=planned_purchase_days,
    )
    buy_allocations = {
        code: executable_amount(value)
        for code, value in allocation.gross_allocations.items()
    }
    substitute_codes = set(substitute_for or {})

    fund_results = {}
    for code in codes:
        gross_buy = float(buy_allocations.get(code, 0.0))
        net_buy_holding = gross_buy / (1.0 + buy_fee_rates[code])
        executable_holding = post_sell_holdings[code] + net_buy_holding
        is_substitute_buy = code in substitute_codes and gross_buy > EXECUTION_EPSILON
        allocation_state = (
            "EXIT"
            if code in exits
            else "REBALANCE"
            if planned_sales.get(code, 0) > EXECUTION_EPSILON
            and sell_amounts[code] > EXECUTION_EPSILON
            else "RESERVE_REPAIR"
            if sell_amounts[code] > EXECUTION_EPSILON
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
            execution_role="substitute" if code in substitute_codes else "strategic",
            buy_source=(
                "limit_substitute"
                if is_substitute_buy
                else "target_gap"
                if gross_buy > EXECUTION_EPSILON
                else "none"
            ),
        )

    total_gross_buy = sum(item.gross_buy for item in fund_results.values())
    cash_after = cash_before + external_budget + reusable_sales - total_gross_buy
    pending_sale_proceeds = total_net_sell - reusable_sales
    risk_free_after = risk_free_before - risk_free_sale
    risk_free_fee_cost = 0.0
    gross = 0.0
    risk_free_total_sold = risk_free_sale

    if can_manage_risk_free and cash_after < cash_target:
        redeem_amount = min(
            (cash_target - cash_after) / (1 - risk_free_sell_fee), risk_free_after
        )
        risk_free_after -= redeem_amount
        cash_after += redeem_amount * (1 - risk_free_sell_fee)
        risk_free_total_sold += redeem_amount
        total_gross_sell += redeem_amount
        total_net_sell += redeem_amount * (1 - risk_free_sell_fee)
    if target_has_risk_free and cash_after > cash_target:
        config = (investment_limits or {}).get("RiskFree")
        limit = monthly_investment_limit(
            config, timestamp, planned_purchase_days=planned_purchase_days
        )
        config_dict = (
            config.model_dump() if hasattr(config, "model_dump") else config or {}
        )
        if config_dict.get("limit_basis") == "net_asset_add":
            limit *= 1 + risk_free_buy_fee
        gross = executable_amount(
            min(
                cash_after - cash_target,
                limit,
                max(0.0, risk_free_target - risk_free_after) * (1 + risk_free_buy_fee),
            )
        )
        net = gross / (1 + risk_free_buy_fee)
        risk_free_after += net
        cash_after -= gross
        risk_free_fee_cost += gross - net

    opening_wealth = (
        sum(current.values()) + risk_free_before + cash_before + external_budget
    )
    buy_fee_cost = sum(
        item.gross_buy - item.gross_buy / (1.0 + item.buy_fee_rate)
        for item in fund_results.values()
    )
    buy_fee_cost += risk_free_fee_cost
    sell_fee_cost = total_gross_sell - total_net_sell
    closing_wealth = (
        sum(item.executable_holding for item in fund_results.values())
        + risk_free_after
        + cash_after
        + pending_sale_proceeds
    )
    accounting_error = opening_wealth - buy_fee_cost - sell_fee_cost - closing_wealth
    if abs(accounting_error) > 1e-6:
        raise RuntimeError(
            f"monthly execution accounting invariant failed: {accounting_error}"
        )

    shortfall = max(0.0, cash_reserve - cash_after)
    initial_shortfall = max(0.0, cash_reserve - reserve_liquidity)
    if (
        cash_after < -EXECUTION_EPSILON
        or shortfall > initial_shortfall + EXECUTION_EPSILON
    ):
        raise RuntimeError("monthly execution violated available cash or reserve")
    diagnostics = allocation.diagnostics.to_dict()
    rounding_remainder = sum(allocation.gross_allocations.values()) - total_gross_buy
    diagnostics["unspent_budget"] += rounding_remainder
    if rounding_remainder > EXECUTION_EPSILON:
        diagnostics["unspent_reason"] = "purchase_amount_constraints"
    diagnostics["constraint_residuals"] = {
        "accounting_error": accounting_error,
        "cash_reserve_shortfall": shortfall,
        "feasible": shortfall <= EXECUTION_EPSILON,
    }

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
        allocation_diagnostics=diagnostics,
        cash_reserve_shortfall=shortfall,
        pending_sale_proceeds=pending_sale_proceeds,
        risk_free_gross_buy=gross,
        risk_free_gross_sell=risk_free_total_sold,
        transaction_fees=buy_fee_cost + sell_fee_cost,
    )
