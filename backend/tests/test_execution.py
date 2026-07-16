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
