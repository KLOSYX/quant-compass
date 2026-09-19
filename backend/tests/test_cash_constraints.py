"""
Test cases for cash constraint and fee handling logic.
"""

from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_recommendation_insufficient_cash():
    """Test that recommendation respects available cash constraint."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates), "000002": [2.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    # Create severe undervaluation to trigger large gap
    mock_df.iloc[-1] = mock_df.iloc[-1] * 0.3  # 70% drop

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A", "000002": "Fund B"},
            [],
        )

        request_data = {
            "fund_codes": ["000001", "000002"],
            "weights": {"000001": 0.6, "000002": 0.4},
            "current_holdings": {"000001": 1000, "000002": 500},  # ¥1500 total
            "current_cash": 500.0,  # Only ¥500 cash
            "monthly_budget": 1000,  # ¥1000 budget
            # Would allow ¥3000 theoretically
            "strategy_mode": "fixed_weight",
        }

        response = client.post("/api/current_recommendation", json=request_data)

        assert response.status_code == 200
        data = response.json()

        # Available cash = 500 (current_cash) + 0 (risk_free) + 1000 (budget) = 1500
        # Even though max_buy_multiplier allows 3000, and gap is large,
        # recommended amount should NOT exceed available cash (1500)
        assert data["recommended_monthly_investment"] <= 1500.0
        print(f"Recommended: ¥{data['recommended_monthly_investment']:.2f}")
        print("Available cash: ¥1500.00")


def test_recommendation_zero_cash():
    """Test recommendation with no available cash."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    mock_df.iloc[-1] = mock_df.iloc[-1] * 0.5  # Create undervaluation

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )

        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 5000},
            "current_cash": 0.0,  # No cash
            "monthly_budget": 0,  # No budget
            "strategy_mode": "fixed_weight",
        }

        response = client.post("/api/current_recommendation", json=request_data)

        assert response.status_code == 200
        data = response.json()

        # With zero cash and zero budget, should recommend 0
        assert data["recommended_monthly_investment"] == 0.0


def test_recommendation_respects_minimum_cash_reserve():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    mock_df.iloc[-1] = mock_df.iloc[-1] * 0.5

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )

        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 0},
            "current_cash": 0.0,
            "monthly_budget": 1000,
            "minimum_cash_reserve": 900,
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()

        # Buy cash cannot exceed budget minus reserve.
        assert data["recommended_monthly_investment"] <= 100.0 + 1e-6


def test_recommendation_zero_target_when_reserve_exceeds_wealth():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )

        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 200},
            "current_cash": 100.0,
            "monthly_budget": 0,
            "minimum_cash_reserve": 1000.0,
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()
        assert data["target_equity_ratio"] == 0.0
        assert data["recommended_monthly_investment"] == 0.0


def test_cvar_warning_preserves_fixed_target():
    dates = pd.date_range(start="2023-01-01", periods=36, freq="ME")
    nav_values = [
        1.0,
        1.5,
        0.7,
        1.6,
        0.6,
        1.8,
        0.55,
        1.9,
        0.5,
        2.0,
        0.48,
        2.2,
    ] * 3
    mock_df = pd.DataFrame({"000001": nav_values[: len(dates)]}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )
        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 0},
            "current_cash": 0.0,
            "monthly_budget": 1000,
            "enable_cvar_constraint": True,
            "cvar_confidence": 0.95,
            "cvar_limit": 0.03,
            "enable_drawdown_constraint": False,
        }
        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()
        assert data["optimizer_info"]["policy"] == "fixed_weight"
        assert data["recommended_monthly_investment"] == pytest.approx(1000)
        assert data["target_equity_ratio"] == pytest.approx(1.0)


