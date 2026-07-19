import asyncio
from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from api.models import CurrentRecommendationRequest
from api.routes import get_current_recommendation
from core.backtest import backtest_kelly_dca
from core.execution import execute_monthly_plan


def test_execute_monthly_plan_reuses_net_exit_proceeds_and_balances_cash():
    result = execute_monthly_plan(
        fund_codes=["A", "B"],
        current_holdings={"A": 0.0, "B": 1000.0},
        target_holdings={"A": 1100.0, "B": 0.0},
        target_weights={"A": 1.0, "B": 0.0},
        current_cash=0.0,
        monthly_budget=100.0,
        buy_fees={},
        sell_fees={"B": 0.01},
        investment_limits={},
        timestamp=date(2026, 7, 16),
        exit_fund_codes=["B"],
        reuse_settled_sale_proceeds=True,
    )

    assert result.funds["A"].gross_buy == pytest.approx(1090.0)
    assert result.funds["A"].target_holding == pytest.approx(1100.0)
    assert result.funds["A"].executable_holding == pytest.approx(1090.0)
    assert result.funds["B"].gross_sell == pytest.approx(1000.0)
    assert result.funds["B"].net_sell_proceeds == pytest.approx(990.0)
    assert result.funds["B"].executable_holding == 0.0
    assert result.cash_after == pytest.approx(0.0)
    assert result.accounting_error == pytest.approx(0.0)


def test_execute_monthly_plan_redistributes_capped_budget():
    result = execute_monthly_plan(
        fund_codes=["A", "B"],
        current_holdings={},
        target_holdings={"A": 1000.0, "B": 1000.0},
        target_weights={"A": 0.5, "B": 0.5},
        current_cash=0.0,
        monthly_budget=1000.0,
        buy_fees={},
        sell_fees={},
        investment_limits={"A": {"monthly_limit": 100.0}},
        timestamp=date(2026, 7, 16),
    )

    assert result.funds["A"].gross_buy == pytest.approx(100.0)
    assert result.funds["B"].gross_buy == pytest.approx(900.0)
    assert result.cash_after == pytest.approx(0.0)
    assert result.accounting_error == pytest.approx(0.0)


def test_execute_monthly_plan_keeps_cash_and_risk_free_separate():
    result = execute_monthly_plan(
        fund_codes=["A"],
        current_holdings={},
        target_holdings={"A": 100.0},
        target_weights={"A": 1.0},
        current_cash=1000.0,
        monthly_budget=100.0,
        buy_fees={},
        sell_fees={},
        investment_limits={},
        timestamp=date(2026, 7, 16),
        minimum_cash_reserve=500.0,
        current_risk_free=100.0,
        target_risk_free=600.0,
        target_cash=500.0,
        can_manage_risk_free=True,
        target_has_risk_free=True,
    )

    assert result.funds["A"].executable_holding == pytest.approx(100.0)
    assert result.risk_free_after == pytest.approx(600.0)
    assert result.cash_after == pytest.approx(500.0)
    assert result.accounting_error == pytest.approx(0.0)


def test_constrained_tracking_uses_same_group_substitute_after_limit_binds():
    covariance = pd.DataFrame(
        [[0.0100, 0.0095], [0.0095, 0.0100]],
        index=["A", "B"],
        columns=["A", "B"],
    )

    result = execute_monthly_plan(
        fund_codes=["A", "B"],
        current_holdings={},
        target_holdings={"A": 1000.0, "B": 0.0},
        target_weights={"A": 1.0, "B": 0.0},
        current_cash=0.0,
        monthly_budget=500.0,
        buy_fees={},
        sell_fees={},
        investment_limits={"A": {"monthly_limit": 100.0}},
        timestamp=date(2026, 7, 16),
        allocation_method="constrained_tracking",
        execution_covariance=covariance,
        fund_roles={"A": "strategic", "B": "substitute"},
        substitution_groups={"A": "em_equity", "B": "em_equity"},
    )

    assert result.funds["A"].gross_buy == pytest.approx(100.0)
    assert result.funds["B"].gross_buy > 350.0
    assert result.funds["B"].allocation_state == "ACTIVE_SUBSTITUTE"
    assert result.funds["B"].buy_source == "limit_substitute"
    assert result.allocation_diagnostics["fallback_used"] is False
    assert (
        result.allocation_diagnostics["tracking_error_after"]
        < result.allocation_diagnostics["tracking_error_before"]
    )


