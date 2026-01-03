"""
Test cases for edge conditions in recommendation logic.
"""

from unittest.mock import patch
import pandas as pd
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_recommendation_zero_investment():
    """Test recommendation with zero budget and zero cash."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)

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
            "monthly_budget": 0,
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()
        assert data["recommended_monthly_investment"] == 0.0


def test_single_asset_100_percent():
    """Test when a single asset has 100% weight."""
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates), "000002": [2.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)

    with patch("main.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A", "000002": "Fund B"},
            [],
        )

        request_data = {
            "fund_codes": ["000001", "000002"],
            "weights": {"000001": 1.0, "000002": 0.0},
            "current_holdings": {"000001": 1000, "000002": 0},
            "current_cash": 0.0,
            "monthly_budget": 1000,
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        data = response.json()

        # Check that 000002 is essentially ignored or treated as 0 target
        advice_map = {item["code"]: item for item in data["fund_advice"]}
        assert advice_map["000002"]["target_holding"] == 0
        assert advice_map["000001"]["target_holding"] > 0
