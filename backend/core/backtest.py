from typing import Dict

import numpy as np
import pandas as pd

from core.classification import (
    calculate_exposure_metrics,
    normalize_asset_categories,
)
from core.constants import (
    DEFAULT_CVAR_CONFIDENCE,
    DEFAULT_CVAR_LIMIT,
    DEFAULT_ESTIMATION_WINDOW,
    DEFAULT_MAX_DRAWDOWN_LIMIT,
    DEFAULT_RISK_HORIZON_DAYS,
    DEFAULT_STRATEGY_MODE,
)
from core.decision import ExecutionContext, PortfolioState, StrategySpec, plan_month
from core.domain import SubstitutionGroup
from core.ledger import AccountUnitLedger
from core.execution_optimizer import build_execution_covariance
from core.portfolio import (
    decompose_selected_weights,
    normalize_weights,
    validate_weight_universe,
)
from core.performance import performance_summary
from core.risk import calculate_max_drawdown
from core.strategy import (
    validate_strategy_params,
)


def backtest_lump_sum(
    df_nav, weights_dict, total_investment, initial_holdings=None, initial_cash=0.0
):
    if initial_holdings is None:
        initial_holdings = {}
    validate_weight_universe(weights_dict, list(df_nav.columns))
    weights = normalize_weights(weights_dict, list(df_nav.columns))
    initial_nav = df_nav.iloc[0]

    # Calculate initial shares from both new investment and existing holdings
    initial_shares = pd.Series(0.0, index=df_nav.columns)
    cash_balance = initial_cash

    for code in df_nav.columns:
        # Shares from initial holdings
        if code in initial_holdings:
            initial_shares[code] = initial_holdings[code] / initial_nav[code]
        # plus shares from new lump sum cash
        initial_shares[code] += (total_investment * weights[code]) / initial_nav[code]

    # Total capital committed at start
    initial_holdings_value = sum(initial_holdings.values())
    total_committed = total_investment + initial_holdings_value + cash_balance

    portfolio_history_values = df_nav.dot(initial_shares.T) + cash_balance
    attribution_history = df_nav.multiply(initial_shares, axis="columns")

    portfolio_series = pd.Series(portfolio_history_values)
    max_drawdown_value = calculate_max_drawdown(portfolio_series)
    max_drawdown_nav = calculate_max_drawdown(portfolio_series / total_committed)

    # Calculate Annualized Return (CAGR)
    days = (df_nav.index[-1] - df_nav.index[0]).days
    years = days / 365.25 if days > 0 else 0
    annualized_return = 0.0
    if years > 0 and total_committed > 0:
        final_value = portfolio_history_values.iloc[-1]
        if final_value > 0:
            annualized_return = (final_value / total_committed) ** (1 / years) - 1
        else:
            annualized_return = -1.0  # Lost everything

    return {
        "total_invested": total_committed,
        "final_value": portfolio_history_values.iloc[-1],
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "history": {
            date.strftime("%Y-%m"): value
            for date, value in portfolio_history_values.to_dict().items()
        },
        "attribution": {
            date.strftime("%Y-%m"): {
                **row.to_dict(),
                "Cash": cash_balance,
            }
            for date, row in attribution_history.iterrows()
        },
    }


