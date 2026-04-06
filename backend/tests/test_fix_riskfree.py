import sys
import os
import asyncio
from datetime import date
from unittest.mock import patch

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from main import get_current_recommendation, CurrentRecommendationRequest


def test_riskfree_post_trade_calculation():
    # Mock get_fund_data to avoid external API calls
    with patch("main.get_fund_data") as mock_get_data:
        # Mock Data: 1 Fund + RiskFree
        import pandas as pd

        idx = pd.date_range(end=date.today(), periods=12, freq="ME")
        mock_df = pd.DataFrame(
            {"FundA": [1.0] * len(idx)},
            index=idx,
        )

        mock_get_data.return_value = (mock_df, {"FundA": "Test Fund"}, [])

        request = CurrentRecommendationRequest(
            fund_codes=["FundA"],
            weights={"FundA": 0.5},
            current_holdings={"FundA": 0.0, "RiskFree": 1000.0},
            current_cash=0.0,
            monthly_budget=100.0,
            max_buy_multiplier=1.0,
            sell_threshold=0.05,
            min_weight=0.5,
            max_weight=0.5,
            ma_window=5,
        )

        # Async run wrapper
        result = asyncio.run(get_current_recommendation(request))

        riskfree_advice = next(
            item for item in result["fund_advice"] if item["code"] == "RiskFree"
        )
        cash_advice = next(
            item for item in result["fund_advice"] if item["code"] == "Cash"
        )

        assert riskfree_advice["ideal_holding"] == 0.0
        assert riskfree_advice["target_holding"] == 0.0
        assert riskfree_advice["action"] == "Sell"
        assert cash_advice["target_holding"] == 550.0
