from typing import Dict

import numpy as np
import pandas as pd

from core.constants import (
    DEFAULT_CVAR_CONFIDENCE,
    DEFAULT_CVAR_LIMIT,
    DEFAULT_ESTIMATION_WINDOW,
    DEFAULT_KELLY_FRACTION,
    DEFAULT_MAX_DRAWDOWN_LIMIT,
    DEFAULT_STRATEGY_MODE,
)
from core.portfolio import (
    decompose_selected_weights,
    normalize_weights,
    validate_weight_universe,
)
from core.risk import calculate_max_drawdown
from core.strategy import (
    calculate_target_ratio,
    calculate_target_ratio_optimized,
    infer_valuation_signal,
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
                **({"RiskFree": cash_balance} if "RiskFree" not in row.index else {}),
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

    for idx, (timestamp, nav_row) in enumerate(df_nav.iterrows()):
        # 1. Calculate Portfolio Value BEFORE new investment (market impact on existing assets)
        current_val_pre = (total_shares * nav_row).sum() + cash_balance

        # 2. Derive Unit NAV
        if total_units > 0:
            unit_nav = current_val_pre / total_units
        else:
            unit_nav = 1.0

        unit_nav_history[timestamp] = unit_nav

        # 3. Add New Investment (Buying Strategy Units)
        total_invested += monthly_investment
        if unit_nav > 0:
            new_units = monthly_investment / unit_nav
            total_units += new_units

        # 4. Execute Investment Logic (Buying Underlying Assets)
        shares_bought = (monthly_investment * weights) / nav_row
        total_shares += shares_bought

        # 5. Record State
        current_asset_values = total_shares * nav_row
        attr = current_asset_values.to_dict()
        if "RiskFree" not in current_asset_values.index:
            attr["RiskFree"] = cash_balance
        attr["Cash"] = cash_balance
        attribution_history[timestamp] = attr
        portfolio_history[timestamp] = current_asset_values.sum() + cash_balance
        invested_history[timestamp] = total_invested

    portfolio_series = pd.Series(portfolio_history)
    unit_nav_series = pd.Series(unit_nav_history)
    # invested_series = pd.Series(invested_history) # No longer used for DD

    # Calculate Max Drawdown based on Unit NAV Series (True performance)
    max_drawdown_nav = calculate_max_drawdown(unit_nav_series)
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

    return {
        "total_invested": total_invested,
        "final_value": list(portfolio_history.values())[-1],
        "final_unit_nav": final_unit_nav,
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "history": {
            date.strftime("%Y-%m"): value for date, value in portfolio_history.items()
        },
        "attribution": {
            date.strftime("%Y-%m"): value for date, value in attribution_history.items()
        },
    }


def backtest_kelly_dca(
    df_nav,
    weights_dict,
    monthly_investment,
    initial_holdings=None,
    max_buy_multiplier=3.0,
    sell_threshold=0.05,
    min_weight=0.3,
    max_weight=0.8,
    buy_fee: Dict[str, float] = None,
    sell_fee: Dict[str, float] = None,
    ma_window: int = 12,
    risk_free_rate: float = 0.0,
    strategy_mode: str = DEFAULT_STRATEGY_MODE,
    kelly_fraction: float = DEFAULT_KELLY_FRACTION,
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW,
    minimum_cash_reserve: float = 0.0,
    enable_cvar_constraint: bool = True,
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE,
    cvar_limit: float = DEFAULT_CVAR_LIMIT,
    enable_drawdown_constraint: bool = True,
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT,
    initial_cash: float = 0.0,
):
    """
    Advanced Value Averaging (VA) Strategy.
    Note: Sometimes referred to as "Kelly DCA" in this codebase, but technically
    it implements Value Averaging by dynamically adjusting investment based on
    market valuation (Price vs MA bias).
    """
    validate_strategy_params(
        strategy_mode=strategy_mode,
        min_weight=min_weight,
        max_weight=max_weight,
        kelly_fraction=kelly_fraction,
        estimation_window=estimation_window,
        minimum_cash_reserve=minimum_cash_reserve,
        enable_cvar_constraint=enable_cvar_constraint,
        cvar_confidence=cvar_confidence,
        cvar_limit=cvar_limit,
        enable_drawdown_constraint=enable_drawdown_constraint,
        max_drawdown_limit=max_drawdown_limit,
    )

    selected = decompose_selected_weights(weights_dict, list(df_nav.columns))
    risky_weights = selected["risky_weights"]
    base_risky_ratio = float(selected["base_risky_ratio"])
    base_risk_free_ratio = float(selected["base_risk_free_ratio"])
    risky_columns = list(risky_weights.index)
    has_risky_assets = base_risky_ratio > 0 and float(risky_weights.sum()) > 0

    # Initialize holdings from initial_holdings if provided
    if initial_holdings is None:
        initial_holdings = {}
    can_use_risk_free_asset = "RiskFree" in df_nav.columns and (
        base_risk_free_ratio > 0 or initial_holdings.get("RiskFree", 0.0) > 0
    )
    target_has_risk_free_asset = (
        "RiskFree" in df_nav.columns and base_risk_free_ratio > 0
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

    initial_value = sum(initial_holdings.values()) + cash_balance
    accumulated_investment = initial_value  # Total external money put in (principal)

    portfolio_history = {}
    attribution_history = {}
    invested_history = {}

    if has_risky_assets:
        reference_portfolio_nav = df_nav[risky_columns].dot(risky_weights)
        ma_series = reference_portfolio_nav.rolling(
            window=ma_window, min_periods=1
        ).mean()
    else:
        reference_portfolio_nav = pd.Series(1.0, index=df_nav.index, dtype=float)
        ma_series = reference_portfolio_nav.copy()

    # Unit NAV Accounting
    total_units = 0.0
    if accumulated_investment > 0:
        total_units = accumulated_investment  # Initial units at 1.0

    unit_nav_history = {}
    market_signal_current = "neutral"
    allocation_signal_current = "neutral"
    optimizer_info_current = None

    for idx, (timestamp, nav_row) in enumerate(df_nav.iterrows()):
        # --- Unit NAV Calculation Start ---
        # Calculate Wealth BEFORE new external inflow (income)
        current_equity_val_pre = (total_shares * nav_row[total_shares.index]).sum()
        current_risk_free_val_pre = (
            risk_free_shares * nav_row["RiskFree"] if can_use_risk_free_asset else 0.0
        )
        wealth_pre = current_equity_val_pre + current_risk_free_val_pre + cash_balance

        if total_units > 0:
            unit_nav = wealth_pre / total_units
        else:
            unit_nav = 1.0

        unit_nav_history[timestamp] = unit_nav
        # --- Unit NAV Calculation End ---

        # 1. Income Step (External Inflow)
        cash_balance += monthly_investment
        accumulated_investment += monthly_investment

        # Buy Strategy Units
        if unit_nav > 0:
            total_units += monthly_investment / unit_nav

        # 2. Valuation Step (Post Income)
        current_equity_value = (total_shares * nav_row[total_shares.index]).sum()
        current_risk_free_value = (
            risk_free_shares * nav_row["RiskFree"] if can_use_risk_free_asset else 0.0
        )
        total_wealth = current_equity_value + current_risk_free_value + cash_balance

        # 3. Target Ratio
        if not has_risky_assets:
            tactical_ratio = 0.0
            market_signal_current = "neutral"
            allocation_signal_current = "neutral"
            optimizer_info_current = {
                "cash_cap_ratio": 1.0,
                "cash_constrained": False,
                "max_feasible_ratio_by_cvar": 0.0,
                "max_feasible_ratio_by_drawdown": 0.0,
                "max_feasible_ratio_by_risk": 0.0,
                "constraint_applied": False,
                "constraint_binding": "cash",
            }
        elif idx == 0:
            cash_cap_ratio = (
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
            tactical_ratio = float(min(min_weight, max_weight, cash_cap_ratio))
            market_signal_current = "neutral"
            allocation_signal_current = "neutral"
            optimizer_info_current = None
        else:
            signal_timestamp = reference_portfolio_nav.index[idx - 1]
            current_price = reference_portfolio_nav.loc[signal_timestamp]
            current_ma = ma_series.loc[signal_timestamp]
            market_signal_current = infer_valuation_signal(current_price, current_ma)
            if strategy_mode == "legacy_linear":
                tactical_ratio, allocation_signal_current = calculate_target_ratio(
                    current_price, current_ma, min_weight, max_weight
                )
                optimizer_info_current = None
            else:
                (
                    tactical_ratio,
                    allocation_signal_current,
                    optimizer_info_current,
                ) = calculate_target_ratio_optimized(
                    reference_portfolio_nav=reference_portfolio_nav,
                    timestamp=signal_timestamp,
                    min_weight=min_weight,
                    max_weight=max_weight,
                    kelly_fraction=kelly_fraction,
                    estimation_window=estimation_window,
                    risk_free_rate=risk_free_rate,
                    total_wealth=total_wealth,
                    minimum_cash_reserve=minimum_cash_reserve,
                    enable_cvar_constraint=enable_cvar_constraint,
                    cvar_confidence=cvar_confidence,
                    cvar_limit=cvar_limit,
                    enable_drawdown_constraint=enable_drawdown_constraint,
                    max_drawdown_limit=max_drawdown_limit,
                )

        # 4. Rebalance Step
        final_target_risky_ratio = float(
            np.clip(base_risky_ratio * tactical_ratio, 0.0, 1.0)
        )
        target_equity_value = total_wealth * final_target_risky_ratio
        diff = target_equity_value - current_equity_value

        if diff > 0:
            # Buy Limit: Min(Gap, Cash Balance considering fees, Budget * Multiplier)
            buy_limit = monthly_investment * max_buy_multiplier

            # Pre-calculate average fee rate to prevent cash overdraft
            total_weight = risky_weights[risky_weights > 0].sum()
            avg_fee = 0.0
            if total_weight > 0:
                avg_fee = (
                    sum(
                        (buy_fee or {}).get(code, 0.0) * w
                        for code, w in risky_weights[risky_weights > 0].items()
                    )
                    / total_weight
                )

            # Calculate max buyable amount considering fees and cash reserve floor
            cash_for_buy = max(0.0, cash_balance - minimum_cash_reserve)
            if can_use_risk_free_asset:
                cash_for_buy += current_risk_free_value
            max_buyable_with_fees = (
                cash_for_buy / (1 + avg_fee) if avg_fee < 1 else cash_for_buy
            )

            buy_amount = min(diff, max_buyable_with_fees, buy_limit)

            if buy_amount > 0:
                # Buy shares and deduct fees
                total_cost_with_fees = 0.0
                for code, w in risky_weights.items():
                    if w > 0:
                        f = (buy_fee or {}).get(code, 0.0)
                        amt = buy_amount * w
                        total_cost_with_fees += amt * (1 + f)
                        total_shares[code] += amt / nav_row[code]

                if can_use_risk_free_asset and total_cost_with_fees > cash_balance:
                    redeem_needed = min(
                        total_cost_with_fees - cash_balance, current_risk_free_value
                    )
                    if redeem_needed > 0:
                        risk_free_shares -= redeem_needed / nav_row["RiskFree"]
                        cash_balance += redeem_needed
                cash_balance -= total_cost_with_fees
        elif diff < 0:
            # Sell Limit: Only if gap > threshold
            if abs(diff) > total_wealth * sell_threshold:
                sell_amount = abs(diff)
                if sell_amount > 0:
                    net_proceeds = 0.0
                    for code, w in risky_weights.items():
                        if w > 0:
                            f = (sell_fee or {}).get(code, 0.0)
                            # CRITICAL FIX: Cannot sell more than what we have (No short selling)
                            # target_amt_to_sell is sell_amount * weight
                            available_val = total_shares[code] * nav_row[code]
                            actual_amt_to_sell = min(sell_amount * w, available_val)

                            if actual_amt_to_sell > 0:
                                net_proceeds += actual_amt_to_sell * (1 - f)
                                total_shares[code] -= actual_amt_to_sell / nav_row[code]
                    cash_balance += net_proceeds

        if can_use_risk_free_asset:
            current_risk_free_value = risk_free_shares * nav_row["RiskFree"]
            non_risky_after_risky_trades = current_risk_free_value + cash_balance

            if target_has_risk_free_asset:
                target_cash_balance = min(
                    minimum_cash_reserve, non_risky_after_risky_trades
                )
                if cash_balance < target_cash_balance:
                    redeem_needed = min(
                        target_cash_balance - cash_balance, current_risk_free_value
                    )
                    if redeem_needed > 0:
                        risk_free_shares -= redeem_needed / nav_row["RiskFree"]
                        cash_balance += redeem_needed
                elif cash_balance > target_cash_balance:
                    rf_buy_amount = cash_balance - target_cash_balance
                    if rf_buy_amount > 0:
                        risk_free_shares += rf_buy_amount / nav_row["RiskFree"]
                        cash_balance -= rf_buy_amount
            elif current_risk_free_value > 0:
                risk_free_shares = 0.0
                cash_balance += current_risk_free_value

        # 5. Record State
        current_asset_values = total_shares * nav_row[total_shares.index]
        attribution_dict = current_asset_values.to_dict()
        if can_use_risk_free_asset:
            attribution_dict["RiskFree"] = risk_free_shares * nav_row["RiskFree"]
        else:
            attribution_dict["RiskFree"] = cash_balance
        attribution_dict["Cash"] = cash_balance
        attribution_history[timestamp] = attribution_dict

        total_portfolio_value = (
            current_asset_values.sum()
            + (
                risk_free_shares * nav_row["RiskFree"]
                if can_use_risk_free_asset
                else 0.0
            )
            + cash_balance
        )
        portfolio_history[timestamp] = total_portfolio_value
        invested_history[timestamp] = accumulated_investment

    portfolio_series = pd.Series(portfolio_history)
    unit_nav_series = pd.Series(unit_nav_history)

    max_drawdown_nav = calculate_max_drawdown(unit_nav_series)
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

    return {
        "total_invested": accumulated_investment,
        "final_value": list(portfolio_history.values())[-1],
        "final_unit_nav": final_unit_nav,
        "annualized_return": annualized_return,
        "max_drawdown": float(max_drawdown_nav),
        "max_drawdown_value": float(max_drawdown_value),
        "max_drawdown_nav": float(max_drawdown_nav),
        "market_signal": market_signal_current,
        "allocation_signal": allocation_signal_current,
        "strategy_mode": strategy_mode,
        "optimizer_info": optimizer_info_current,
        "effective_risky_weights": risky_weights.to_dict(),
        "history": {
            date.strftime("%Y-%m"): value for date, value in portfolio_history.items()
        },
        "unit_nav_history": {
            date.strftime("%Y-%m"): value for date, value in unit_nav_history.items()
        },
        "attribution": {
            date.strftime("%Y-%m"): value for date, value in attribution_history.items()
        },
    }


def simulate_strategy_frontier(
    frontier_points,
    nav_adjusted,
    fund_fees,
    start_date,
    end_date,
    risk_free_rate,
    ma_window=12,
    buy_fee=None,
    sell_fee=None,
    max_buy_multiplier=3.0,
    sell_threshold=0.05,
    user_min_weight=None,
    user_max_weight=None,
    strategy_mode=DEFAULT_STRATEGY_MODE,
    kelly_fraction=DEFAULT_KELLY_FRACTION,
    estimation_window=DEFAULT_ESTIMATION_WINDOW,
    minimum_cash_reserve=0.0,
    enable_cvar_constraint=True,
    cvar_confidence=DEFAULT_CVAR_CONFIDENCE,
    cvar_limit=DEFAULT_CVAR_LIMIT,
    enable_drawdown_constraint=True,
    max_drawdown_limit=DEFAULT_MAX_DRAWDOWN_LIMIT,
    initial_lump_sum=0.0,
    monthly_investment=1000.0,
):
    """
    Simulate VA/Kelly strategy for each point on the frontier to get separate 'Strategy Frontier'.
    If user_min_weight / user_max_weight are provided, use them (Manual Mode).
    Otherwise, use auto-tuning logic (Auto Mode).
    """
    strategy_points = []
    risks = [p["risk"] for p in frontier_points]
    if not risks:
        return []
    min_risk = min(risks)
    max_risk = max(risks)

    # Prepare backtest common params
    # We use user provided params to match scale

    for pt in frontier_points:
        risk = pt["risk"]
        weights = pt["weights"]

        if user_min_weight is not None and user_max_weight is not None:
            # Use user provided fixed params (Align with detailed backtest)
            min_weight = user_min_weight
            max_weight = user_max_weight
        else:
            # Auto-tune Params Logic (Aggressive)
            if max_risk > min_risk:
                risk_level = (risk - min_risk) / (max_risk - min_risk)
            else:
                risk_level = 0.5  # Default middle

            # Interpolate: Min 40->90, Max 80->100
            min_weight = 0.4 + (risk_level * 0.5)
            max_weight = 0.8 + (risk_level * 0.2)

        # Run Backtest
        # Note: nav_adjusted ALREADY has fees subtracted in analyze_portfolio
        # so we pass buy_fee/sell_fee as 0 to keep it pure to the strategy logic impact
        result = backtest_kelly_dca(
            nav_adjusted,
            weights,
            monthly_investment,
            initial_holdings={
                code: initial_lump_sum * w for code, w in weights.items() if w > 0
            },
            max_buy_multiplier=max_buy_multiplier,
            sell_threshold=sell_threshold,
            min_weight=min_weight,
            max_weight=max_weight,
            buy_fee=buy_fee or {},
            sell_fee=sell_fee or {},
            ma_window=ma_window,
            risk_free_rate=risk_free_rate or 0.0,
            strategy_mode=strategy_mode,
            kelly_fraction=kelly_fraction,
            estimation_window=estimation_window,
            minimum_cash_reserve=minimum_cash_reserve,
            enable_cvar_constraint=enable_cvar_constraint,
            cvar_confidence=cvar_confidence,
            cvar_limit=cvar_limit,
            enable_drawdown_constraint=enable_drawdown_constraint,
            max_drawdown_limit=max_drawdown_limit,
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
                "auto_params": {"min_weight": min_weight, "max_weight": max_weight},
            }
        )

    return strategy_points