def backtest_dca(
    df_nav, weights_dict, monthly_investment, initial_holdings=None, initial_cash=0.0
):
    if initial_holdings is None:
        initial_holdings = {}
    validate_weight_universe(weights_dict, list(df_nav.columns))
    weights = normalize_weights(weights_dict, list(df_nav.columns))
    initial_nav = df_nav.iloc[0]

    total_shares = pd.Series(0.0, index=df_nav.columns)
    # Initialize from existing holdings
    for code in df_nav.columns:
        if code in initial_holdings and initial_holdings[code] > 0:
            total_shares[code] = initial_holdings[code] / initial_nav[code]

    cash_balance = initial_cash
    initial_asset_value = sum(initial_holdings.values())
    total_invested = initial_asset_value + cash_balance

    portfolio_history = {}
    attribution_history = {}
    invested_history = {}

    # Initialize "Strategy Unit" accounting
    # We treat the strategy as a fund where new investments buy "units" of the strategy
    # This allows correctly calculating Drawdown based on Unit Value, not (Value/Invested)
    total_units = 0.0
    if total_invested > 0:
        total_units = total_invested  # Initial units at price 1.0

    unit_nav_history = {}
    external_contributions = []
    if total_invested > 0:
        external_contributions.append((df_nav.index[0], total_invested))

    for idx, (timestamp, nav_row) in enumerate(df_nav.iterrows()):
        # 1. Calculate Portfolio Value BEFORE new investment (market impact on existing assets)
        current_val_pre = (total_shares * nav_row).sum() + cash_balance

        # 2. Derive Unit NAV
        if total_units > 0:
            unit_nav = current_val_pre / total_units
        else:
            unit_nav = 1.0

        # 3. Add New Investment (Buying Strategy Units)
        total_invested += monthly_investment
        if monthly_investment > 0:
            external_contributions.append((timestamp, monthly_investment))
        if unit_nav > 0:
            new_units = monthly_investment / unit_nav
            total_units += new_units

        # 4. Execute Investment Logic (Buying Underlying Assets)
        shares_bought = (monthly_investment * weights) / nav_row
        total_shares += shares_bought

        # 5. Record State
        current_asset_values = total_shares * nav_row
        attr = current_asset_values.to_dict()
        attr["Cash"] = cash_balance
        attribution_history[timestamp] = attr
        portfolio_history[timestamp] = current_asset_values.sum() + cash_balance
        unit_nav_history[timestamp] = (
            portfolio_history[timestamp] / total_units if total_units > 0 else 1.0
        )
        invested_history[timestamp] = total_invested

    portfolio_series = pd.Series(portfolio_history)
    unit_nav_series = pd.Series(unit_nav_history)
    # invested_series = pd.Series(invested_history) # No longer used for DD

    # Calculate Max Drawdown based on Unit NAV Series (True performance)
    anchored_unit_nav = pd.concat(
        [pd.Series([1.0]), unit_nav_series.reset_index(drop=True)]
    )
    max_drawdown_nav = calculate_max_drawdown(anchored_unit_nav)
    max_drawdown_value = calculate_max_drawdown(portfolio_series)

    # Calculate Annualized Return (CAGR based on Strategy Unit NAV)
    days = (df_nav.index[-1] - df_nav.index[0]).days
    years = days / 365.25 if days > 0 else 0
    annualized_return = 0.0

    final_unit_nav = (
        float(unit_nav_series.iloc[-1]) if not unit_nav_series.empty else 1.0
    )

    if years > 0:
        if final_unit_nav > 0:
            annualized_return = (final_unit_nav) ** (1 / years) - 1
        else:
            annualized_return = -1.0

    performance = performance_summary(
        unit_nav_series, external_contributions, list(portfolio_history.values())[-1]
    )
    return {
        "total_invested": total_invested,
        "final_value": list(portfolio_history.values())[-1],
        "final_unit_nav": final_unit_nav,
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "monthly_max_drawdown": float(max_drawdown_nav),
        **performance,
        "history": {
            date.strftime("%Y-%m"): value for date, value in portfolio_history.items()
        },
        "attribution": {
            date.strftime("%Y-%m"): value for date, value in attribution_history.items()
        },
    }


