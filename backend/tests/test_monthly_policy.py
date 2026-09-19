"""Business acceptance cases independent of optimizer implementation details."""

import numpy as np
import pandas as pd
import pytest

from core.decision import ExecutionContext, PortfolioState, StrategySpec, plan_month
from core.execution import execute_monthly_plan
from core.limits import monthly_investment_limit
from core.data import _get_fund_nav


def test_riskfree_purchase_preserves_household_cash_floor():
    result = plan_month(
        columns=["RiskFree"],
        state=PortfolioState({}, 0),
        strategy=StrategySpec("fixed", {"RiskFree": 1}),
        context=ExecutionContext(monthly_budget=1000, minimum_cash_reserve=500),
        as_of="2026-09-19",
    )
    assert result.cash_after == 500
    assert result.risk_free_after == 500
    assert result.cash_reserve_shortfall == 0
    assert result.accounting_error == pytest.approx(0)


def test_cash_floor_and_accounting_for_varied_valid_portfolios():
    rng = np.random.default_rng(391)
    for _ in range(60):
        budget, cash, reserve = rng.uniform(0, 10000, size=3)
        safe_weight = float(rng.uniform())
        holdings = {"A": 5000.0, "RiskFree": 1000.0}
        result = plan_month(
            columns=["A", "RiskFree"],
            state=PortfolioState(holdings, cash),
            strategy=StrategySpec(
                "fixed", {"A": 1 - safe_weight, "RiskFree": safe_weight}
            ),
            context=ExecutionContext(
                monthly_budget=budget,
                minimum_cash_reserve=reserve,
                buy_fees={"A": 0.015},
            ),
            as_of="2026-09-19",
        )
        assert result.cash_after >= min(cash + budget, reserve) - 1e-7
        assert result.accounting_error == pytest.approx(0, abs=1e-7)
        assert result.funds["A"].gross_buy * 100 == pytest.approx(
            round(result.funds["A"].gross_buy * 100)
        )


def test_unsettled_sale_is_neither_spendable_nor_cash_reserve():
    result = execute_monthly_plan(
        fund_codes=["A", "B"],
        current_holdings={"A": 1000},
        target_holdings={"A": 0, "B": 1100},
        target_weights={"A": 0, "B": 1},
        current_cash=0,
        monthly_budget=100,
        buy_fees={},
        sell_fees={},
        investment_limits={},
        timestamp="2026-09-19",
        minimum_cash_reserve=1000,
        exit_fund_codes=["A"],
        reuse_settled_sale_proceeds=False,
    )
    assert result.total_gross_buy == 0
    assert result.cash_after == 100
    assert result.pending_sale_proceeds == 1000
    assert result.cash_reserve_shortfall == 900
    assert result.accounting_error == 0


def test_purchase_minimum_does_not_force_overspending():
    result = plan_month(
        columns=["A"],
        state=PortfolioState({}, 0),
        strategy=StrategySpec("fixed", {"A": 1}),
        context=ExecutionContext(monthly_budget=9.99, min_purchase_amount=10),
        as_of="2026-09-19",
    )
    assert result.total_gross_buy == 0
    assert result.cash_after == 9.99
    assert (
        result.allocation_diagnostics["unspent_reason"] == "purchase_amount_constraints"
    )


def test_daily_limits_use_standard_month_and_subtract_used_capacity():
    config = {
        "daily_limit": 100,
        "monthly_limit": 500,
        "daily_used": 20,
        "monthly_used": 450,
    }
    assert monthly_investment_limit({"daily_limit": 100}, "2026-09-30") == 2100
    assert (
        monthly_investment_limit(
            {"daily_limit": 100}, "2026-09-30", planned_purchase_days=20
        )
        == 2000
    )
    assert monthly_investment_limit(config, "2026-09-19") == 50


def test_dividend_reconciliation_uses_cumulative_not_reinvested_nav(monkeypatch):
    dates = pd.date_range("2026-01-01", periods=3)
    frames = {
        "单位净值走势": pd.DataFrame({"净值日期": dates, "单位净值": [1.0, 0.9, 0.99]}),
        "累计净值走势": pd.DataFrame({"净值日期": dates, "累计净值": [1.0, 1.0, 1.09]}),
        "分红送配详情": pd.DataFrame({"除息日": [dates[1]], "每份分红": [0.1]}),
        "拆分详情": pd.DataFrame(),
    }
    monkeypatch.setattr(
        "core.data.ak.fund_open_fund_info_em",
        lambda symbol, indicator: frames[indicator].copy(),
    )
    nav = _get_fund_nav("A", "债券")
    assert nav.attrs["return_quality"].status == "reconciled"
    assert nav.attrs["return_quality"].reconciliation_error == pytest.approx(0)