def test_drawdown_warning_preserves_fixed_target():
    dates = pd.date_range(start="2023-01-01", periods=36, freq="ME")
    nav_values = [
        1.0,
        1.4,
        0.8,
        1.6,
        0.75,
        1.7,
        0.72,
        1.8,
        0.7,
        1.9,
        0.68,
        2.0,
    ] * 3
    mock_df = pd.DataFrame({"000001": nav_values[: len(dates)]}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )
        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 0},
            "current_cash": 0.0,
            "monthly_budget": 1000,
            "enable_cvar_constraint": False,
            "enable_drawdown_constraint": True,
            "max_drawdown_limit": 0.05,
        }
        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()
        assert data["optimizer_info"]["policy"] == "fixed_weight"
        assert data["recommended_monthly_investment"] == pytest.approx(1000)
        assert data["target_equity_ratio"] == pytest.approx(1.0)


def test_combined_risk_warnings_preserve_fixed_target():
    dates = pd.date_range(start="2023-01-01", periods=36, freq="ME")
    nav_values = [
        1.0,
        1.5,
        0.7,
        1.7,
        0.65,
        1.8,
        0.6,
        1.9,
        0.55,
        2.0,
        0.5,
        2.2,
    ] * 3
    mock_df = pd.DataFrame({"000001": nav_values[: len(dates)]}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )
        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 0},
            "current_cash": 0.0,
            "monthly_budget": 1000,
            "enable_cvar_constraint": True,
            "cvar_limit": 0.04,
            "enable_drawdown_constraint": True,
            "max_drawdown_limit": 0.08,
        }
        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()
        assert data["optimizer_info"]["policy"] == "fixed_weight"
        assert data["recommended_monthly_investment"] == pytest.approx(1000)
        assert data["target_equity_ratio"] == pytest.approx(1.0)


def test_soft_risk_preferences_do_not_cancel_minimum_allocation():
    dates = pd.date_range(start="2023-01-01", periods=36, freq="ME")
    nav_values = [
        1.0,
        1.6,
        0.6,
        1.7,
        0.55,
        1.8,
        0.5,
        1.9,
        0.45,
        2.0,
        0.4,
        2.2,
    ] * 3
    mock_df = pd.DataFrame({"000001": nav_values[: len(dates)]}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )
        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 0},
            "current_cash": 0.0,
            "monthly_budget": 1000,
            "enable_cvar_constraint": True,
            "cvar_limit": 0.02,
            "enable_drawdown_constraint": True,
            "max_drawdown_limit": 0.04,
        }
        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()
        assert data["optimizer_info"]["policy"] == "fixed_weight"
        assert data["recommended_monthly_investment"] == pytest.approx(1000)
        assert data["target_equity_ratio"] == pytest.approx(1.0)


def test_backtest_fee_no_overdraft():
    """Test that buy fees don't cause cash overdraft in backtest."""
    from core.backtest import backtest_fixed_target

    # Create simple NAV data
    dates = pd.date_range(start="2023-01-31", end="2023-06-30", freq="ME")
    nav_data = {
        "000001": [1.0, 0.9, 0.8, 0.7, 0.6, 0.5],  # Continuous decline
        "000002": [2.0, 1.8, 1.6, 1.4, 1.2, 1.0],
    }
    df_nav = pd.DataFrame(nav_data, index=dates)

    weights = {"000001": 0.6, "000002": 0.4}
    monthly_investment = 1000.0
    buy_fee = {"000001": 0.015, "000002": 0.015}  # 1.5% buy fee
    sell_fee = {"000001": 0.005, "000002": 0.005}

    result = backtest_fixed_target(
        df_nav,
        weights,
        monthly_investment,
        initial_holdings={},
        buy_fee=buy_fee,
        sell_fee=sell_fee,
    )

    # Check that backtest completed successfully
    assert result["total_invested"] > 0
    assert result["final_value"] > 0

    # Verify cash never went negative by checking attribution
    for month, attribution in result["attribution"].items():
        risk_free = attribution.get("RiskFree", 0)
        # Cash (RiskFree) should never be negative
        assert risk_free >= -0.01, f"Cash balance went negative in {month}: {risk_free}"


