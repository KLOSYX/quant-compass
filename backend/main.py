import traceback
from datetime import date
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import requests
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.staticfiles import StaticFiles

from core.constants import (
    DEFAULT_APPLY_FUND_FEES_TO_HISTORY,
    DEFAULT_CVAR_CONFIDENCE,
    DEFAULT_CVAR_LIMIT,
    DEFAULT_ESTIMATION_WINDOW,
    DEFAULT_KELLY_FRACTION,
    DEFAULT_MAX_DRAWDOWN_LIMIT,
    DEFAULT_STRATEGY_MODE,
)
from core.data import (
    ensure_risk_free_column,
    get_fund_data,
    prepare_nav_for_analysis,
)
from core.frontier import (
    append_frontier_stability_warnings,  # noqa: F401 — re-exported for tests
    calculate_efficient_frontier,  # noqa: F401 — re-exported for tests
    calculate_frontier_walk_forward_metrics,  # noqa: F401 — re-exported for tests
)
from core.portfolio import (
    append_fee_warnings,
    decompose_selected_weights,  # noqa: F401 — re-exported for tests
    get_effective_single_weight_cap,  # noqa: F401 — re-exported for tests
    normalize_risky_weights,
)
from core.risk import (
    calculate_asset_diagnostics,
)
from core.backtest import (
    backtest_dca,
    backtest_kelly_dca,
    backtest_lump_sum,
    simulate_strategy_frontier,
)
from core.strategy import (
    calculate_max_feasible_risk_ratio,  # noqa: F401
    calculate_target_ratio,
    calculate_target_ratio_optimized,
    get_monthly_rf_return,  # noqa: F401
    infer_valuation_signal,
    validate_strategy_params,
    _infer_signal_from_bounds,  # noqa: F401
)

# Set a default timeout for all requests globally to prevent hanging
# This specifically addresses the user's request to "reduce timeout"
_original_request = requests.Session.request


def _patched_request(self, method, url, *args, **kwargs):
    if "timeout" not in kwargs:
        kwargs["timeout"] = 10  # 10 seconds timeout
    return _original_request(self, method, url, *args, **kwargs)


requests.Session.request = _patched_request

app = FastAPI()
router = APIRouter()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalysisRequest(BaseModel):
    fund_codes: List[str]
    fund_fees: Dict[str, float]
    apply_fund_fees_to_history: bool = DEFAULT_APPLY_FUND_FEES_TO_HISTORY
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    risk_free_rate: Optional[float] = None
    # Add Strategy Params to align chart with actual backtest
    max_buy_multiplier: Optional[float] = 3.0
    sell_threshold: Optional[float] = 0.05
    min_weight: Optional[float] = None  # If None, use auto-tune. If set, use fixed.
    max_weight: Optional[float] = None
    strategy_mode: str = DEFAULT_STRATEGY_MODE
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW
    minimum_cash_reserve: float = 0.0
    enable_cvar_constraint: bool = True
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE
    cvar_limit: float = DEFAULT_CVAR_LIMIT
    enable_drawdown_constraint: bool = True
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    ma_window: int = 12
    include_strategy_frontier: bool = False
    initial_lump_sum: Optional[float] = 10000.0
    monthly_investment: Optional[float] = 1000.0


class StrategyBacktestRequest(BaseModel):
    fund_codes: List[str]
    weights: Dict[str, float]
    fund_fees: Dict[str, float]
    apply_fund_fees_to_history: bool = DEFAULT_APPLY_FUND_FEES_TO_HISTORY
    start_date: date
    end_date: date
    monthly_investment: float
    risk_free_rate: Optional[float] = None
    initial_holdings: Dict[str, float] = {}
    initial_cash: float = 0.0
    max_buy_multiplier: float = 3.0
    sell_threshold: float = 0.05
    min_weight: float = 0.3
    max_weight: float = 0.8
    strategy_mode: str = DEFAULT_STRATEGY_MODE
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW
    minimum_cash_reserve: float = 0.0
    enable_cvar_constraint: bool = True
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE
    cvar_limit: float = DEFAULT_CVAR_LIMIT
    enable_drawdown_constraint: bool = True
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    ma_window: int = 12