def backtest_fixed_target(
    df_nav,
    weights_dict,
    monthly_investment,
    initial_holdings=None,
    buy_fee: Dict[str, float] = None,
    sell_fee: Dict[str, float] = None,
    risk_free_rate: float = 0.0,
    strategy_mode: str = DEFAULT_STRATEGY_MODE,
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW,
    minimum_cash_reserve: float = 0.0,
    enable_cvar_constraint: bool = True,
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE,
    cvar_limit: float = DEFAULT_CVAR_LIMIT,
    enable_drawdown_constraint: bool = True,
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT,
    initial_cash: float = 0.0,
    fund_investment_limits: Dict[str, object] = None,
    exit_fund_codes=None,
    reuse_settled_sale_proceeds: bool = False,
    asset_categories: Dict[str, str] = None,
    daily_nav: pd.DataFrame = None,
    risk_horizon_days: int = DEFAULT_RISK_HORIZON_DAYS,
    substitute_for: Dict[str, str] = None,
    execution_allocation_method: str = "proportional_gap",
    planning_period_days: int = 30,
    planned_purchase_days: int = None,
    estimation_nav: pd.DataFrame = None,
    daily_estimation_nav: pd.DataFrame = None,
    corporate_actions=(),
    account_distribution_policy="cash",
    contributions=None,
    rebalance_enabled=True,
    rebalance_band=0.02,
    available_existing_cash=0.0,
    redemption_limits=None,
    min_purchase_amount=0.0,
    amount_step=0.01,
):
    """Fixed-target allocation with the same execution rules as recommendations."""
    validate_strategy_params(
        strategy_mode=strategy_mode,
        estimation_window=estimation_window,
        minimum_cash_reserve=minimum_cash_reserve,
        enable_cvar_constraint=enable_cvar_constraint,
        cvar_confidence=cvar_confidence,
        cvar_limit=cvar_limit,
        enable_drawdown_constraint=enable_drawdown_constraint,
        max_drawdown_limit=max_drawdown_limit,
        risk_horizon_days=risk_horizon_days,
    )

    # Initialize holdings from initial_holdings if provided
    if initial_holdings is None:
        initial_holdings = {}
    if "RiskFree" not in df_nav.columns and initial_holdings.get("RiskFree", 0.0) > 0:
        df_nav = df_nav.copy()
        df_nav["RiskFree"] = 1.0

    selected = decompose_selected_weights(weights_dict, list(df_nav.columns))
    normalized_asset_categories = normalize_asset_categories(
        list(df_nav.columns), asset_categories
    )
    risky_weights = selected["risky_weights"]
    base_risk_free_ratio = float(selected["base_risk_free_ratio"])
    risky_columns = list(risky_weights.index)

    can_use_risk_free_asset = "RiskFree" in df_nav.columns and (
        base_risk_free_ratio > 0 or initial_holdings.get("RiskFree", 0.0) > 0
    )

    initial_nav = df_nav.iloc[0]
    total_shares = pd.Series(0.0, index=risky_columns, dtype=float)
    risk_free_shares = 0.0

    # Convert initial holdings (in currency) to shares.
    for code in risky_columns:
        if code in initial_holdings and initial_holdings[code] > 0:
            total_shares[code] = initial_holdings[code] / initial_nav[code]

    if can_use_risk_free_asset and initial_holdings.get("RiskFree", 0.0) > 0:
        risk_free_shares = initial_holdings["RiskFree"] / initial_nav["RiskFree"]

    cash_balance = initial_cash
    pending_sale_proceeds = 0.0

    initial_value = sum(initial_holdings.values()) + cash_balance
    accumulated_investment = initial_value  # Total external money put in (principal)

    portfolio_history = {}
    attribution_history = {}
    execution_history = {}
    category_attribution_history = {}
    invested_history = {}

    signal_nav = estimation_nav if estimation_nav is not None else df_nav
    ledger = AccountUnitLedger.initialize(
        {
            **total_shares.to_dict(),
            **({"RiskFree": risk_free_shares} if can_use_risk_free_asset else {}),
        },
        cash_balance,
        initial_nav.to_dict(),
    )
    regular_contribution = monthly_investment
    reusable_cash_remaining = available_existing_cash
    unit_nav_history = {}
    external_contributions = []
    if accumulated_investment > 0:
        external_contributions.append((df_nav.index[0], accumulated_investment))
    market_signal_current = "neutral"
    allocation_signal_current = "neutral"
    optimizer_info_current = None

    for idx, (timestamp, nav_row) in enumerate(df_nav.iterrows()):
        monthly_investment = (
            regular_contribution
            if contributions is None
            else float(contributions.get(timestamp, 0.0))
        )
        settled_sales = ledger.receivables.pop("sale_proceeds", 0.0)
        ledger.cash += settled_sales
        reusable_cash_remaining += settled_sales
        if idx > 0:
            ledger.process_actions(
                corporate_actions,
                df_nav.index[idx - 1],
                timestamp,
                daily_nav if daily_nav is not None else df_nav,
                account_distribution_policy,
            )
        total_shares = pd.Series(
            {code: ledger.shares.get(code, 0.0) for code in risky_columns}, dtype=float
        )
        risk_free_shares = ledger.shares.get("RiskFree", 0.0)
        cash_balance = ledger.cash
        current_risk_free_value = risk_free_shares * nav_row.get("RiskFree", 0.0)
        ledger.contribute(timestamp, monthly_investment, nav_row.to_dict())
        accumulated_investment += monthly_investment
        if monthly_investment > 0:
            external_contributions.append((timestamp, monthly_investment))

        market_signal_current = allocation_signal_current = "neutral"
        optimizer_info_current = {"policy": "fixed_weight"}

        execution = plan_month(
            columns=list(df_nav.columns),
            state=PortfolioState(
                holdings={
                    **(total_shares * nav_row[total_shares.index]).to_dict(),
                    "RiskFree": current_risk_free_value,
                },
                cash=cash_balance,
            ),
            strategy=StrategySpec("fixed-weight", weights_dict),
            context=ExecutionContext(
                monthly_budget=monthly_investment,
                rebalance_enabled=rebalance_enabled,
                rebalance_band=rebalance_band,
                available_existing_cash=min(reusable_cash_remaining, cash_balance),
                redemption_limits=redemption_limits or {},
                buy_fees=buy_fee or {},
                sell_fees=sell_fee or {},
                investment_limits=fund_investment_limits or {},
                minimum_cash_reserve=minimum_cash_reserve,
                allow_sales=bool(exit_fund_codes),
                exit_fund_codes=tuple(exit_fund_codes or ()),
                reuse_settled_sale_proceeds=reuse_settled_sale_proceeds,
                allocation_method=execution_allocation_method,
                execution_covariance=build_execution_covariance(
                    signal_nav.iloc[:idx],
                    risky_columns,
                    estimation_window=estimation_window,
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
            as_of=timestamp,
        )

        ledger.apply_execution(execution, nav_row.to_dict())
        reusable_cash_remaining = max(
            0.0,
            reusable_cash_remaining
            - max(
                0.0,
                execution.total_gross_buy
                + execution.risk_free_gross_buy
                - monthly_investment
                - execution.reused_sale_proceeds,
            ),
        )
        total_shares = pd.Series(
            {code: ledger.shares.get(code, 0.0) for code in risky_columns}, dtype=float
        )
        risk_free_shares = ledger.shares.get("RiskFree", 0.0)
        cash_balance = ledger.cash
        pending_sale_proceeds = sum(ledger.receivables.values())
        execution_history[timestamp] = {
            "total_gross_buy": execution.total_gross_buy,
            "total_gross_sell": execution.total_gross_sell,
            "total_net_sell_proceeds": execution.total_net_sell_proceeds,
            "reused_sale_proceeds": execution.reused_sale_proceeds,
            "cash_after": execution.cash_after,
            "pending_sale_proceeds": pending_sale_proceeds,
            "risk_free_after": execution.risk_free_after,
            "accounting_error": execution.accounting_error,
            "allocation_diagnostics": dict(execution.allocation_diagnostics),
            "funds": {
                code: {
                    "action": item.action,
                    "allocation_state": item.allocation_state,
                    "current_holding": item.current_holding,
                    "target_holding": item.target_holding,
                    "executable_holding": item.executable_holding,
                    "gross_buy": item.gross_buy,
                    "gross_sell": item.gross_sell,
                    "net_sell_proceeds": item.net_sell_proceeds,
                    "execution_role": item.execution_role,
                    "buy_source": item.buy_source,
                }
                for code, item in execution.funds.items()
            },
        }

        # 5. Record State
        current_asset_values = total_shares * nav_row[total_shares.index]
        attribution_dict = current_asset_values.to_dict()
        if can_use_risk_free_asset:
            attribution_dict["RiskFree"] = risk_free_shares * nav_row["RiskFree"]
        attribution_dict["Cash"] = cash_balance
        attribution_dict["PendingCash"] = pending_sale_proceeds
        attribution_history[timestamp] = attribution_dict

        total_portfolio_value = ledger.wealth(nav_row.to_dict())
        category_metrics = calculate_exposure_metrics(
            {
                code: value
                for code, value in attribution_dict.items()
                if code not in {"Cash", "PendingCash"}
            },
            total_wealth=total_portfolio_value,
            asset_categories=normalized_asset_categories,
        )
        category_attribution_history[timestamp] = {
            **category_metrics,
            "cash_value": cash_balance,
            "cash_exposure": (
                cash_balance / total_portfolio_value
                if total_portfolio_value > 0
                else 0.0
            ),
        }
        portfolio_history[timestamp] = total_portfolio_value
        unit_nav_history[timestamp] = ledger.record(timestamp, nav_row.to_dict())
        invested_history[timestamp] = accumulated_investment

    portfolio_series = pd.Series(portfolio_history)
    unit_nav_series = pd.Series(unit_nav_history)

    anchored_unit_nav = pd.concat(
        [pd.Series([1.0]), unit_nav_series.reset_index(drop=True)]
    )
    max_drawdown_nav = calculate_max_drawdown(anchored_unit_nav)
    max_drawdown_value = calculate_max_drawdown(portfolio_series)

    # Calculate Annualized Return (CAGR based on Strategy Unit NAV)
    days = (df_nav.index[-1] - df_nav.index[0]).days
    years = days / 365.25 if days > 0 else 0
    annualized_return = 0.0

    final_unit_nav = (
        float(unit_nav_series.iloc[-1]) if not unit_nav_series.empty else 1.0
    )

    if years > 0:
        if final_unit_nav > 0:
            annualized_return = (final_unit_nav) ** (1 / years) - 1
        else:
            annualized_return = -1.0

    performance = performance_summary(
        unit_nav_series, external_contributions, list(portfolio_history.values())[-1]
    )
    return {
        "total_invested": accumulated_investment,
        "final_value": list(portfolio_history.values())[-1],
        "final_unit_nav": final_unit_nav,
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "monthly_max_drawdown": float(max_drawdown_nav),
        **performance,
        "market_signal": market_signal_current,
        "allocation_signal": allocation_signal_current,
        "strategy_mode": strategy_mode,
        "optimizer_info": optimizer_info_current,
        "effective_risky_weights": risky_weights.to_dict(),
        "asset_categories": normalized_asset_categories,
        "history": {
            date.strftime("%Y-%m"): value for date, value in portfolio_history.items()
        },
        "unit_nav_history": {
            date.strftime("%Y-%m"): value for date, value in unit_nav_history.items()
        },
        "attribution": {
            date.strftime("%Y-%m"): value for date, value in attribution_history.items()
        },
        "execution": {
            date.strftime("%Y-%m"): value for date, value in execution_history.items()
        },
        "category_attribution": {
            date.strftime("%Y-%m"): value
            for date, value in category_attribution_history.items()
        },
    }


def simulate_strategy_frontier(
    frontier_points,
    nav_adjusted,
    fund_fees,
    start_date,
    end_date,
    risk_free_rate,
    buy_fee=None,
    sell_fee=None,
    strategy_mode=DEFAULT_STRATEGY_MODE,
    estimation_window=DEFAULT_ESTIMATION_WINDOW,
    minimum_cash_reserve=0.0,
    enable_cvar_constraint=True,
    cvar_confidence=DEFAULT_CVAR_CONFIDENCE,
    cvar_limit=DEFAULT_CVAR_LIMIT,
    enable_drawdown_constraint=True,
    max_drawdown_limit=DEFAULT_MAX_DRAWDOWN_LIMIT,
    initial_lump_sum=0.0,
    monthly_investment=1000.0,
    fund_investment_limits=None,
    asset_categories=None,
    daily_nav=None,
    risk_horizon_days=DEFAULT_RISK_HORIZON_DAYS,
    substitute_for=None,
    execution_allocation_method="proportional_gap",
    planning_period_days=30,
    planned_purchase_days=None,
):
    """Replay each selected fixed target through the shared executor."""
    strategy_points = []
    for pt in frontier_points:
        risk = pt["risk"]
        weights = pt["weights"]

        # Run Backtest
        # Note: nav_adjusted ALREADY has fees subtracted in analyze_portfolio
        # so we pass buy_fee/sell_fee as 0 to keep it pure to the strategy logic impact
        result = backtest_fixed_target(
            nav_adjusted,
            weights,
            monthly_investment,
            initial_holdings={
                code: initial_lump_sum * w for code, w in weights.items() if w > 0
            },
            buy_fee=buy_fee or {},
            sell_fee=sell_fee or {},
            risk_free_rate=risk_free_rate or 0.0,
            strategy_mode=strategy_mode,
            estimation_window=estimation_window,
            minimum_cash_reserve=minimum_cash_reserve,
            enable_cvar_constraint=enable_cvar_constraint,
            cvar_confidence=cvar_confidence,
            cvar_limit=cvar_limit,
            enable_drawdown_constraint=enable_drawdown_constraint,
            max_drawdown_limit=max_drawdown_limit,
            fund_investment_limits=fund_investment_limits,
            asset_categories=asset_categories,
            daily_nav=daily_nav,
            risk_horizon_days=risk_horizon_days,
            substitute_for=substitute_for,
            execution_allocation_method=execution_allocation_method,
            planning_period_days=planning_period_days,
            planned_purchase_days=planned_purchase_days,
        )

        # Strategy Return: Standard CAGR based on Strategy Unit NAV
        # Formula: FinalUnitNav ^ (1 / Years) - 1
        # Duration in years
        days = (pd.to_datetime(end_date) - pd.to_datetime(start_date)).days
        years = days / 365.25
        if years <= 0:
            years = 1

        final_nav = result.get("final_unit_nav", 1.0)
        strategy_return_annualized = (final_nav) ** (1 / years) - 1

        # 2. Strategy Risk (Drawdown? or Volatility?)
        # MV Frontier X-axis is Volatility.
        # But Strategy users care about Drawdown.
        # However, to plot on the SAME chart, X-axis must remain "Annualized Volatility"
        # OR we plot a different chart.
        # If we plot on same chart, we should calculate the Strategy's Volatility.
        # Strategy volatility should use unit NAV returns (cashflow-neutral risk).
        hist_source = result.get("unit_nav_history") or result.get("history", {})
        hist_vals = pd.Series(hist_source, dtype=float)
        if not hist_vals.empty:
            hist_vals.index = pd.to_datetime(hist_vals.index)
            hist_vals = hist_vals.sort_index()
        strat_monthly_rets = hist_vals.pct_change().dropna()
        if strat_monthly_rets.empty:
            strategy_volatility = 0.0
        else:
            strategy_volatility = float(strat_monthly_rets.std(ddof=0) * np.sqrt(12))
            if not np.isfinite(strategy_volatility):
                strategy_volatility = 0.0

        max_dd = result["max_drawdown_nav"]  # Negative float

        strategy_points.append(
            {
                "risk": strategy_volatility,
                "return": strategy_return_annualized,
                "max_drawdown": max_dd,
                "original_risk": risk,  # Link back to original point
                "weights": weights,
                "effective_risky_weights": result.get("effective_risky_weights", {}),
                "policy": "fixed_weight",
            }
        )

    return strategy_points