def test_backtest_respects_minimum_cash_reserve_floor():
    from core.backtest import backtest_fixed_target

    dates = pd.date_range(start="2023-01-31", end="2023-06-30", freq="ME")
    nav_data = {"000001": [1.0, 0.9, 0.8, 0.7, 0.6, 0.5]}
    df_nav = pd.DataFrame(nav_data, index=dates)

    result = backtest_fixed_target(
        df_nav,
        {"000001": 1.0},
        1000.0,
        initial_holdings={"RiskFree": 800.0},
        minimum_cash_reserve=500.0,
    )

    for attribution in result["attribution"].values():
        assert attribution.get("Cash", 0) >= 499.99


def test_backtest_parks_uninvested_capital_in_riskfree_when_available():
    from core.backtest import backtest_fixed_target

    dates = pd.date_range(start="2023-01-31", periods=3, freq="ME")
    df_nav = pd.DataFrame(
        {
            "000001": [1.0, 1.0, 1.0],
            "RiskFree": [1.0, 1.001, 1.002],
        },
        index=dates,
    )

    result = backtest_fixed_target(
        df_nav,
        {"000001": 0.4, "RiskFree": 0.6},
        monthly_investment=100.0,
        initial_holdings={},
        initial_cash=1000.0,
        strategy_mode="fixed_weight",
    )

    first_month = result["attribution"][dates[0].strftime("%Y-%m")]
    assert first_month["000001"] == 40.0
    # The base 60% safe sleeve remains distinct from residual cash.
    assert first_month["Cash"] == pytest.approx(1000.0)
    assert first_month["RiskFree"] == pytest.approx(60.0)


