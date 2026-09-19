import sys
import os
import asyncio
from datetime import date
from unittest.mock import patch

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.models import CurrentRecommendationRequest
from api.routes import get_current_recommendation


def test_riskfree_post_trade_calculation():
    # Mock get_fund_data to avoid external API calls
    with patch("api.routes.get_fund_data") as mock_get_data:
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
            weights={"FundA": 1.0},
            current_holdings={"FundA": 0.0, "RiskFree": 1000.0},
            current_cash=0.0,
            monthly_budget=100.0,
            rebalance_enabled=False,
            risk_free_rate=0.03,
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
        # A zero theoretical target does not authorize an automatic redemption.
        assert riskfree_advice["action"] == "Hold"
        assert riskfree_advice["executable_holding"] == 1000.0
        assert riskfree_advice["amount"] == 0.0
        assert cash_advice["target_holding"] == 0.0