def test_constrained_tracking_does_not_use_unrelated_substitute_group():
    covariance = pd.DataFrame(
        [
            [0.0100, 0.0095, 0.0095, 0.0000],
            [0.0095, 0.0100, 0.0095, 0.0000],
            [0.0095, 0.0095, 0.0100, 0.0000],
            [0.0000, 0.0000, 0.0000, 0.0100],
        ],
        index=["A", "B", "C", "D"],
        columns=["A", "B", "C", "D"],
    )

    result = execute_monthly_plan(
        fund_codes=["A", "B", "C", "D"],
        current_holdings={},
        target_holdings={"A": 1000.0, "B": 0.0, "C": 0.0, "D": 0.0},
        target_weights={"A": 1.0, "B": 0.0, "C": 0.0, "D": 0.0},
        current_cash=0.0,
        monthly_budget=500.0,
        buy_fees={},
        sell_fees={},
        investment_limits={"A": {"monthly_limit": 100.0}},
        timestamp=date(2026, 7, 16),
        allocation_method="constrained_tracking",
        execution_covariance=covariance,
        fund_roles={
            "A": "strategic",
            "B": "substitute",
            "C": "substitute",
            "D": "strategic",
        },
        substitution_groups={
            "A": "em_equity",
            "B": "em_equity",
            "C": "gold",
            "D": "gold",
        },
    )

    assert result.funds["B"].gross_buy > 0.0
    assert result.funds["C"].gross_buy == 0.0


def test_proxy_penalty_can_prefer_cash_over_expensive_substitute():
    covariance = pd.DataFrame(
        [[0.0100, 0.0095], [0.0095, 0.0100]],
        index=["A", "B"],
        columns=["A", "B"],
    )

    result = execute_monthly_plan(
        fund_codes=["A", "B"],
        current_holdings={},
        target_holdings={"A": 1000.0, "B": 0.0},
        target_weights={"A": 1.0, "B": 0.0},
        current_cash=0.0,
        monthly_budget=500.0,
        buy_fees={},
        sell_fees={},
        investment_limits={"A": {"monthly_limit": 100.0}},
        timestamp=date(2026, 7, 16),
        allocation_method="constrained_tracking",
        execution_covariance=covariance,
        fund_roles={"A": "strategic", "B": "substitute"},
        substitution_groups={"A": "em_equity", "B": "em_equity"},
        proxy_penalties={"B": 10.0},
    )

    assert result.funds["A"].gross_buy == pytest.approx(100.0)
    assert result.funds["B"].gross_buy == pytest.approx(0.0, abs=1e-5)
    assert result.cash_after == pytest.approx(400.0, abs=1e-5)


def test_current_recommendation_reports_limit_substitute_purchase():
    dates = pd.date_range("2024-01-31", periods=13, freq="ME")
    returns = [0.02, -0.01, 0.015, -0.005, 0.018, -0.012] * 2
    nav = pd.DataFrame(
        {
            "A": [1.0, *pd.Series([1 + value for value in returns]).cumprod()],
            "B": [
                1.0,
                *pd.Series([1 + 0.95 * value for value in returns]).cumprod(),
            ],
        },
        index=dates,
    )
    request = CurrentRecommendationRequest(
        fund_codes=["A", "B"],
        weights={"A": 1.0, "B": 0.0},
        current_holdings={},
        monthly_budget=500.0,
        strategy_mode="legacy_linear",
        min_weight=1.0,
        max_weight=1.0,
        fund_investment_limits={"A": {"monthly_limit": 100.0}},
        fund_roles={"A": "strategic", "B": "substitute"},
        substitution_groups={"A": "em_equity", "B": "em_equity"},
        execution_allocation_method="constrained_tracking",
    )

    with patch(
        "api.routes.get_fund_data",
        return_value=(nav, {"A": "Preferred", "B": "Proxy"}, []),
    ):
        recommendation = asyncio.run(get_current_recommendation(request))

    advice = {item["code"]: item for item in recommendation["fund_advice"]}
    assert advice["A"]["amount"] == pytest.approx(100.0)
    assert advice["B"]["amount"] > 300.0
    assert advice["B"]["buy_source"] == "limit_substitute"
    assert recommendation["execution_allocation"]["fallback_used"] is False
    assert recommendation["execution_allocation"]["substitute_purchases"]["B"] > 0