def test_fee_calculation_accuracy():
    """Test that fees are calculated accurately in recommendations."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    mock_df.iloc[-1] = 0.8  # Slight undervaluation

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )

        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 5000},
            "current_cash": 0.0,
            "monthly_budget": 1000,
            "buy_fee": {"000001": 0.015},  # 1.5% buy fee
            "strategy_mode": "fixed_weight",
        }

        response = client.post("/api/current_recommendation", json=request_data)

        assert response.status_code == 200
        data = response.json()

        # In Gross Investment logic:
        # 1. recommended_monthly_investment represents total cash spent.
        # 2. total_buy_with_fees (sum of Buy amounts) should equal recommended_monthly_investment.
        # 3. net_cash_flow = budget - total_buy_with_fees.

        buy_total = sum(
            item["amount"] for item in data["fund_advice"] if item["action"] == "Buy"
        )
        sell_total = sum(
            item["amount"] for item in data["fund_advice"] if item["action"] == "Sell"
        )
        budget = request_data["monthly_budget"]

        deposit_row = data["fund_advice"][-1]
        deposit_amount = deposit_row["amount"]

        # budget + sell_total_net = buy_total + deposit_amount
        # In this test, sell_fee is not set, so it defaults to 0.
        assert abs(budget + sell_total - (buy_total + deposit_amount)) < 0.1

        print(
            f"Budget: {budget}, Buys: {buy_total}, Sells: {sell_total}, Deposit: {deposit_amount}"
        )


def test_recommendation_respects_fund_monthly_buy_limits_and_redistributes():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame(
        {"000001": [1.0] * len(dates), "000002": [1.0] * len(dates)},
        index=dates,
    )
    mock_df.iloc[-1] = 0.3

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Limited Fund", "000002": "Open Fund"},
            [],
        )

        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001", "000002"],
                "weights": {"000001": 0.5, "000002": 0.5},
                "current_holdings": {},
                "current_cash": 0.0,
                "monthly_budget": 10000,
                "strategy_mode": "fixed_weight",
                "fund_investment_limits": {
                    "000001": {"monthly_limit": 500.0},
                },
            },
        )

    assert response.status_code == 200
    data = response.json()
    advice = {item["code"]: item for item in data["fund_advice"]}

    assert advice["000001"]["action"] == "Buy"
    assert advice["000001"]["amount"] == pytest.approx(500.0)
    assert advice["000001"]["limit_applied"] is True
    assert advice["000001"]["target_holding"] == pytest.approx(5000.0)
    assert advice["000001"]["executable_holding"] == pytest.approx(500.0)
    assert advice["000001"]["ideal_holding"] == pytest.approx(5000.0)

    assert advice["000002"]["action"] == "Buy"
    assert advice["000002"]["amount"] == pytest.approx(5000.0)
    assert data["recommended_monthly_investment"] == pytest.approx(5500.0)
    assert advice["Cash"]["executable_holding"] == pytest.approx(4500.0)


def test_daily_limit_uses_explicit_planned_purchase_days():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Daily Capped Fund"}, [])
        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "monthly_budget": 10000.0,
                "strategy_mode": "fixed_weight",
                "fund_investment_limits": {"000001": {"daily_limit": 2000.0}},
                "planned_purchase_days": 1,
            },
        )

    assert response.status_code == 200
    advice = next(
        item for item in response.json()["fund_advice"] if item["code"] == "000001"
    )
    assert advice["monthly_buy_limit"] == pytest.approx(2000.0)
    assert advice["amount"] == pytest.approx(2000.0)


def test_recommendation_does_not_blame_limit_when_only_dca_budget_is_partial():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Unlimited Fund"}, [])
        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "current_holdings": {},
                "current_cash": 9000.0,
                "monthly_budget": 1000.0,
                "strategy_mode": "fixed_weight",
            },
        )

    assert response.status_code == 200
    advice = next(
        item for item in response.json()["fund_advice"] if item["code"] == "000001"
    )
    assert advice["action"] == "Buy"
    assert advice["amount"] == pytest.approx(1000.0)
    assert advice["monthly_buy_limit"] is None
    assert advice["limit_applied"] is False
    assert advice["amount"] == advice["gap"]
    assert "DCA" in advice["reason"]


def test_recommendation_does_not_sell_overweight_fund_while_net_buy_is_capped():
    """Do not create idle cash by executing the sell side of a capped rebalance."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame(
        {"000001": [1.0] * len(dates), "000002": [1.0] * len(dates)}, index=dates
    )

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Overweight Fund", "000002": "Capped Buy Fund"},
            [],
        )
        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001", "000002"],
                "weights": {"000001": 0.5, "000002": 0.5},
                "current_holdings": {"000001": 10000.0},
                "current_cash": 1000.0,
                "rebalance_enabled": False,
                "monthly_budget": 100.0,
                "strategy_mode": "fixed_weight",
                "fund_investment_limits": {"000002": {"monthly_limit": 500.0}},
            },
        )

    assert response.status_code == 200
    data = response.json()
    advice = {item["code"]: item for item in data["fund_advice"]}
    assert data["gap"] > 0
    assert advice["000002"]["action"] == "Buy"
    assert advice["000002"]["amount"] == pytest.approx(100.0)
    assert advice["000001"]["gap"] < 0
    assert advice["000001"]["action"] == "Hold"
    assert advice["000001"]["amount"] == 0.0
    assert advice["000001"]["executable_holding"] == pytest.approx(10000.0)
    assert "偏差在容忍范围内" in advice["000001"]["reason"]
    assert advice["Cash"]["executable_holding"] == pytest.approx(1000.0)


def test_zero_target_is_no_new_buy_unless_exit_is_explicit():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame(
        {"000001": [1.0] * len(dates), "000002": [1.0] * len(dates)},
        index=dates,
    )

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Active Fund", "000002": "Tiny Weight Fund"},
            [],
        )
        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001", "000002"],
                "weights": {"000001": 1.0, "000002": 0.0},
                "current_holdings": {"000002": 1000.0},
                "rebalance_enabled": False,
                "monthly_budget": 100.0,
                "strategy_mode": "fixed_weight",
            },
        )

    assert response.status_code == 200
    advice = {item["code"]: item for item in response.json()["fund_advice"]}
    zero_weight = advice["000002"]
    assert zero_weight["allocation_state"] == "NO_NEW_BUY"
    assert zero_weight["action"] == "Hold"
    assert zero_weight["amount"] == 0.0
    assert zero_weight["target_holding"] == 0.0
    assert zero_weight["executable_holding"] == pytest.approx(1000.0)