def test_reserve_repair_sells_only_needed_amount_and_waits_for_settlement():
    result = plan_month(
        columns=["A"],
        state=PortfolioState({"A": 1000}, 0),
        strategy=StrategySpec("fixed", {"A": 1}),
        context=ExecutionContext(
            monthly_budget=100, minimum_cash_reserve=500, sell_fees={"A": 0.01}
        ),
        as_of="2026-09-19",
    )
    assert result.funds["A"].gross_sell == pytest.approx(400 / 0.99)
    assert result.funds["A"].allocation_state == "RESERVE_REPAIR"
    assert result.pending_sale_proceeds == pytest.approx(400)
    assert result.cash_after == 100
    assert result.transaction_fees == pytest.approx(400 / 0.99 - 400)


def test_dividends_in_shared_backtest_ledger_are_not_market_losses():
    from core.backtest import backtest_fixed_target
    from core.domain import CorporateAction

    dates = pd.to_datetime(["2026-01-31", "2026-02-28", "2026-03-31"])
    prices = pd.DataFrame({"A": [1.0, 0.9, 0.99]}, index=dates)
    action = CorporateAction(
        asset_code="A",
        kind="cash_distribution",
        record_date="2026-02-01",
        ex_date="2026-02-02",
        payment_date="2026-03-02",
        cash_per_share=0.1,
    )
    result = backtest_fixed_target(
        prices,
        {"A": 1},
        0,
        initial_holdings={"A": 100},
        strategy_mode="fixed_weight",
        corporate_actions=[action],
    )
    assert result["history"]["2026-02"] == pytest.approx(100)
    assert result["attribution"]["2026-02"]["Cash"] == 0
    assert result["attribution"]["2026-02"]["PendingCash"] == 10
    assert result["history"]["2026-03"] == pytest.approx(109)
    assert result["attribution"]["2026-03"]["Cash"] == 10


def test_reinvested_distribution_uses_exact_confirmation_price():
    from core.backtest import backtest_fixed_target
    from core.domain import CorporateAction

    dates = pd.to_datetime(["2026-01-31", "2026-02-28", "2026-03-31"])
    prices = pd.DataFrame({"A": [1.0, 0.9, 0.99]}, index=dates)
    action = CorporateAction(
        asset_code="A",
        kind="cash_distribution",
        record_date="2026-02-01",
        ex_date="2026-02-02",
        payment_date="2026-02-28",
        cash_per_share=0.1,
    )
    result = backtest_fixed_target(
        prices,
        {"A": 1},
        0,
        initial_holdings={"A": 100},
        strategy_mode="fixed_weight",
        corporate_actions=[action],
        account_distribution_policy="reinvest",
    )
    assert result["final_value"] == pytest.approx(110)
    assert result["monthly_max_drawdown"] == pytest.approx(0)


def test_missing_research_metrics_do_not_block_unconfirmed_reference():
    from api.routes import _select_recommended_frontier_point

    points = [
        {"risk": 0.2, "weights": {"A": 1}},
        {"risk": 0.1, "weights": {"A": 0.5, "B": 0.5}},
    ]
    selected, evidence = _select_recommended_frontier_point(
        points, maximum_drawdown=0.1, cvar_enabled=True, maximum_cvar_loss=0.01
    )
    assert selected == 1
    assert evidence["fallback_used"] is False
    assert evidence["requires_target_confirmation"] is True
    assert evidence["confidence"] == "limited"
    assert evidence["eligible_count"] == 2


def test_data_outage_keeps_a_valid_target_based_monthly_plan(monkeypatch, tmp_path):
    import asyncio
    from api.models import CurrentRecommendationRequest
    from api.routes import get_current_recommendation

    monkeypatch.setenv("QUANT_COMPASS_AUDIT_DB", str(tmp_path / "audit.sqlite3"))

    def unavailable(*args, **kwargs):
        raise OSError("provider offline")

    monkeypatch.setattr("api.routes.get_fund_data", unavailable)
    result = asyncio.run(
        get_current_recommendation(
            CurrentRecommendationRequest(
                fund_codes=["A"],
                weights={"A": 1},
                monthly_budget=1000,
                minimum_cash_reserve=500,
            )
        )
    )
    assert result["fallback_used"] is True
    assert result["latest_nav"] is None
    by_code = {row["code"]: row for row in result["fund_advice"]}
    assert by_code["A"]["amount"] == 500
    assert by_code["Cash"]["executable_holding"] == 500
    assert result["decision_readiness"] == "research_only"