def test_backtest_only_uses_substitute_after_lagged_covariance_is_available():
    dates = pd.date_range("2024-01-31", periods=8, freq="ME")
    returns = [0.02, -0.01, 0.015, -0.005, 0.018, -0.012, 0.01]
    nav = pd.DataFrame(
        {
            "A": [1.0, *pd.Series([1 + value for value in returns]).cumprod()],
            "B": [
                1.0,
                *pd.Series([1 + 0.95 * value for value in returns]).cumprod(),
            ],
        },
        index=dates,
    )

    result = backtest_kelly_dca(
        nav,
        {"A": 1.0, "B": 0.0},
        monthly_investment=500.0,
        min_weight=1.0,
        max_weight=1.0,
        strategy_mode="legacy_linear",
        fund_investment_limits={"A": {"monthly_limit": 100.0}},
        fund_roles={"A": "strategic", "B": "substitute"},
        substitution_groups={"A": "em_equity", "B": "em_equity"},
        execution_allocation_method="constrained_tracking",
        estimation_window=6,
    )

    executions = list(result["execution"].values())
    assert executions[0]["funds"]["B"]["gross_buy"] == 0.0
    assert any(
        month["funds"]["B"]["gross_buy"] > 0
        and month["allocation_diagnostics"]["fallback_used"] is False
        for month in executions[4:]
    )


def test_execute_monthly_plan_rejects_unknown_current_holding():
    with pytest.raises(ValueError, match="outside the execution universe"):
        execute_monthly_plan(
            fund_codes=["A"],
            current_holdings={"B": 100.0},
            target_holdings={"A": 100.0},
            target_weights={"A": 1.0},
            current_cash=0.0,
            monthly_budget=100.0,
            buy_fees={},
            sell_fees={},
            investment_limits={},
            timestamp=date(2026, 7, 16),
        )


def test_recommendation_and_backtest_share_first_month_execution_vector():
    dates = pd.date_range("2024-01-31", periods=13, freq="ME")
    nav = pd.DataFrame({"A": [1.0] * 13, "B": [1.0] * 13}, index=dates)
    request = CurrentRecommendationRequest(
        fund_codes=["A", "B"],
        weights={"A": 1.0, "B": 0.0},
        current_holdings={"B": 1000.0},
        monthly_budget=100.0,
        sell_fee={"B": 0.01},
        exit_fund_codes=["B"],
        reuse_settled_sale_proceeds=True,
        strategy_mode="legacy_linear",
        min_weight=1.0,
        max_weight=1.0,
    )

    with patch(
        "api.routes.get_fund_data",
        return_value=(nav, {"A": "Fund A", "B": "Fund B"}, []),
    ):
        recommendation = asyncio.run(get_current_recommendation(request))

    backtest = backtest_kelly_dca(
        nav.iloc[:1],
        {"A": 1.0, "B": 0.0},
        monthly_investment=100.0,
        initial_holdings={"B": 1000.0},
        min_weight=1.0,
        max_weight=1.0,
        sell_fee={"B": 0.01},
        strategy_mode="legacy_linear",
        exit_fund_codes=["B"],
        reuse_settled_sale_proceeds=True,
    )

    recommendation_by_code = {
        item["code"]: item for item in recommendation["fund_advice"]
    }
    execution = backtest["execution"]["2024-01"]
    assert execution["funds"]["A"]["gross_buy"] == pytest.approx(
        recommendation_by_code["A"]["amount"]
    )
    assert execution["funds"]["B"]["gross_sell"] == pytest.approx(
        recommendation_by_code["B"]["amount"]
    )
    assert execution["funds"]["B"]["executable_holding"] == pytest.approx(
        recommendation_by_code["B"]["executable_holding"]
    )
    assert execution["cash_after"] == pytest.approx(
        recommendation_by_code["Cash"]["executable_holding"]
    )
    assert execution["accounting_error"] == pytest.approx(0.0)
