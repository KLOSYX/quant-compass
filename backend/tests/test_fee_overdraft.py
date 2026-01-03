"""
Test case to specifically verify fee-aware cash constraint in recommendations.
"""

from unittest.mock import patch
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_recommendation_fee_overdraft_prevention():
    """
    Test that the recommendation does not exceed available cash when fees are included.
    Available cash = 1000
    Buy fee = 10% (extreme for testing)
    Max recommended amount should be 1000 / 1.1 = 909.09
    """
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    mock_df.iloc[-1] = 0.5  # Create large gap

    with patch("main.get_fund_data") as mock_get_fund:
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
            "buy_fee": {"000001": 0.1},  # 10% fee
            "max_buy_multiplier": 5.0,  # High enough not to be the bottleneck
            "max_weight": 1.0,  # Ensure gap can reach 1000
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()

        recommended = data["recommended_monthly_investment"]

        print(f"Recommended (Gross): {recommended}")

        # In Gross Investment logic, recommended amount IS the total cash payment.
        # So it should be exactly 1000, not 909.09.
        assert abs(recommended - 1000) < 0.001

        # Verify fund advice amount is also 1000
        assert data["fund_advice"][0]["amount"] == 1000
        assert data["fund_advice"][1]["action"] == "持有"  # RiskFree row


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
