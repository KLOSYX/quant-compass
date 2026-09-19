import traceback
from dataclasses import asdict
from datetime import date

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException

from api.presentation import build_fund_advice, _unspent_reason_label
from api.models import (
    ActualFillRequest,
    AnalysisRequest,
    CurrentRecommendationRequest,
    FundNamesRequest,
    StrategyBacktestRequest,
)
from core.audit_store import default_audit_store
from core.classification import (
    calculate_exposure_metrics,
    normalize_asset_categories,
)
from core.backtest import (
    backtest_fixed_target,
    simulate_strategy_frontier,
)
from core.data import (
    estimation_total_return_nav,
    ensure_risk_free_column,
    get_fund_data,
    get_fund_names,
    prepare_nav_for_analysis,
)
from core.decision import (
    DecisionInput,
    ExecutionContext,
    PortfolioState,
    StrategySpec,
    decide,
)
from core.domain import ReturnQuality, SubstitutionGroup
from core.execution_optimizer import (
    build_execution_covariance,
    normalize_substitute_for,
    strategic_fund_codes,
)
from core.frontier import (
    append_frontier_stability_warnings,
    calculate_efficient_frontier,
    calculate_frontier_walk_forward_metrics,
    evaluate_covariance_shrinkage_ablation,
)
from core.limits import get_monthly_investment_limits
from core.market_data import build_market_snapshot
from core.portfolio import (
    append_fee_warnings,
    decompose_selected_weights,
    normalize_risky_weights,
)
from core.risk import calculate_asset_diagnostics, estimate_account_risk
from core.reference import build_reference_basket
from core.strategy import (
    validate_strategy_params,
)
from core.walk_forward import evaluate_executable_walk_forward

router = APIRouter()


def _benchmark_rate(request) -> float:
    explicit = getattr(request, "benchmark_rate", None)
    return float(explicit if explicit is not None else (request.risk_free_rate or 0.0))


def _execution_allocation_method(substitute_for: dict[str, str]) -> str:
    has_relationship = any(
        str(primary or "").strip() for primary in substitute_for.values()
    )
    return "constrained_tracking" if has_relationship else "proportional_gap"


def _select_recommended_frontier_point(
    frontier_points: list[dict],
    *,
    maximum_drawdown: float,
    cvar_enabled: bool,
    maximum_cvar_loss: float,
) -> tuple[int | None, dict]:
    """Choose a historical minimum-risk reference, never a return forecast.

    Risk preferences remain visible diagnostics. No Sharpe winner, minimum
    sample count or uncalibrated stability gate defines the user's target.
    """
    candidates = []
    diagnostics = []
    for index, point in enumerate(frontier_points):
        valid = not point.get("solver_fallback_used") and np.isfinite(
            point.get("risk", np.nan)
        )
        point["frontier_recommendation_eligible"] = bool(valid)
        point["frontier_recommendation_rejection_reasons"] = (
            [] if valid else ["unverified_solver"]
        )
        risk_warnings = []
        drawdown = point.get("frontier_walk_forward_max_drawdown")
        cvar = point.get("frontier_walk_forward_cvar_loss")
        if drawdown is not None and drawdown > maximum_drawdown:
            risk_warnings.append("max_drawdown_exceeded")
        if cvar_enabled and cvar is not None and cvar > maximum_cvar_loss:
            risk_warnings.append("cvar_exceeded")
        diagnostics.append(
            {
                "index": index,
                "eligible": bool(valid),
                "rejection_reasons": point["frontier_recommendation_rejection_reasons"],
                "risk_warnings": risk_warnings,
            }
        )
        if valid:
            candidates.append(index)
    selected = (
        min(candidates, key=lambda i: (frontier_points[i]["risk"], i))
        if candidates
        else (0 if frontier_points else None)
    )
    return selected, {
        "selected_index": selected,
        "fallback_used": not candidates,
        "selection_evidence": "historical_minimum_variance_reference_not_forecast",
        "requires_target_confirmation": True,
        "ranking": ["lower_theoretical_risk"],
        "eligible_count": len(candidates),
        "total_count": len(frontier_points),
        "confidence": "limited",
        "candidate_diagnostics": diagnostics,
    }


@router.post("/fund_names")
async def resolve_fund_names(request: FundNamesRequest):
    return {"fund_names": get_fund_names(request.fund_codes)}


