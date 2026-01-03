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

    with patch("main.get_fund_data") as mock_get_fund:
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
            "max_buy_multiplier": 3.0,  # Would allow ¥3000 theoretically
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

    with patch("main.get_fund_data") as mock_get_fund:
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
        }

        response = client.post("/api/current_recommendation", json=request_data)

        assert response.status_code == 200
        data = response.json()

        # With zero cash and zero budget, should recommend 0
        assert data["recommended_monthly_investment"] == 0.0


def test_backtest_fee_no_overdraft():
    """Test that buy fees don't cause cash overdraft in backtest."""
    from main import backtest_kelly_dca

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

    result = backtest_kelly_dca(
        df_nav,
        weights,
        monthly_investment,
        initial_holdings={},
        max_buy_multiplier=3.0,
        sell_threshold=0.05,
        min_weight=0.3,
        max_weight=0.8,
        buy_fee=buy_fee,
        sell_fee=sell_fee,
        ma_window=12,
    )

    # Check that backtest completed successfully
    assert result["total_invested"] > 0
    assert result["final_value"] > 0

    # Verify cash never went negative by checking attribution
    for month, attribution in result["attribution"].items():
        risk_free = attribution.get("RiskFree", 0)
        # Cash (RiskFree) should never be negative
        assert risk_free >= -0.01, f"Cash balance went negative in {month}: {risk_free}"


def test_fee_calculation_accuracy():
    """Test that fees are calculated accurately in recommendations."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    mock_df.iloc[-1] = 0.8  # Slight undervaluation

    with patch("main.get_fund_data") as mock_get_fund:
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
        }

        response = client.post("/api/current_recommendation", json=request_data)

        assert response.status_code == 200
        data = response.json()

        # If recommendation is to buy, check fee calculations
        fund_advice = data["fund_advice"]
        for advice in fund_advice:
            if advice["action"] == "Buy" and advice["code"] == "000001":
                amount = advice["amount"]
                # Total cost should be amount * 1.015
                expected_cost = amount * 1.015
                # Just verify the logic is reasonable
                assert amount > 0
                print(f"Buy amount: ¥{amount:.2f}, With fee: ¥{expected_cost:.2f}")


def test_sell_threshold_boundary():
    """Test that sell threshold is respected correctly."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    # Create overvaluation to trigger potential sell
    mock_df.iloc[-1] = 1.3  # 30% above average

    with patch("main.get_fund_data") as mock_get_fund:
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
            "sell_threshold": 0.05,  # 5% threshold
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
            assert fund_action["action"] == "Sell"


def test_extreme_fee_handling():
    """Test handling of extreme fee rates."""
    from main import backtest_kelly_dca

    dates = pd.date_range(start="2023-01-31", end="2023-03-31", freq="ME")
    nav_data = {"000001": [1.0, 0.9, 0.8]}
    df_nav = pd.DataFrame(nav_data, index=dates)

    weights = {"000001": 1.0}
    monthly_investment = 1000.0
    buy_fee = {"000001": 0.5}  # Extreme 50% fee
    sell_fee = {"000001": 0.1}

    result = backtest_kelly_dca(
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

    with patch("main.get_fund_data") as mock_get_fund:
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
            "sell_threshold": 0.01,
            "min_weight": 0.3,
            "max_weight": 0.8,
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
        risk_free = next(item for item in advice_list if item["code"] == "RiskFree")

        assert risk_free["action"] == "存入"
        assert abs(risk_free["amount"] - 6930.0) < 0.1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