class CurrentRecommendationRequest(BaseModel):
    fund_codes: List[str]
    fund_fees: Dict[str, float] = {}
    apply_fund_fees_to_history: bool = DEFAULT_APPLY_FUND_FEES_TO_HISTORY
    weights: Dict[str, float]
    current_holdings: Dict[str, float] = {}
    current_cash: float = 0.0
    monthly_budget: float
    risk_free_rate: Optional[float] = 0.0
    max_buy_multiplier: float = 3.0
    sell_threshold: float = 0.05
    min_weight: float = 0.3
    max_weight: float = 0.8
    strategy_mode: str = DEFAULT_STRATEGY_MODE
    kelly_fraction: float = DEFAULT_KELLY_FRACTION
    estimation_window: int = DEFAULT_ESTIMATION_WINDOW
    minimum_cash_reserve: float = 0.0
    enable_cvar_constraint: bool = True
    cvar_confidence: float = DEFAULT_CVAR_CONFIDENCE
    cvar_limit: float = DEFAULT_CVAR_LIMIT
    enable_drawdown_constraint: bool = True
    max_drawdown_limit: float = DEFAULT_MAX_DRAWDOWN_LIMIT
    buy_fee: Dict[str, float] = {}
    sell_fee: Dict[str, float] = {}
    ma_window: int = 12


