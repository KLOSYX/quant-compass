import traceback
from datetime import date

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException

from api.models import (
    AnalysisRequest,
    CurrentRecommendationRequest,
    FundNamesRequest,
    StrategyBacktestRequest,
)
from core.classification import (
    calculate_exposure_metrics,
    normalize_asset_categories,
)
from core.backtest import (
    backtest_dca,
    backtest_kelly_dca,
    backtest_lump_sum,
    simulate_strategy_frontier,
)
from core.data import (
    ensure_risk_free_column,
    get_fund_data,
    get_fund_names,
    prepare_nav_for_analysis,
)
from core.execution import execute_monthly_plan
from core.frontier import (
    append_frontier_stability_warnings,
    calculate_efficient_frontier,
    calculate_frontier_walk_forward_metrics,
    evaluate_covariance_shrinkage_ablation,
)
from core.limits import get_monthly_investment_limits
from core.portfolio import (
    append_fee_warnings,
    decompose_selected_weights,
    normalize_risky_weights,
)
from core.risk import calculate_asset_diagnostics
from core.strategy import (
    assess_kelly_window_robustness,
    calculate_target_ratio,
    calculate_target_ratio_optimized,
    infer_valuation_signal,
    validate_strategy_params,
)
from core.walk_forward import evaluate_executable_walk_forward

router = APIRouter()


@router.post("/fund_names")
async def resolve_fund_names(request: FundNamesRequest):
    return {"fund_names": get_fund_names(request.fund_codes)}