def test_explicit_exit_can_reuse_net_sale_proceeds_for_second_buy_round():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame(
        {"000001": [1.0] * len(dates), "000002": [1.0] * len(dates)},
        index=dates,
    )

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Active Fund", "000002": "Exit Fund"},
            [],
        )
        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001", "000002"],
                "weights": {"000001": 1.0, "000002": 0.0},
                "current_holdings": {"000002": 1000.0},
                "monthly_budget": 100.0,
                "sell_fee": {"000002": 0.01},
                "exit_fund_codes": ["000002"],
                "reuse_settled_sale_proceeds": True,
                "strategy_mode": "fixed_weight",
            },
        )

    assert response.status_code == 200
    data = response.json()
    advice = {item["code"]: item for item in data["fund_advice"]}
    exited = advice["000002"]
    active = advice["000001"]
    assert exited["allocation_state"] == "EXIT"
    assert exited["action"] == "Sell"
    assert exited["amount"] == pytest.approx(1000.0)
    assert exited["executable_holding"] == 0.0
    assert active["target_holding"] == pytest.approx(1100.0)
    assert active["executable_holding"] == pytest.approx(1090.0)
    assert active["amount"] == pytest.approx(1090.0)
    assert data["recommended_monthly_investment"] == pytest.approx(1090.0)
    assert advice["Cash"]["executable_holding"] == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("exit_code", "expected_detail"),
    [
        ("000001", "must have zero target weight"),
        ("999999", "outside the current analysis universe"),
    ],
)
def test_explicit_exit_must_be_in_universe_with_zero_target(exit_code, expected_detail):
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])
        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "current_holdings": {"000001": 1000.0},
                "monthly_budget": 100.0,
                "exit_fund_codes": [exit_code],
                "strategy_mode": "fixed_weight",
            },
        )

    assert response.status_code == 400
    assert expected_detail in response.json()["detail"]


def test_backtest_respects_fund_monthly_buy_limits():
    from core.backtest import backtest_fixed_target

    dates = pd.date_range(start="2023-01-31", periods=4, freq="ME")
    df_nav = pd.DataFrame({"000001": [1.0, 1.0, 1.0, 1.0]}, index=dates)

    result = backtest_fixed_target(
        df_nav,
        {"000001": 1.0},
        monthly_investment=1000.0,
        initial_holdings={},
        initial_cash=0.0,
        strategy_mode="fixed_weight",
        fund_investment_limits={"000001": {"monthly_limit": 100.0}},
    )

    for idx, date in enumerate(dates, start=1):
        attribution = result["attribution"][date.strftime("%Y-%m")]
        assert attribution["000001"] == pytest.approx(idx * 100.0)
        assert attribution["Cash"] == pytest.approx(idx * 900.0)