@router.post("/analyze")
async def analyze_portfolio(request: AnalysisRequest):
    if not request.fund_codes and request.risk_free_rate is None:
        raise HTTPException(
            status_code=400, detail="请至少选择一只基金或添加无风险资产。"
        )
    try:
        validate_strategy_params(
            strategy_mode=request.strategy_mode,
            min_weight=request.min_weight,
            max_weight=request.max_weight,
            kelly_fraction=request.kelly_fraction,
            estimation_window=request.estimation_window,
            minimum_cash_reserve=request.minimum_cash_reserve,
            enable_cvar_constraint=request.enable_cvar_constraint,
            cvar_confidence=request.cvar_confidence,
            cvar_limit=request.cvar_limit,
            enable_drawdown_constraint=request.enable_drawdown_constraint,
            max_drawdown_limit=request.max_drawdown_limit,
            allow_auto_bounds=True,
        )

        fund_df, fund_names, warnings = get_fund_data(
            request.fund_codes,
            request.start_date,
            request.end_date,
            request.risk_free_rate,
        )
        append_fee_warnings(
            warnings,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )
        append_frontier_stability_warnings(warnings, fund_df, request.fund_fees)
        efficient_frontier_points = calculate_efficient_frontier(
            fund_df, request.fund_fees
        )
        asset_diagnostics = calculate_asset_diagnostics(
            fund_df, fund_names, efficient_frontier_points
        )
        walk_forward_metrics = calculate_frontier_walk_forward_metrics(
            fund_df, request.fund_fees
        )
        for point, metric in zip(efficient_frontier_points, walk_forward_metrics):
            point["effective_risky_weights"] = normalize_risky_weights(
                point["weights"], list(fund_df.columns)
            ).to_dict()
            point.update(metric)

        recommended_point_index = None
        scored_points = [
            (idx, point.get("robust_score"))
            for idx, point in enumerate(efficient_frontier_points)
            if point.get("robust_score") is not None
        ]
        if scored_points:
            recommended_point_index = max(scored_points, key=lambda item: item[1])[0]

        # --- Simulate Strategy Frontier ---
        nav_adjusted = prepare_nav_for_analysis(
            fund_df,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )

        start_date_str = fund_df.index.min().strftime("%Y-%m-%d")
        end_date_str = fund_df.index.max().strftime("%Y-%m-%d")

        strategy_frontier_points = []
        if request.include_strategy_frontier:
            strategy_frontier_points = simulate_strategy_frontier(
                efficient_frontier_points,
                nav_adjusted,
                request.fund_fees,
                start_date_str,
                end_date_str,
                request.risk_free_rate,
                ma_window=request.ma_window,
                buy_fee=request.buy_fee,
                sell_fee=request.sell_fee,
                max_buy_multiplier=request.max_buy_multiplier,
                sell_threshold=request.sell_threshold,
                user_min_weight=request.min_weight,
                user_max_weight=request.max_weight,
                strategy_mode=request.strategy_mode,
                kelly_fraction=request.kelly_fraction,
                estimation_window=request.estimation_window,
                minimum_cash_reserve=request.minimum_cash_reserve,
                enable_cvar_constraint=request.enable_cvar_constraint,
                cvar_confidence=request.cvar_confidence,
                cvar_limit=request.cvar_limit,
                enable_drawdown_constraint=request.enable_drawdown_constraint,
                max_drawdown_limit=request.max_drawdown_limit,
                initial_lump_sum=request.initial_lump_sum or 0.0,
                monthly_investment=request.monthly_investment or 1000.0,
            )

        return {
            "efficient_frontier": efficient_frontier_points,
            "strategy_frontier": strategy_frontier_points,
            "recommended_point_index": recommended_point_index,
            "fund_names": fund_names,
            "asset_diagnostics": asset_diagnostics,
            "backtest_period": {
                "start_date": start_date_str,
                "end_date": end_date_str,
            },
            "warnings": warnings,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/current_recommendation")
async def get_current_recommendation(request: CurrentRecommendationRequest):
    """Calculate current investment recommendation based on latest market data."""
    try:
        validate_strategy_params(
            strategy_mode=request.strategy_mode,
            min_weight=request.min_weight,
            max_weight=request.max_weight,
            kelly_fraction=request.kelly_fraction,
            estimation_window=request.estimation_window,
            minimum_cash_reserve=request.minimum_cash_reserve,
            enable_cvar_constraint=request.enable_cvar_constraint,
            cvar_confidence=request.cvar_confidence,
            cvar_limit=request.cvar_limit,
            enable_drawdown_constraint=request.enable_drawdown_constraint,
            max_drawdown_limit=request.max_drawdown_limit,
        )

        # Fetch latest fund data (last 12 months for MA calculation)
        end_date_obj = date.today()
        start_date_obj = date(
            end_date_obj.year - 2, end_date_obj.month, end_date_obj.day
        )

        fund_df, fund_names, _ = get_fund_data(
            request.fund_codes,
            start_date_obj,
            end_date_obj,
            request.risk_free_rate,
        )
        fund_df, fund_names = ensure_risk_free_column(
            fund_df,
            fund_names,
            weights_dict=request.weights,
            holdings_dict=request.current_holdings,
        )
        selected = decompose_selected_weights(request.weights, list(fund_df.columns))
        risky_weights = selected["risky_weights"]
        base_risky_ratio = float(selected["base_risky_ratio"])
        adjusted_fund_df = prepare_nav_for_analysis(
            fund_df,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )
        has_risky_assets = base_risky_ratio > 0 and float(risky_weights.sum()) > 0

        # Kelly/VA strategy treats cash as the risk-free sleeve outside the risky basket.
        if has_risky_assets:
            reference_portfolio_nav = adjusted_fund_df[risky_weights.index].dot(
                risky_weights
            )
        else:
            reference_portfolio_nav = pd.Series(1.0, index=fund_df.index, dtype=float)

        # Calculate moving average based on ma_window
        ma_window = request.ma_window
        ma_series = reference_portfolio_nav.rolling(
            window=ma_window, min_periods=1
        ).mean()

        if reference_portfolio_nav.empty:
            raise HTTPException(
                status_code=400, detail="Insufficient data for calculation"
            )

        # Get current (latest) values
        current_price = reference_portfolio_nav.iloc[-1]
        current_ma = ma_series.iloc[-1]
        latest_nav = float(reference_portfolio_nav.iloc[-1])
        current_price = latest_nav
        current_ma = float(ma_series.iloc[-1])

        # Calculate current equity value
        # Handle 'RiskFree' if passed (filter it out for equity calc)
        equity_holdings = {
            k: v for k, v in request.current_holdings.items() if k != "RiskFree"
        }
        current_equity_value = sum(equity_holdings.values())
        risk_free_balance = request.current_holdings.get("RiskFree", 0.0)
        current_cash = request.current_cash
        base_risk_free_ratio = float(selected["base_risk_free_ratio"])
        can_use_risk_free_asset = "RiskFree" in fund_df.columns and (
            base_risk_free_ratio > 0 or risk_free_balance > 0
        )
        target_has_risk_free_asset = (
            "RiskFree" in fund_df.columns and base_risk_free_ratio > 0
        )

        # Calculate total wealth projected (Equity + RiskFree + Current Cash + New Budget)
        total_wealth_projected = (
            current_equity_value
            + risk_free_balance
            + current_cash
            + request.monthly_budget
        )

        market_signal = infer_valuation_signal(current_price, current_ma)
        if not has_risky_assets:
            tactical_ratio = 0.0
            allocation_signal = "neutral"
            optimizer_info = {
                "cash_cap_ratio": 1.0,
                "cash_constrained": False,
                "max_feasible_ratio_by_cvar": 0.0,
                "max_feasible_ratio_by_drawdown": 0.0,
                "max_feasible_ratio_by_risk": 0.0,
                "constraint_applied": False,
                "constraint_binding": "cash",
            }
            market_signal = "neutral"
        elif request.strategy_mode == "legacy_linear":
            tactical_ratio, allocation_signal = calculate_target_ratio(
                current_price, current_ma, request.min_weight, request.max_weight
            )
            optimizer_info = None
        else:
            (
                tactical_ratio,
                allocation_signal,
                optimizer_info,
            ) = calculate_target_ratio_optimized(
                reference_portfolio_nav=reference_portfolio_nav,
                timestamp=reference_portfolio_nav.index[-1],
                min_weight=request.min_weight,
                max_weight=request.max_weight,
                kelly_fraction=request.kelly_fraction,
                estimation_window=request.estimation_window,
                risk_free_rate=request.risk_free_rate or 0.0,
                total_wealth=total_wealth_projected,
                minimum_cash_reserve=request.minimum_cash_reserve,
                enable_cvar_constraint=request.enable_cvar_constraint,
                cvar_confidence=request.cvar_confidence,
                cvar_limit=request.cvar_limit,
                enable_drawdown_constraint=request.enable_drawdown_constraint,
                max_drawdown_limit=request.max_drawdown_limit,
            )

        # Calculate target equity value
        target_equity_ratio = float(
            np.clip(base_risky_ratio * tactical_ratio, 0.0, 1.0)
        )
        target_equity_value = total_wealth_projected * target_equity_ratio
        non_risky_target_value = max(0.0, total_wealth_projected - target_equity_value)
        if target_has_risk_free_asset:
            target_cash_value = min(
                request.minimum_cash_reserve, non_risky_target_value
            )
            target_risk_free_value = max(
                0.0, non_risky_target_value - target_cash_value
            )
        else:
            target_cash_value = non_risky_target_value
            target_risk_free_value = 0.0

        # Calculate gap
        gap = target_equity_value - current_equity_value

        # Calculate recommended investment with Limits
        recommended_monthly_investment = 0.0

        if gap > 0:
            # Calculate available cash including new budget
            available_cash = max(
                0.0,
                (risk_free_balance if can_use_risk_free_asset else 0.0)
                + current_cash
                + request.monthly_budget
                - target_cash_value,
            )

            # Pre-calculate average buy fee rate based on effective risky weights
            total_weight = risky_weights.sum()
            avg_fee = 0.0
            if total_weight > 0:
                avg_fee = (
                    sum(
                        request.buy_fee.get(code, 0.0) * w
                        for code, w in risky_weights.items()
                    )
                    / total_weight
                )

            # Apply triple constraint: Gap (converted to gross), Budget Limit, Available Cash
            # gap is the NAV gap. To fill it, we need gap * (1 + avg_fee) cash.
            gross_gap = gap * (1 + avg_fee)
            limit = request.monthly_budget * request.max_buy_multiplier

            # recommended_monthly_investment is now the TOTAL CASH to be spent (Gross)
            recommended_monthly_investment = min(gross_gap, limit, available_cash)

        recommended_monthly_investment = max(0, recommended_monthly_investment)

        # Calculate Detailed Fund Advice
        fund_advice = []
        total_weight = risky_weights.sum()
        positive_gap_sum = 0
        fund_gaps = {}

        # 1. Distribute Gap Calculation
        # Use a list of dict keys to ensure uniqueness if codes were somehow repeated
        unique_fund_codes = list(dict.fromkeys(request.fund_codes))
        for code in unique_fund_codes:
            w = risky_weights.get(code, 0.0) / total_weight if total_weight > 0 else 0
            current_val = equity_holdings.get(code, 0)
            target_val = target_equity_value * w
            gap_val = target_val - current_val
            fund_gaps[code] = gap_val
            if gap_val > 0:
                positive_gap_sum += gap_val

        # 2. Determine Actions
        for code in unique_fund_codes:
            current_val = equity_holdings.get(code, 0)
            gap_val = fund_gaps.get(code, 0)
            target_val = current_val + gap_val
            sell_threshold_value = total_wealth_projected * request.sell_threshold

            # Reset action and amount for each fund to prevent leakage
            # Using a more robust if-elif-else structure
            action = "Hold"
            amount = 0.0
            reason = "持有"

            if gap_val < 0:
                # Sells must be evaluated per fund, even when the portfolio is net-buying elsewhere.
                # Otherwise zero-target assets can get stuck as "Hold".
                if target_val <= 1e-9:
                    action = "Sell"
                    amount = abs(gap_val)
                    reason = "目标仓位为0，建议卖出"
                elif abs(gap_val) > sell_threshold_value:
                    action = "Sell"
                    amount = abs(gap_val)
                    reason = f"严重高估触发减持 (Gap: {gap_val:.1f})"
                else:
                    action = "Hold"
                    reason = "未达卖出阈值"
            elif gap_val > 0:
                if recommended_monthly_investment > 0 and positive_gap_sum > 0:
                    action = "Buy"
                    # amount is the Gross Cash to spend on this fund
                    amount = recommended_monthly_investment * (
                        gap_val / positive_gap_sum
                    )
                    reason = f"价值平均补足缺口 (Gap: {gap_val:.1f})"
                else:
                    action = "Hold"
                    reason = "低位观察不额外定投"
            else:
                action = "Hold"
                reason = "仓位已达标"

            # target_val is already calculated as the ideal target (current + gap)

            fund_advice.append(
                {
                    "code": code,
                    "name": fund_names.get(code, code),
                    "current_holding": current_val,
                    "target_holding": target_val,  # Ideal Target (Current + Gap)
                    "ideal_holding": target_val,  # This is Ideal Target (Current + Gap)
                    "gap": gap_val,
                    "action": action,
                    "amount": amount,
                    "reason": reason,
                }
            )

        # 3. Simulate non-risky sleeve after risky-fund trades.
        total_buy_with_fees = 0.0
        for item in fund_advice:
            if item["action"] == "Buy":
                total_buy_with_fees += item["amount"]

        total_sell_net_proceeds = 0.0
        for item in fund_advice:
            if item["action"] == "Sell":
                f = request.sell_fee.get(item["code"], 0.0)
                total_sell_net_proceeds += item["amount"] * (1 - f)

        cash_after_risky = (
            current_cash
            + request.monthly_budget
            + total_sell_net_proceeds
            - total_buy_with_fees
        )
        risk_free_after_trades = risk_free_balance

        if can_use_risk_free_asset and cash_after_risky < target_cash_value:
            redeem_needed = min(
                target_cash_value - cash_after_risky, risk_free_after_trades
            )
            if redeem_needed > 0:
                risk_free_after_trades -= redeem_needed
                cash_after_risky += redeem_needed

        if target_has_risk_free_asset and cash_after_risky > target_cash_value:
            buy_risk_free_amount = min(
                max(0.0, target_risk_free_value - risk_free_after_trades),
                cash_after_risky - target_cash_value,
            )
            if buy_risk_free_amount > 0:
                risk_free_after_trades += buy_risk_free_amount
                cash_after_risky -= buy_risk_free_amount

        if can_use_risk_free_asset:
            risk_free_trade_amount = abs(risk_free_after_trades - risk_free_balance)
            if risk_free_after_trades > risk_free_balance + 1e-9:
                risk_free_action = "Buy"
                risk_free_reason = "剩余流动性转入货基"
            elif risk_free_after_trades + 1e-9 < risk_free_balance:
                risk_free_action = "Sell"
                risk_free_reason = "赎回货基补充调仓资金"
            else:
                risk_free_action = "Hold"
                risk_free_reason = "非风险仓位平衡"

            fund_advice.append(
                {
                    "code": "RiskFree",
                    "name": "无风险资产 (货基)",
                    "current_holding": risk_free_balance,
                    "target_holding": target_risk_free_value,
                    "ideal_holding": target_risk_free_value,
                    "gap": target_risk_free_value - risk_free_balance,
                    "action": risk_free_action,
                    "amount": risk_free_trade_amount,
                    "reason": risk_free_reason,
                }
            )

        cash_trade_amount = abs(cash_after_risky - current_cash)
        if cash_after_risky > current_cash + 1e-9:
            cash_action = "存入"
            cash_reason = "保留调仓现金"
        elif cash_after_risky + 1e-9 < current_cash:
            cash_action = "取用"
            cash_reason = "调仓使用现金"
        else:
            cash_action = "持有"
            cash_reason = "现金余额平衡"

        fund_advice.append(
            {
                "code": "Cash",
                "name": "现金",
                "current_holding": current_cash,
                "target_holding": target_cash_value,
                "ideal_holding": target_cash_value,
                "gap": target_cash_value - current_cash,
                "action": cash_action,
                "amount": cash_trade_amount,
                "reason": cash_reason,
            }
        )

        return {
            "market_signal": market_signal,
            "allocation_signal": allocation_signal,
            "target_equity_ratio": target_equity_ratio,
            "current_equity_value": current_equity_value,
            "current_risk_free_value": risk_free_balance,
            "current_cash": current_cash,
            "target_equity_value": target_equity_value,
            "target_risk_free_value": target_risk_free_value,
            "target_cash_value": target_cash_value,
            "gap": gap,
            "recommended_monthly_investment": recommended_monthly_investment,
            "monthly_budget": request.monthly_budget,
            "latest_nav": latest_nav,
            "ma_value": float(current_ma),
            "current_price": float(current_price),
            "strategy_mode": request.strategy_mode,
            "optimizer_info": optimizer_info,
            "effective_risky_weights": risky_weights.to_dict(),
            "warnings": [
                (
                    "历史信号已按输入管理费额外扣减；若原始净值已是费后净值，这会偏保守。"
                    if request.apply_fund_fees_to_history
                    else "历史信号未额外扣减管理费，以避免对基金单位净值重复扣费。"
                )
            ]
            if any(abs(fee) > 1e-12 for fee in request.fund_fees.values())
            else [],
            "fund_names": fund_names,
            "fund_advice": fund_advice,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backtest_strategies")
async def run_strategy_backtests(request: StrategyBacktestRequest):
    try:
        validate_strategy_params(
            strategy_mode=request.strategy_mode,
            min_weight=request.min_weight,
            max_weight=request.max_weight,
            kelly_fraction=request.kelly_fraction,
            estimation_window=request.estimation_window,
            minimum_cash_reserve=request.minimum_cash_reserve,
            enable_cvar_constraint=request.enable_cvar_constraint,
            cvar_confidence=request.cvar_confidence,
            cvar_limit=request.cvar_limit,
            enable_drawdown_constraint=request.enable_drawdown_constraint,
            max_drawdown_limit=request.max_drawdown_limit,
        )

        fund_df, _, _ = get_fund_data(
            request.fund_codes,
            request.start_date,
            request.end_date,
            request.risk_free_rate,
        )
        fund_df, _ = ensure_risk_free_column(
            fund_df,
            {},
            weights_dict=request.weights,
            holdings_dict=request.initial_holdings,
        )

        nav_adjusted = prepare_nav_for_analysis(
            fund_df,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )

        num_months = len(fund_df)
        total_lump_sum_investment = request.monthly_investment * num_months
        lump_sum_results = backtest_lump_sum(
            nav_adjusted,
            request.weights,
            total_lump_sum_investment,
            initial_holdings=request.initial_holdings,
            initial_cash=request.initial_cash,
        )

        dca_results = backtest_dca(
            nav_adjusted,
            request.weights,
            request.monthly_investment,
            initial_holdings=request.initial_holdings,
            initial_cash=request.initial_cash,
        )

        kelly_results = backtest_kelly_dca(
            nav_adjusted,
            request.weights,
            request.monthly_investment,
            request.initial_holdings,
            request.max_buy_multiplier,
            request.sell_threshold,
            request.min_weight,
            request.max_weight,
            request.buy_fee,
            request.sell_fee,
            request.ma_window,
            risk_free_rate=request.risk_free_rate or 0.0,
            strategy_mode=request.strategy_mode,
            kelly_fraction=request.kelly_fraction,
            estimation_window=request.estimation_window,
            minimum_cash_reserve=request.minimum_cash_reserve,
            enable_cvar_constraint=request.enable_cvar_constraint,
            cvar_confidence=request.cvar_confidence,
            cvar_limit=request.cvar_limit,
            enable_drawdown_constraint=request.enable_drawdown_constraint,
            max_drawdown_limit=request.max_drawdown_limit,
            initial_cash=request.initial_cash,
        )

        return {
            "lump_sum": lump_sum_results,
            "dca": dca_results,
            "kelly_dca": kelly_results,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


app.include_router(router, prefix="/api")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