def _reject_implicit_legacy_risk_free(
    *,
    weights: dict[str, float],
    holdings: dict[str, float],
    risk_free_rate: float | None,
) -> None:
    legacy_holding = float(holdings.get("RiskFree", 0.0))
    explicit_weight = float(weights.get("RiskFree", 0.0))
    if legacy_holding > 1e-12 and explicit_weight <= 1e-12 and risk_free_rate is None:
        raise HTTPException(
            status_code=400,
            detail="检测到旧版合成资产 RiskFree 持仓。请删除该旧持仓，并使用实际货币基金代码。",
        )


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
            risk_horizon_days=request.risk_horizon_days,
        )

        fund_df, fund_names, warnings = get_fund_data(
            request.fund_codes,
            request.start_date,
            request.end_date,
            request.risk_free_rate,
        )
        daily_nav = fund_df.attrs.get("daily_nav")
        append_fee_warnings(
            warnings,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )
        nav_adjusted = prepare_nav_for_analysis(
            fund_df,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )
        append_frontier_stability_warnings(warnings, nav_adjusted, request.fund_fees)
        asset_categories = normalize_asset_categories(
            list(fund_df.columns), request.asset_categories
        )
        efficient_frontier_points = calculate_efficient_frontier(
            nav_adjusted, request.fund_fees
        )
        asset_diagnostics = calculate_asset_diagnostics(
            nav_adjusted, fund_names, efficient_frontier_points
        )
        walk_forward_metrics = calculate_frontier_walk_forward_metrics(
            nav_adjusted,
            request.fund_fees,
            cvar_confidence=request.cvar_confidence,
        )
        covariance_ablation = evaluate_covariance_shrinkage_ablation(nav_adjusted)
        for point, metric in zip(efficient_frontier_points, walk_forward_metrics):
            point["effective_risky_weights"] = normalize_risky_weights(
                point["weights"], list(fund_df.columns)
            ).to_dict()
            point["category_weights"] = calculate_exposure_metrics(
                point["weights"],
                total_wealth=1.0,
                asset_categories=asset_categories,
            )["category_values"]
            point.update(metric)

        recommended_point_index = None
        recommendation_candidates = [
            (idx, point)
            for idx, point in enumerate(efficient_frontier_points)
            if (point.get("frontier_walk_forward_observations") or 0) >= 12
            and (point.get("frontier_walk_forward_max_drawdown") or 0.0)
            <= request.max_drawdown_limit
            and (
                not request.enable_cvar_constraint
                or (point.get("frontier_walk_forward_cvar_loss") or 0.0)
                <= request.cvar_limit
            )
            and (point.get("frontier_walk_forward_weight_stability") or 0.0) >= 0.5
        ]
        if recommendation_candidates:
            recommended_point_index = max(
                recommendation_candidates,
                key=lambda item: (
                    (
                        item[1]["frontier_walk_forward_sharpe"]
                        if item[1].get("frontier_walk_forward_sharpe") is not None
                        else float("-inf")
                    ),
                    item[1].get("frontier_walk_forward_weight_stability") or 0.0,
                    -item[1]["risk"],
                ),
            )[0]
        else:
            warnings.append(
                "没有基础前沿点同时满足样本外观察数、最大回撤和权重稳定性要求；本次不自动选择推荐点。"
            )

        # --- Simulate Strategy Frontier ---
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
                fund_investment_limits=request.fund_investment_limits,
                asset_categories=asset_categories,
                daily_nav=daily_nav,
                risk_horizon_days=request.risk_horizon_days,
            )

        return {
            "efficient_frontier": efficient_frontier_points,
            "strategy_frontier": strategy_frontier_points,
            "recommended_point_index": recommended_point_index,
            "recommended_point_selection": {
                "minimum_observations": 12,
                "maximum_drawdown": request.max_drawdown_limit,
                "cvar_enabled": request.enable_cvar_constraint,
                "cvar_confidence": request.cvar_confidence,
                "maximum_cvar_loss": request.cvar_limit,
                "minimum_weight_stability": 0.5,
                "ranking": ["oos_sharpe", "weight_stability", "lower_risk"],
            },
            "fund_names": fund_names,
            "asset_categories": asset_categories,
            "asset_diagnostics": asset_diagnostics,
            "covariance_ablation": covariance_ablation,
            "backtest_period": {
                "start_date": start_date_str,
                "end_date": end_date_str,
            },
            "warnings": warnings,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/current_recommendation")
async def get_current_recommendation(request: CurrentRecommendationRequest):
    """Calculate current investment recommendation based on latest market data."""
    try:
        _reject_implicit_legacy_risk_free(
            weights=request.weights,
            holdings=request.current_holdings,
            risk_free_rate=request.risk_free_rate,
        )
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
            risk_horizon_days=request.risk_horizon_days,
        )

        # Fetch enough history for 60-month Kelly and non-overlapping CVaR diagnostics.
        end_date_obj = date.today()
        start_date_obj = (pd.Timestamp(end_date_obj) - pd.DateOffset(years=10)).date()

        fund_df, fund_names, _ = get_fund_data(
            request.fund_codes,
            start_date_obj,
            end_date_obj,
            request.risk_free_rate,
        )
        daily_nav = fund_df.attrs.get("daily_nav")
        fund_df, fund_names = ensure_risk_free_column(
            fund_df,
            fund_names,
            weights_dict=request.weights,
            holdings_dict=request.current_holdings,
        )
        asset_categories = normalize_asset_categories(
            list(fund_df.columns), request.asset_categories
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

        # Kelly+DCA strategy treats cash as the risk-free sleeve outside the risky basket.
        if has_risky_assets:
            reference_portfolio_nav = adjusted_fund_df[risky_weights.index].dot(
                risky_weights
            )
            daily_reference_portfolio_nav = (
                daily_nav[risky_weights.index].dot(risky_weights)
                if daily_nav is not None
                and set(risky_weights.index).issubset(daily_nav.columns)
                else None
            )
        else:
            reference_portfolio_nav = pd.Series(1.0, index=fund_df.index, dtype=float)
            daily_reference_portfolio_nav = None

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

        holding_codes = {
            code
            for code, value in request.current_holdings.items()
            if code != "RiskFree" and float(value) > 1e-12
        }
        unknown_holdings = sorted(holding_codes - set(fund_df.columns))
        if unknown_holdings:
            raise HTTPException(
                status_code=400,
                detail=(
                    "current_holdings contain assets outside the current analysis universe: "
                    + ", ".join(unknown_holdings)
                ),
            )

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
                daily_reference_nav=daily_reference_portfolio_nav,
                risk_horizon_days=request.risk_horizon_days,
            )

        if has_risky_assets and request.strategy_mode != "legacy_linear":
            window_robustness = assess_kelly_window_robustness(
                reference_portfolio_nav=reference_portfolio_nav,
                min_weight=request.min_weight,
                max_weight=request.max_weight,
                kelly_fraction=request.kelly_fraction,
                risk_free_rate=request.risk_free_rate or 0.0,
                total_wealth=total_wealth_projected,
                minimum_cash_reserve=request.minimum_cash_reserve,
                enable_cvar_constraint=request.enable_cvar_constraint,
                cvar_confidence=request.cvar_confidence,
                cvar_limit=request.cvar_limit,
                enable_drawdown_constraint=request.enable_drawdown_constraint,
                max_drawdown_limit=request.max_drawdown_limit,
                daily_reference_nav=daily_reference_portfolio_nav,
                risk_horizon_days=request.risk_horizon_days,
            )
        else:
            window_robustness = {
                "status": "not_applicable",
                "base_window_months": 36,
                "measurements": [],
                "message": "窗口稳健性评估仅适用于优化 Kelly 模式。",
            }
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

        # Kelly selects the target mix, while DCA fixes this period's external
        # contribution. Existing Cash and RiskFree are not consumed to chase a
        # target, and max_buy_multiplier is retained only for API compatibility.
        # Calculate Detailed Fund Advice
        fund_advice = []
        total_weight = risky_weights.sum()
        positive_gap_sum = 0
        fund_gaps = {}

        # 1. Distribute Gap Calculation
        # Use a list of dict keys to ensure uniqueness if codes were somehow repeated
        unique_fund_codes = list(dict.fromkeys(request.fund_codes))
        target_holdings = {}
        for code in unique_fund_codes:
            w = risky_weights.get(code, 0.0) / total_weight if total_weight > 0 else 0
            current_val = equity_holdings.get(code, 0)
            target_val = target_equity_value * w
            target_holdings[code] = target_val
            gap_val = target_val - current_val
            fund_gaps[code] = gap_val
            if gap_val > 0:
                positive_gap_sum += gap_val

        fund_monthly_limits = get_monthly_investment_limits(
            unique_fund_codes,
            request.fund_investment_limits,
            end_date_obj,
        )
        execution = execute_monthly_plan(
            fund_codes=unique_fund_codes,
            current_holdings=equity_holdings,
            target_holdings=target_holdings,
            target_weights=risky_weights.to_dict(),
            current_cash=current_cash,
            monthly_budget=request.monthly_budget,
            buy_fees=request.buy_fee,
            sell_fees=request.sell_fee,
            investment_limits=request.fund_investment_limits,
            timestamp=end_date_obj,
            minimum_cash_reserve=request.minimum_cash_reserve,
            exit_fund_codes=request.exit_fund_codes,
            reuse_settled_sale_proceeds=request.reuse_settled_sale_proceeds,
            current_risk_free=risk_free_balance,
            target_risk_free=target_risk_free_value,
            target_cash=target_cash_value,
            can_manage_risk_free=can_use_risk_free_asset,
            target_has_risk_free=target_has_risk_free_asset,
        )
        recommended_monthly_investment = execution.total_gross_buy

        # 2. Determine Actions
        for code in unique_fund_codes:
            fund_execution = execution.funds[code]
            current_val = fund_execution.current_holding
            gap_val = fund_execution.gap
            ideal_target_val = fund_execution.target_holding
            buy_fee = fund_execution.buy_fee_rate
            gross_gap = max(0.0, gap_val) * (1 + buy_fee)
            monthly_buy_limit = fund_monthly_limits.get(code, float("inf"))
            allocation_state = fund_execution.allocation_state

            # Reset action and amount for each fund to prevent leakage
            # Using a more robust if-elif-else structure
            action = fund_execution.action
            amount = (
                fund_execution.gross_sell
                if action == "Sell"
                else fund_execution.gross_buy
            )
            reason = "持有"

            if allocation_state == "EXIT":
                if amount > 1e-9:
                    reason = "资产已被显式标记为退出，建议卖出"
                else:
                    reason = "资产已标记退出，但当前没有可卖持仓"
            elif gap_val < 0:
                if allocation_state == "NO_NEW_BUY":
                    reason = "目标权重为数值零，暂停新增买入但不自动卖出"
                else:
                    reason = "DCA 不因短期偏离卖出；后续新增资金将优先投向低配资产"
            elif gap_val > 0:
                limit_applied = bool(
                    np.isfinite(monthly_buy_limit)
                    and gross_gap > monthly_buy_limit + 1e-9
                    and fund_execution.gross_buy + 1e-9 >= monthly_buy_limit
                )
                if fund_execution.gross_buy > 0 and positive_gap_sum > 0:
                    if limit_applied:
                        reason = "受申购限额约束，先买入可执行额度"
                    else:
                        reason = f"按 DCA 预算向低配资产投入 (Gap: {gap_val:.1f})"
                else:
                    action = "Hold"
                    if fund_monthly_limits.get(code, float("inf")) <= 1e-9:
                        reason = "申购限额为0，本月无法买入"
                    else:
                        reason = "低位观察不额外定投"
            else:
                action = "Hold"
                reason = "仓位已达标"

            if gap_val <= 0:
                limit_applied = False

            # target_val is already calculated as the ideal target (current + gap)

            fund_advice.append(
                {
                    "code": code,
                    "name": fund_names.get(code, code),
                    "current_holding": current_val,
                    "target_holding": ideal_target_val,
                    "executable_holding": fund_execution.executable_holding,
                    "ideal_holding": ideal_target_val,
                    "gap": gap_val,
                    "action": action,
                    "amount": amount,
                    "monthly_buy_limit": (
                        monthly_buy_limit if np.isfinite(monthly_buy_limit) else None
                    ),
                    "limit_applied": limit_applied,
                    "allocation_state": allocation_state,
                    "reason": reason,
                }
            )

        # 3. Simulate non-risky sleeve after risky-fund trades.
        total_sell_net_proceeds = execution.total_net_sell_proceeds
        reusable_sale_proceeds = execution.reused_sale_proceeds
        cash_after_risky = execution.cash_after
        risk_free_after_trades = execution.risk_free_after

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
                    "executable_holding": risk_free_after_trades,
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
                "executable_holding": cash_after_risky,
                "ideal_holding": target_cash_value,
                "gap": target_cash_value - current_cash,
                "action": cash_action,
                "amount": cash_trade_amount,
                "reason": cash_reason,
            }
        )

        current_asset_values = dict(equity_holdings)
        if can_use_risk_free_asset and risk_free_balance > 0:
            current_asset_values["RiskFree"] = risk_free_balance
        current_total_wealth = current_equity_value + risk_free_balance + current_cash
        current_exposures = calculate_exposure_metrics(
            current_asset_values,
            total_wealth=current_total_wealth,
            asset_categories=asset_categories,
        )

        target_asset_values = dict(target_holdings)
        if target_has_risk_free_asset and target_risk_free_value > 0:
            target_asset_values["RiskFree"] = target_risk_free_value
        target_exposures = calculate_exposure_metrics(
            target_asset_values,
            total_wealth=total_wealth_projected,
            asset_categories=asset_categories,
        )
        target_fund_ratio = (
            (target_equity_value + target_risk_free_value) / total_wealth_projected
            if total_wealth_projected > 0
            else 0.0
        )
        current_fund_ratio = (
            (current_equity_value + risk_free_balance) / current_total_wealth
            if current_total_wealth > 0
            else 0.0
        )

        return {
            "market_signal": market_signal,
            "allocation_signal": allocation_signal,
            "target_fund_ratio": target_fund_ratio,
            "target_equity_ratio": target_equity_ratio,
            "target_equity_exposure": target_exposures["equity_exposure"],
            "target_risk_asset_exposure": target_exposures["risk_asset_exposure"],
            "target_category_values": target_exposures["category_values"],
            "target_category_exposures": target_exposures["category_exposures"],
            "current_fund_ratio": current_fund_ratio,
            "current_equity_exposure": current_exposures["equity_exposure"],
            "current_risk_asset_exposure": current_exposures["risk_asset_exposure"],
            "current_category_values": current_exposures["category_values"],
            "current_category_exposures": current_exposures["category_exposures"],
            "asset_categories": asset_categories,
            "current_equity_value": current_equity_value,
            "current_risk_free_value": risk_free_balance,
            "current_cash": current_cash,
            "target_equity_value": target_equity_value,
            "target_risk_free_value": target_risk_free_value,
            "target_cash_value": target_cash_value,
            "gap": gap,
            "recommended_monthly_investment": recommended_monthly_investment,
            "total_sell_net_proceeds": total_sell_net_proceeds,
            "reused_sale_proceeds": reusable_sale_proceeds,
            "monthly_budget": request.monthly_budget,
            "latest_nav": latest_nav,
            "ma_value": float(current_ma),
            "current_price": float(current_price),
            "strategy_mode": request.strategy_mode,
            "optimizer_info": optimizer_info,
            "window_robustness": window_robustness,
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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/backtest_strategies")
async def run_strategy_backtests(request: StrategyBacktestRequest):
    try:
        _reject_implicit_legacy_risk_free(
            weights=request.weights,
            holdings=request.initial_holdings,
            risk_free_rate=request.risk_free_rate,
        )

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
            risk_horizon_days=request.risk_horizon_days,
        )

        fund_df, _, _ = get_fund_data(
            request.fund_codes,
            request.start_date,
            request.end_date,
            request.risk_free_rate,
        )
        daily_nav = fund_df.attrs.get("daily_nav")
        fund_df, _ = ensure_risk_free_column(
            fund_df,
            {},
            weights_dict=request.weights,
            holdings_dict=request.initial_holdings,
        )
        asset_categories = normalize_asset_categories(
            list(fund_df.columns), request.asset_categories
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
            initial_holdings=request.initial_holdings,
            min_weight=request.min_weight,
            max_weight=request.max_weight,
            buy_fee=request.buy_fee,
            sell_fee=request.sell_fee,
            ma_window=request.ma_window,
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
            fund_investment_limits=request.fund_investment_limits,
            exit_fund_codes=request.exit_fund_codes,
            reuse_settled_sale_proceeds=request.reuse_settled_sale_proceeds,
            asset_categories=asset_categories,
            daily_nav=daily_nav,
            risk_horizon_days=request.risk_horizon_days,
        )
        walk_forward = (
            evaluate_executable_walk_forward(
                nav_adjusted,
                request.fund_fees,
                monthly_investment=request.monthly_investment,
                initial_holdings=request.initial_holdings,
                initial_cash=request.initial_cash,
                buy_fees=request.buy_fee,
                sell_fees=request.sell_fee,
                fund_investment_limits=request.fund_investment_limits,
                min_weight=request.min_weight,
                max_weight=request.max_weight,
                kelly_fraction=request.kelly_fraction,
                estimation_window=request.estimation_window,
                risk_free_rate=request.risk_free_rate or 0.0,
                minimum_cash_reserve=request.minimum_cash_reserve,
                enable_cvar_constraint=request.enable_cvar_constraint,
                cvar_confidence=request.cvar_confidence,
                cvar_limit=request.cvar_limit,
                enable_drawdown_constraint=request.enable_drawdown_constraint,
                max_drawdown_limit=request.max_drawdown_limit,
                daily_nav=daily_nav,
                risk_horizon_days=request.risk_horizon_days,
                include_covariance_ablation=True,
            )
            if request.include_walk_forward
            else None
        )

        return {
            "lump_sum": lump_sum_results,
            "dca": dca_results,
            "kelly_dca": kelly_results,
            "asset_categories": asset_categories,
            "walk_forward": walk_forward,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