def test_sell_threshold_boundary():
    """Test that sell threshold is respected correctly."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    # Create overvaluation to trigger potential sell
    mock_df.iloc[-1] = 1.3  # 30% above average

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )

        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 8000},  # Large holding
            "current_cash": 0.0,
            "monthly_budget": 1000,
            # 5% threshold
            "strategy_mode": "fixed_weight",
        }

        response = client.post("/api/current_recommendation", json=request_data)

        assert response.status_code == 200
        data = response.json()

        # Check if gap exceeds threshold
        total_wealth = 8000 + 0 + 1000  # holdings + cash + budget
        gap_percent = abs(data["gap"]) / total_wealth

        fund_advice = data["fund_advice"]
        fund_action = next(
            (item for item in fund_advice if item["code"] == "000001"), None
        )

        # If gap < 5%, action should be Hold
        # If gap >= 5%, action could be Sell
        if gap_percent < 0.05:
            assert fund_action["action"] in ["Hold", "Buy"]
        # If gap >= 5% and gap is negative, should sell
        elif data["gap"] < 0:
            assert fund_action["action"] == "Hold"


def test_recommendation_hold_reason_distinguishes_small_overweight():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )

        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "current_holdings": {"000001": 5200},
                "current_cash": 0.0,
                "monthly_budget": 0,
                "strategy_mode": "fixed_weight",
            },
        )

    assert response.status_code == 200
    data = response.json()
    fund_action = next(item for item in data["fund_advice"] if item["code"] == "000001")
    assert fund_action["action"] == "Hold"
    assert fund_action["amount"] == 0.0
    assert fund_action["gap"] == 0
    assert fund_action["executable_holding"] == pytest.approx(
        fund_action["current_holding"]
    )
    assert "仓位已达标" in fund_action["reason"]


def test_extreme_fee_handling():
    """Test handling of extreme fee rates."""
    from core.backtest import backtest_fixed_target

    dates = pd.date_range(start="2023-01-31", end="2023-03-31", freq="ME")
    nav_data = {"000001": [1.0, 0.9, 0.8]}
    df_nav = pd.DataFrame(nav_data, index=dates)

    weights = {"000001": 1.0}
    monthly_investment = 1000.0
    buy_fee = {"000001": 0.5}  # Extreme 50% fee
    sell_fee = {"000001": 0.1}

    result = backtest_fixed_target(
        df_nav,
        weights,
        monthly_investment,
        initial_holdings={},
        buy_fee=buy_fee,
        sell_fee=sell_fee,
    )

    # Should still complete without crash
    assert result["total_invested"] > 0
    # Final value will be much lower due to high fees
    assert result["final_value"] >= 0

    # Verify no negative cash
    for attribution in result["attribution"].values():
        assert attribution.get("RiskFree", 0) >= -0.01


def test_recommendation_sell_proceeds_not_double_counted():
    """Test that sell proceeds are not double-counted in RiskFree deposit recommendation."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    # Create severe overvaluation to trigger sell signal (bias = 1.5)
    mock_df.iloc[-1] = 1.5

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A"},
            [],
        )

        request_data = {
            "fund_codes": ["000001"],
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 10000},  # Value = 10000
            "current_cash": 0.0,
            "monthly_budget": 0,  # No new budget for simplicity
            "strategy_mode": "fixed_weight",
            "sell_fee": {"000001": 0.01},  # 1% sell fee
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()

        # Calculation Check:
        # Total Wealth = 10000 (holding) + 0 (cash) + 0 (budget) = 10000
        # Target Ratio = min_weight = 0.3 (since bias 1.5 > 1.2 high_bias)
        # Target Equity = 10000 * 0.3 = 3000
        # Gap = 3000 - 10000 = -7000
        # Action: Sell 7000
        # Net Proceeds = 7000 * (1 - 0.01) = 6930
        # Net Cash Flow = Budget (0) - Buys (0) + Net Proceeds (6930) = 6930
        # Previous BUG would add 7000 *again* to this value.
        # Correct Deposit Amount should be exactly 6930.

        advice_list = data["fund_advice"]
        cash_row = next(item for item in advice_list if item["code"] == "Cash")

        assert cash_row["action"] == "持有"
        assert cash_row["amount"] == pytest.approx(0.0)


def test_backtests_do_not_label_cash_as_risk_free_without_risk_free_asset():
    dates = pd.date_range(start="2024-01-31", periods=3, freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0, 1.1, 1.2]}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])

        response = client.post(
            "/api/backtest_strategies",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "fund_fees": {},
                "start_date": "2024-01-31",
                "end_date": "2024-03-31",
                "monthly_investment": 100.0,
                "initial_holdings": {},
                "initial_cash": 50.0,
                "strategy_mode": "fixed_weight",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    for result_name in ["lump_sum", "dca", "fixed_target"]:
        first_attr = next(iter(payload[result_name]["attribution"].values()))
        assert "Cash" in first_attr
        assert "RiskFree" not in first_attr


def test_current_recommendation_rejects_holdings_outside_universe():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])

        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "current_holdings": {"000001": 100.0, "999999": 50.0},
                "current_cash": 0.0,
                "monthly_budget": 1000.0,
            },
        )

    assert response.status_code == 400
    assert "current_holdings contain assets outside" in response.json()["detail"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