@router.post("/actual_fills")
async def record_actual_fill(request: ActualFillRequest):
    with default_audit_store() as store:
        store.save_actual_fill(
            request.decision_hash,
            {"fills": request.fills, "fees": request.fees},
            request.filled_at,
        )
    return {"status": "recorded", "decision_hash": request.decision_hash}


def _reject_implicit_legacy_risk_free(
    *,
    weights: dict[str, float],
    holdings: dict[str, float],
    risk_free_rate: float | None,
) -> None:
    if risk_free_rate is not None and risk_free_rate <= -1:
        raise HTTPException(
            status_code=400, detail="risk_free_rate must be greater than -1"
        )
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
            estimation_window=request.estimation_window,
            minimum_cash_reserve=request.minimum_cash_reserve,
            enable_cvar_constraint=request.enable_cvar_constraint,
            cvar_confidence=request.cvar_confidence,
            cvar_limit=request.cvar_limit,
            enable_drawdown_constraint=request.enable_drawdown_constraint,
            max_drawdown_limit=request.max_drawdown_limit,
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
        quality = fund_df.attrs.get("return_quality", {})
        blocked_codes = sorted(
            code
            for code, item in quality.items()
            if item.status not in {"verified", "reconciled"}
        )
        if request.target_weights is not None:
            decompose_selected_weights(request.target_weights, list(fund_df.columns))
        insufficient_months = len(fund_df) < 3 and any(
            code != "RiskFree" for code in fund_df.columns
        )
        if (
            blocked_codes and not request.allow_partial_return_data_for_research
        ) or insufficient_months:
            categories = normalize_asset_categories(
                list(fund_df.columns), request.asset_categories
            )
            substitutes = normalize_substitute_for(
                list(fund_df.columns), request.substitute_for
            )
            primary_codes = strategic_fund_codes(list(fund_df.columns), substitutes)
            if not primary_codes:
                raise ValueError("at least one strategic fund is required")
            weights = request.target_weights
            if weights is None:
                weights = {code: 1.0 / len(primary_codes) for code in primary_codes}
            message = (
                (
                    "完整月度净值观测不足3期，无法估计组合协方差"
                    if insufficient_months
                    else "以下基金的总收益数据尚未通过核验：" + ", ".join(blocked_codes)
                )
                + "。已暂停前沿和历史绩效估计；"
                + (
                    "沿用上次选定目标。"
                    if request.target_weights is not None
                    else "暂无已选目标，提供所选主标的等权备用配置，供本月执行前复核。"
                )
                + "该配置不代表收益或风险最优，仍可按真实持仓、现金底线和限额生成月度资金安排。"
            )
            return {
                "analysis_status": "target_only_fallback",
                "fallback_message": message,
                "fallback_target": {
                    "weights": weights,
                    "risk": None,
                    "return": None,
                    "target_source": "previous_selection"
                    if request.target_weights is not None
                    else "equal_weight_fallback",
                },
                "return_quality": {
                    code: asdict(item) for code, item in quality.items()
                },
                "blocked_fund_codes": blocked_codes,
                "efficient_frontier": [],
                "strategy_frontier": [],
                "recommended_point_index": None,
                "recommended_point_selection": None,
                "fund_names": fund_names,
                "asset_categories": categories,
                "substitute_for": substitutes,
                "asset_diagnostics": None,
                "covariance_ablation": None,
                "backtest_period": {
                    "start_date": fund_df.index.min().strftime("%Y-%m-%d"),
                    "end_date": fund_df.index.max().strftime("%Y-%m-%d"),
                },
                "warnings": [*warnings, message],
            }
        estimation_nav, quality_warnings = estimation_total_return_nav(
            fund_df,
            allow_degraded_research=request.allow_partial_return_data_for_research,
        )
        warnings.extend(quality_warnings)
        warnings.append(
            "前沿使用各标的自身历史总收益均值，未进行跨类别均值收缩；纵轴是历史算术年化统计，不是未来收益预测。低波动参考点须确认后才成为长期目标；模型未对集中度、信用或流动性偏好作隐含优化。"
        )
        nav_adjusted = prepare_nav_for_analysis(
            estimation_nav,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )
        asset_categories = normalize_asset_categories(
            list(fund_df.columns), request.asset_categories
        )
        normalized_substitute_for = normalize_substitute_for(
            list(fund_df.columns), request.substitute_for
        )
        strategic_codes = strategic_fund_codes(
            list(fund_df.columns), normalized_substitute_for
        )
        if not strategic_codes:
            raise ValueError("at least one strategic fund is required")
        strategic_nav = nav_adjusted[strategic_codes]
        append_frontier_stability_warnings(warnings, strategic_nav, request.fund_fees)
        efficient_frontier_points = calculate_efficient_frontier(
            strategic_nav, request.fund_fees
        )
        asset_diagnostics = calculate_asset_diagnostics(
            strategic_nav, fund_names, efficient_frontier_points
        )
        walk_forward_metrics = calculate_frontier_walk_forward_metrics(
            strategic_nav,
            request.fund_fees,
            cvar_confidence=request.cvar_confidence,
            annual_risk_free_rate=_benchmark_rate(request),
        )
        covariance_ablation = evaluate_covariance_shrinkage_ablation(strategic_nav)
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

        rejected_points = max(
            (p.get("rejected_frontier_points", 0) for p in efficient_frontier_points),
            default=0,
        )
        if rejected_points:
            warnings.append(
                f"{rejected_points} 个前沿候选未通过数值最优性验证，已排除；图中仅展示通过验证的点。"
            )

        recommended_point_index, recommended_point_selection = (
            _select_recommended_frontier_point(
                efficient_frontier_points,
                maximum_drawdown=request.max_drawdown_limit,
                cvar_enabled=request.enable_cvar_constraint,
                maximum_cvar_loss=request.cvar_limit,
            )
        )
        recommended_point_selection.update(
            {
                "cvar_confidence": request.cvar_confidence,
                "annual_risk_free_rate": _benchmark_rate(request),
            }
        )
        if recommended_point_selection["fallback_used"]:
            warnings.append(
                "前沿数值求解未通过最优性验证，展示可行备用配置供确认，不宣称它是最优目标。"
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
                buy_fee=request.buy_fee,
                sell_fee=request.sell_fee,
                strategy_mode=request.strategy_mode,
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
                substitute_for=request.substitute_for,
                execution_allocation_method=_execution_allocation_method(
                    request.substitute_for
                ),
                planning_period_days=request.planning_period_days,
                planned_purchase_days=request.planned_purchase_days,
            )

        return {
            "efficient_frontier": efficient_frontier_points,
            "strategy_frontier": strategy_frontier_points,
            "recommended_point_index": recommended_point_index,
            "recommended_point_selection": recommended_point_selection,
            "fund_names": fund_names,
            "asset_categories": asset_categories,
            "substitute_for": normalized_substitute_for,
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
            estimation_window=request.estimation_window,
            minimum_cash_reserve=request.minimum_cash_reserve,
            enable_cvar_constraint=request.enable_cvar_constraint,
            cvar_confidence=request.cvar_confidence,
            cvar_limit=request.cvar_limit,
            enable_drawdown_constraint=request.enable_drawdown_constraint,
            max_drawdown_limit=request.max_drawdown_limit,
            risk_horizon_days=request.risk_horizon_days,
        )

        # Fetch sufficient history for non-overlapping CVaR diagnostics.
        end_date_obj = date.today()
        start_date_obj = (pd.Timestamp(end_date_obj) - pd.DateOffset(years=10)).date()

        universe = list(
            dict.fromkeys(
                [
                    *request.fund_codes,
                    *(
                        ["RiskFree"]
                        if request.risk_free_rate is not None
                        or request.current_holdings.get("RiskFree", 0) > 0
                        or request.weights.get("RiskFree", 0) > 0
                        else []
                    ),
                ]
            )
        )
        decompose_selected_weights(request.weights, universe)
        data_unavailable = False
        try:
            fund_df, fund_names, data_warnings = get_fund_data(
                request.fund_codes, start_date_obj, end_date_obj, request.risk_free_rate
            )
        except (HTTPException, ValueError, RuntimeError, OSError) as exc:
            # Input/universe validation is above. A provider outage must not erase
            # a user's valid monetary target, holdings, or reserve instructions.
            data_unavailable = True
            fund_df = pd.DataFrame(
                1.0, index=[pd.Timestamp(end_date_obj)], columns=universe
            )
            fund_names = {code: code for code in universe}
            data_warnings = [
                "行情不可用；按已确认目标安排资金，未使用行情预测：" + str(exc)
            ]
            fund_df.attrs["return_quality"] = {
                code: ReturnQuality(
                    status="partial", corporate_action_coverage_complete=False
                )
                for code in universe
            }
        data_quality = fund_df.attrs.get("return_quality", {})
        target_only_fallback = data_unavailable or any(
            item.status not in {"verified", "reconciled"}
            for item in data_quality.values()
        )
        daily_total_return_nav = fund_df.attrs.get("daily_total_return")
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
        if target_only_fallback:
            estimation_nav = fund_df.copy()
            quality_warnings = [
                *data_warnings,
                "收益证据不足，本月沿用已确认目标，不依赖收益预测；补齐数据或下月更新时重算。",
            ]
        else:
            estimation_nav, quality_warnings = estimation_total_return_nav(
                fund_df,
                allow_degraded_research=request.allow_partial_return_data_for_research,
            )
        adjusted_fund_df = prepare_nav_for_analysis(
            estimation_nav,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )
        has_risky_assets = base_risky_ratio > 0 and float(risky_weights.sum()) > 0

        # Reference basket is a historical illustration, never the account Cash sleeve.
        if has_risky_assets:
            reference_portfolio_nav = build_reference_basket(
                adjusted_fund_df[risky_weights.index], risky_weights
            )
        else:
            reference_portfolio_nav = pd.Series(1.0, index=fund_df.index, dtype=float)

        # Calculate moving average based on ma_window
        ma_window = 12
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
            + request.pending_sale_proceeds
        )

        market_signal = "neutral"
        allocation_signal = "neutral"
        optimizer_info = {"policy": "fixed_weight"}
        unique_fund_codes = list(dict.fromkeys(request.fund_codes))
        fund_monthly_limits = get_monthly_investment_limits(
            unique_fund_codes,
            request.fund_investment_limits,
            end_date_obj,
            planned_purchase_days=len(request.planned_purchase_dates)
            or request.planned_purchase_days,
        )
        decision_snapshot = fund_df.attrs.get("market_snapshot")
        decision_as_of = pd.Timestamp.now(tz="UTC")
        if decision_snapshot is None:
            synthetic_quality = {
                code: ReturnQuality(
                    status="partial" if target_only_fallback else "verified",
                    corporate_action_coverage_complete=not target_only_fallback,
                    availability_quality="inferred",
                )
                for code in fund_df.columns
            }
            decision_snapshot = build_market_snapshot(
                price_series=fund_df,
                corporate_actions=(),
                return_quality=synthetic_quality,
                source="caller-supplied",
                fetched_at=decision_as_of,
                available_at={code: decision_as_of for code in fund_df.columns},
            )
        grouped_substitutes = {}
        for substitute, primary in request.substitute_for.items():
            if primary:
                grouped_substitutes.setdefault(primary, []).append(substitute)
        decision_input = DecisionInput(
            as_of=decision_as_of,
            portfolio_state=PortfolioState(
                holdings={**equity_holdings, "RiskFree": risk_free_balance},
                cash=current_cash,
            ),
            market_snapshot=decision_snapshot,
            strategy_spec=StrategySpec(
                policy_id=f"fixed-weight:{request.strategy_mode}:v2",
                target_weights=request.weights,
            ),
            execution_context=ExecutionContext(
                monthly_budget=request.monthly_budget,
                rebalance_enabled=request.rebalance_enabled,
                rebalance_band=request.rebalance_band,
                available_existing_cash=request.available_existing_cash,
                pending_sale_proceeds=request.pending_sale_proceeds,
                pending_sell_amounts=request.pending_sell_amounts,
                redemption_limits=request.redemption_limits,
                planned_purchase_dates=tuple(request.planned_purchase_dates),
                buy_fees=request.buy_fee,
                sell_fees=request.sell_fee,
                investment_limits=request.fund_investment_limits,
                substitution_groups=tuple(
                    SubstitutionGroup(
                        group_id=f"primary:{primary}",
                        primary_fund=primary,
                        substitutes=tuple(substitutes),
                    )
                    for primary, substitutes in sorted(grouped_substitutes.items())
                ),
                minimum_cash_reserve=request.minimum_cash_reserve,
                allow_sales=bool(request.exit_fund_codes),
                exit_fund_codes=tuple(request.exit_fund_codes),
                reuse_settled_sale_proceeds=request.reuse_settled_sale_proceeds,
                planning_period_days=request.planning_period_days,
                planned_purchase_days=request.planned_purchase_days,
                min_purchase_amount=request.min_purchase_amount,
                amount_step=request.amount_step,
                allocation_method=_execution_allocation_method(request.substitute_for),
                execution_covariance=build_execution_covariance(
                    adjusted_fund_df,
                    unique_fund_codes,
                    estimation_window=request.estimation_window,
                ),
            ),
        )
        decision_result = decide(decision_input)
        with default_audit_store() as store:
            store.save_snapshot(decision_snapshot, decision_as_of)
            decision_hash = store.save_decision(
                decision_input, decision_result, decision_as_of
            )
        execution = decision_result.execution_plan
        target_holdings = dict(decision_result.target_allocation)
        target_equity_value = sum(target_holdings.values())
        target_equity_ratio = (
            target_equity_value / total_wealth_projected
            if total_wealth_projected > 0
            else 0.0
        )
        target_risk_free_value = execution.target_risk_free
        target_cash_value = execution.target_cash
        gap = target_equity_value - current_equity_value
        recommended_monthly_investment = (
            execution.total_gross_buy + execution.risk_free_gross_buy
        )
        allocation_diagnostics = dict(execution.allocation_diagnostics)
        # The optimizer reports a portfolio-level reason as well as precise
        # per-fund reasons.  Keep both machine-readable fields in the API and
        # provide labels for the existing diagnostic panel.
        allocation_unspent_reasons = {
            str(code): str(reason)
            for code, reason in (
                allocation_diagnostics.get("unspent_reasons", {}) or {}
            ).items()
            if reason
        }
        allocation_diagnostics["unspent_reason_labels"] = {
            code: _unspent_reason_label(reason)
            for code, reason in allocation_unspent_reasons.items()
        }
        allocation_diagnostics["unspent_reason_label"] = _unspent_reason_label(
            allocation_diagnostics.get("unspent_reason")
        )

        fund_advice = build_fund_advice(
            execution,
            unique_fund_codes,
            fund_names,
            fund_monthly_limits,
            allocation_unspent_reasons,
            allocation_diagnostics,
        )

        # 3. Simulate non-risky sleeve after risky-fund trades.
        total_sell_net_proceeds = execution.total_net_sell_proceeds
        reusable_sale_proceeds = execution.reused_sale_proceeds
        cash_after_risky = execution.cash_after
        risk_free_after_trades = execution.risk_free_after

        if can_use_risk_free_asset:
            risk_free_trade_amount = (
                execution.risk_free_gross_sell or execution.risk_free_gross_buy
            )
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
            cash_reason = "保留现金；额度恢复、卖出款到账或下月新增资金时重新计算"
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

        if execution.pending_sale_proceeds > 0:
            fund_advice.append(
                {
                    "code": "PendingCash",
                    "name": "待到账卖出款",
                    "current_holding": 0.0,
                    "target_holding": 0.0,
                    "executable_holding": execution.pending_sale_proceeds,
                    "ideal_holding": 0.0,
                    "gap": 0.0,
                    "action": "Hold",
                    "amount": execution.pending_sale_proceeds,
                    "reason": "到账后更新现金余额并重新计算；不计入本次可用现金或现金底线",
                }
            )
        current_asset_values = dict(equity_holdings)
        if can_use_risk_free_asset and risk_free_balance > 0:
            current_asset_values["RiskFree"] = risk_free_balance
        current_total_wealth = (
            current_equity_value
            + risk_free_balance
            + current_cash
            + request.pending_sale_proceeds
        )
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
        actual_non_riskfree_fund_ratio = (
            current_equity_value / current_total_wealth
            if current_total_wealth > 0
            else 0.0
        )
        cash_reserve_shortfall = execution.cash_reserve_shortfall
        projected_assets = {
            code: item.executable_holding for code, item in execution.funds.items()
        }
        if "RiskFree" in fund_df.columns:
            projected_assets["RiskFree"] = execution.risk_free_after
        projected_wealth = (
            sum(projected_assets.values())
            + execution.cash_after
            + execution.pending_sale_proceeds
        )
        projected_exposures = calculate_exposure_metrics(
            projected_assets,
            total_wealth=projected_wealth,
            asset_categories=asset_categories,
        )
        # Target attainment is an execution fact, never a future-risk guarantee.
        target_deviations = {
            code: projected_assets.get(code, 0.0) - value
            for code, value in target_asset_values.items()
        }
        target_reached = (
            all(abs(value) <= 0.01 for value in target_deviations.values())
            and cash_reserve_shortfall <= 0.01
        )
        projected_risk = estimate_account_risk(
            daily_total_return_nav,
            projected_assets,
            projected_wealth,
            confidence=request.cvar_confidence,
            horizon=request.risk_horizon_days,
            data_verified=not target_only_fallback,
        )
        optimizer_info = {
            "policy": "fixed_weight",
            "risk_estimate_basis": "projected_account_frozen_exposures",
            "cvar_preference_exceeded": bool(
                request.enable_cvar_constraint
                and projected_risk.get("cvar_loss") is not None
                and projected_risk["cvar_loss"] > request.cvar_limit
            ),
            "drawdown_preference_exceeded": bool(
                request.enable_drawdown_constraint
                and projected_risk.get("max_drawdown") is not None
                and projected_risk["max_drawdown"] > request.max_drawdown_limit
            ),
        }
        residual_cash_ratio = (
            execution.cash_after / projected_wealth if projected_wealth else 0.0
        )

        return {
            "projected_account_risk": projected_risk,
            "projected_category_exposures": projected_exposures["category_exposures"],
            "projected_total_wealth": projected_wealth,
            "projected_cash": execution.cash_after,
            "rebalancing": execution.allocation_diagnostics.get("rebalancing"),
            "market_signal": market_signal,
            "allocation_signal": allocation_signal,
            "target_fund_ratio": target_fund_ratio,
            "target_equity_ratio": target_equity_ratio,
            "base_non_riskfree_fund_ratio": base_risky_ratio,
            "final_non_riskfree_fund_ratio": target_equity_ratio,
            "base_safe_sleeve_ratio": base_risk_free_ratio,
            "residual_cash_ratio": residual_cash_ratio,
            "actual_risk_ratio": actual_non_riskfree_fund_ratio,
            "cash_reserve_shortfall": cash_reserve_shortfall,
            "target_reached": target_reached,
            "target_deviations": target_deviations,
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
            "execution_allocation": allocation_diagnostics,
            "total_sell_net_proceeds": total_sell_net_proceeds,
            "reused_sale_proceeds": reusable_sale_proceeds,
            "monthly_budget": request.monthly_budget,
            "latest_nav": None if data_unavailable else latest_nav,
            "fallback_used": target_only_fallback,
            "ma_value": None if data_unavailable else float(current_ma),
            "current_price": None if data_unavailable else float(current_price),
            "strategy_mode": request.strategy_mode,
            "strategy_policy_id": decision_result.strategy_policy_id,
            "strategy_spec_hash": decision_result.strategy_spec_hash,
            "market_snapshot_hash": decision_result.market_snapshot_hash,
            "portfolio_state_hash": decision_result.portfolio_state_hash,
            "decision_engine_version": decision_result.decision_engine_version,
            "decision_hash": decision_hash,
            "solver_version": decision_result.solver_version,
            "signal_cutoff": str(decision_result.signal_cutoff),
            "constraint_residuals": decision_result.constraint_residuals,
            "decision_readiness": decision_result.decision_readiness,
            "pending_sale_proceeds": execution.pending_sale_proceeds,
            "transaction_fees": execution.transaction_fees,
            "optimizer_info": optimizer_info,
            "effective_risky_weights": risky_weights.to_dict(),
            "warnings": quality_warnings
            + (
                [
                    (
                        "历史信号已按输入管理费额外扣减；若原始净值已是费后净值，这会偏保守。"
                        if request.apply_fund_fees_to_history
                        else "历史信号未额外扣减管理费，以避免对基金单位净值重复扣费。"
                    )
                ]
                if any(abs(fee) > 1e-12 for fee in request.fund_fees.values())
                else []
            ),
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
        account_actions = tuple(fund_df.attrs.get("corporate_actions", ()))
        daily_nav = fund_df.attrs.get("daily_nav")
        daily_total_return_nav = fund_df.attrs.get("daily_total_return")
        fund_df, _ = ensure_risk_free_column(
            fund_df,
            {},
            weights_dict=request.weights,
            holdings_dict=request.initial_holdings,
        )
        asset_categories = normalize_asset_categories(
            list(fund_df.columns), request.asset_categories
        )

        estimation_nav, quality_warnings = estimation_total_return_nav(
            fund_df,
            allow_degraded_research=request.allow_partial_return_data_for_research,
        )
        nav_adjusted = prepare_nav_for_analysis(
            estimation_nav,
            request.fund_fees,
            apply_fund_fees_to_history=request.apply_fund_fees_to_history,
        )

        common = dict(
            initial_holdings=request.initial_holdings,
            initial_cash=request.initial_cash,
            available_existing_cash=request.available_existing_cash,
            rebalance_band=request.rebalance_band,
            redemption_limits=request.redemption_limits,
            buy_fee=request.buy_fee,
            sell_fee=request.sell_fee,
            minimum_cash_reserve=request.minimum_cash_reserve,
            fund_investment_limits=request.fund_investment_limits,
            exit_fund_codes=request.exit_fund_codes,
            reuse_settled_sale_proceeds=request.reuse_settled_sale_proceeds,
            asset_categories=asset_categories,
            daily_nav=daily_nav,
            substitute_for=request.substitute_for,
            execution_allocation_method=_execution_allocation_method(
                request.substitute_for
            ),
            planning_period_days=request.planning_period_days,
            planned_purchase_days=request.planned_purchase_days,
            min_purchase_amount=request.min_purchase_amount,
            amount_step=request.amount_step,
            estimation_nav=nav_adjusted,
            daily_estimation_nav=daily_total_return_nav,
            corporate_actions=account_actions,
            account_distribution_policy=request.account_distribution_policy,
        )
        baseline = dict(
            common,
            strategy_mode="fixed_weight",
            rebalance_enabled=False,
        )
        dca_results = backtest_fixed_target(
            fund_df, request.weights, request.monthly_investment, **baseline
        )
        lump_sum_results = backtest_fixed_target(
            fund_df,
            request.weights,
            request.monthly_investment,
            **baseline,
            contributions={fund_df.index[0]: request.monthly_investment * len(fund_df)},
        )
        fixed_target_results = backtest_fixed_target(
            fund_df,
            request.weights,
            request.monthly_investment,
            **common,
            rebalance_enabled=request.rebalance_enabled,
            risk_free_rate=_benchmark_rate(request),
            strategy_mode=request.strategy_mode,
            estimation_window=request.estimation_window,
            enable_cvar_constraint=request.enable_cvar_constraint,
            cvar_confidence=request.cvar_confidence,
            cvar_limit=request.cvar_limit,
            enable_drawdown_constraint=request.enable_drawdown_constraint,
            max_drawdown_limit=request.max_drawdown_limit,
            risk_horizon_days=request.risk_horizon_days,
        )
        walk_forward = (
            evaluate_executable_walk_forward(
                nav_adjusted,
                request.fund_fees,
                monthly_investment=request.monthly_investment,
                initial_holdings=request.initial_holdings,
                initial_cash=request.initial_cash,
                rebalance_enabled=request.rebalance_enabled,
                rebalance_band=request.rebalance_band,
                available_existing_cash=request.available_existing_cash,
                redemption_limits=request.redemption_limits,
                buy_fees=request.buy_fee,
                sell_fees=request.sell_fee,
                fund_investment_limits=request.fund_investment_limits,
                estimation_window=request.estimation_window,
                risk_free_rate=_benchmark_rate(request),
                minimum_cash_reserve=request.minimum_cash_reserve,
                enable_cvar_constraint=request.enable_cvar_constraint,
                cvar_confidence=request.cvar_confidence,
                cvar_limit=request.cvar_limit,
                enable_drawdown_constraint=request.enable_drawdown_constraint,
                max_drawdown_limit=request.max_drawdown_limit,
                daily_nav=daily_nav,
                risk_horizon_days=request.risk_horizon_days,
                include_covariance_ablation=True,
                asset_categories=asset_categories,
                substitute_for=request.substitute_for,
                execution_allocation_method=_execution_allocation_method(
                    request.substitute_for
                ),
                planning_period_days=request.planning_period_days,
                planned_purchase_days=request.planned_purchase_days,
                execution_nav=fund_df,
                corporate_actions=account_actions,
                account_distribution_policy=request.account_distribution_policy,
                min_purchase_amount=request.min_purchase_amount,
                amount_step=request.amount_step,
            )
            if request.include_walk_forward
            else None
        )

        return {
            "lump_sum": lump_sum_results,
            "dca": dca_results,
            "fixed_target": fixed_target_results,
            "asset_categories": asset_categories,
            "walk_forward": walk_forward,
            "warnings": quality_warnings,
        }
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