def test_frontier_solver_failure_returns_labeled_feasible_target(monkeypatch):
    from types import SimpleNamespace
    from core import frontier

    monkeypatch.setattr(
        frontier, "minimize", lambda *args, **kwargs: SimpleNamespace(success=False)
    )
    nav = pd.DataFrame({"A": [1, 1.1, 1.05, 1.2], "B": [1, 0.95, 1.1, 1.15]})
    points = frontier.calculate_efficient_frontier(nav, {})
    assert len(points) == 1
    assert points[0]["weights"] == {"A": 0.5, "B": 0.5}
    assert points[0]["solver_fallback_used"] is True
    assert np.isfinite(points[0]["risk"])
    assert np.isfinite(points[0]["return"])


@pytest.mark.parametrize("previous", [None, {"006662": 0.7, "012643": 0.3}])
def test_partial_return_analysis_continues_to_monthly_plan(
    monkeypatch, tmp_path, previous
):
    import asyncio
    from api.models import AnalysisRequest, CurrentRecommendationRequest
    from api.routes import analyze_portfolio, get_current_recommendation
    from core.domain import ReturnQuality

    codes = ["006662", "012643", "270023", "457001"]
    nav = pd.DataFrame(
        1.0, index=pd.date_range("2026-01-31", periods=8, freq="ME"), columns=codes
    )
    nav.attrs["return_quality"] = {
        code: ReturnQuality(status="partial", corporate_action_coverage_complete=False)
        for code in codes
    }
    nav.attrs["monthly_total_return"] = nav.copy()
    monkeypatch.setattr(
        "api.routes.get_fund_data",
        lambda *a, **k: (nav.copy(), dict(zip(codes, codes)), []),
    )
    monkeypatch.setenv("QUANT_COMPASS_AUDIT_DB", str(tmp_path / "audit.sqlite3"))

    def no_estimation(*a, **k):
        raise AssertionError("Unverified returns must not enter estimation")

    monkeypatch.setattr("api.routes.estimation_total_return_nav", no_estimation)
    analysis = asyncio.run(
        analyze_portfolio(
            AnalysisRequest(
                fund_codes=codes,
                fund_fees={},
                target_weights=previous,
            )
        )
    )
    assert analysis["analysis_status"] == "target_only_fallback"
    assert analysis["efficient_frontier"] == []
    assert analysis["fallback_target"]["risk"] is None
    assert analysis["fallback_target"]["return"] is None
    assert analysis["blocked_fund_codes"] == codes
    weights = analysis["fallback_target"]["weights"]
    assert weights == (previous or dict.fromkeys(codes, 0.25))
    recommendation = asyncio.run(
        get_current_recommendation(
            CurrentRecommendationRequest(
                fund_codes=codes,
                weights=weights,
                monthly_budget=1000,
                minimum_cash_reserve=200,
            )
        )
    )
    assert recommendation["fallback_used"] is True
    assert recommendation["recommended_monthly_investment"] == pytest.approx(800)
    cash = next(row for row in recommendation["fund_advice"] if row["code"] == "Cash")
    assert cash["executable_holding"] == pytest.approx(200)


def test_standard_month_daily_capacity_is_independent_of_calendar_date():
    for date in ("2026-09-01", "2026-09-19", "2026-09-30", "2026-02-28", "2028-02-29"):
        assert monthly_investment_limit({"daily_limit": 100}, date) == 2100
        assert (
            monthly_investment_limit(
                {"daily_limit": 100}, date, planned_purchase_days=1
            )
            == 100
        )
        assert (
            monthly_investment_limit(
                {"daily_limit": 100, "monthly_limit": 2000, "monthly_used": 500}, date
            )
            == 1500
        )


def test_planning_period_and_trading_days_are_independent_settings():
    from api.models import CurrentRecommendationRequest
    from pydantic import ValidationError

    settings = CurrentRecommendationRequest(
        fund_codes=["A"], monthly_budget=1000, weights={"A": 1}
    )
    assert settings.planning_period_days == 30
    assert settings.planned_purchase_days == 21
    settings = CurrentRecommendationRequest(
        fund_codes=["A"],
        monthly_budget=1000,
        weights={"A": 1},
        planning_period_days=45,
        planned_purchase_days=32,
    )
    assert (
        monthly_investment_limit(
            {"daily_limit": 100},
            "2026-09-30",
            planned_purchase_days=settings.planned_purchase_days,
        )
        == 3200
    )
    with pytest.raises(ValidationError, match="cannot exceed"):
        CurrentRecommendationRequest(
            fund_codes=["A"],
            monthly_budget=1000,
            weights={"A": 1},
            planning_period_days=15,
            planned_purchase_days=21,
        )
