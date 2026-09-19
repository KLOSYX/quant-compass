from unittest.mock import patch

from fastapi.testclient import TestClient
from main import app

from .mock_data import mock_fund_name_em, mock_fund_open_fund_info_em
import pandas as pd
import pytest

client = TestClient(app)


def test_backtest_strategies_success():
    """Test successful strategy backtesting."""
    with (
        patch("akshare.fund_name_em", return_value=mock_fund_name_em()),
        patch(
            "akshare.fund_open_fund_info_em", side_effect=mock_fund_open_fund_info_em
        ),
    ):
        request_data = {
            "fund_codes": ["000001", "000002"],
            "weights": {"000001": 0.6, "000002": 0.4},
            "fund_fees": {"000001": 0.015, "000002": 0.01},
            "start_date": "2023-01-15",
            "end_date": "2023-03-15",
            "monthly_investment": 1000,
            "risk_free_rate": 0.02,
            "buy_fee": {"000001": 0.0015, "000002": 0.0015},
            "sell_fee": {"000001": 0.005, "000002": 0.005},
        }

        response = client.post("/api/backtest_strategies", json=request_data)

        assert response.status_code == 200
        data = response.json()
        assert "lump_sum" in data
        assert "dca" in data
        assert data["lump_sum"]["total_invested"] > 0
        assert data["lump_sum"]["final_value"] > 0
        assert len(data["lump_sum"]["history"]) > 0
        assert data["dca"]["total_invested"] > 0
        assert data["dca"]["final_value"] > 0
        assert len(data["dca"]["history"]) > 0

        assert "fixed_target" in data
        assert data["fixed_target"]["total_invested"] > 0
        assert data["fixed_target"]["final_value"] > 0
        assert len(data["fixed_target"]["history"]) > 0
        assert data["fixed_target"]["strategy_mode"] == "fixed_weight"
        assert "optimizer_info" in data["fixed_target"]
        assert data["fixed_target"]["optimizer_info"]["policy"] == "fixed_weight"

        # Verify Annualized Return is present
        assert "annualized_return" in data["lump_sum"]
        assert isinstance(data["lump_sum"]["annualized_return"], float)
        assert "annualized_return" in data["dca"]
        assert isinstance(data["dca"]["annualized_return"], float)
        assert "annualized_return" in data["fixed_target"]
        assert isinstance(data["fixed_target"]["annualized_return"], float)


def test_current_recommendation_success():
    """Test current recommendation endpoint."""
    # Mock data return for get_fund_data
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    # Make sure we have enough data (13 months)
    data = {"000001": [1.0] * len(dates), "000002": [2.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    # create price that is undervalued (0.5 vs 1.0 avg)
    mock_df.iloc[-1] = mock_df.iloc[-1] * 0.5

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A", "000002": "Fund B"},
            [],
        )

        request_data = {
            "fund_codes": ["000001", "000002"],
            "weights": {"000001": 0.6, "000002": 0.4},
            "current_holdings": {"000001": 5000, "000002": 3000},
            "current_cash": 2000.0,
            "monthly_budget": 1000,
            "strategy_mode": "fixed_weight",
            "buy_fee": {"000001": 0.0015, "000002": 0.0015},
            "sell_fee": {"000001": 0.005, "000002": 0.005},
        }

        response = client.post("/api/current_recommendation", json=request_data)

        assert response.status_code == 200
        data = response.json()

        assert "market_signal" in data
        assert "allocation_signal" in data
        assert "target_equity_ratio" in data
        assert "recommended_monthly_investment" in data
        assert data["current_equity_value"] == 8000
        assert data["current_cash"] == 2000.0
        # Since last price is 0.5 of previous, MA will be higher -> Undervalued
        assert data["market_signal"] == "neutral"
        assert data["allocation_signal"] == "neutral"

        assert "fund_advice" in data
        advice_list = data["fund_advice"]
        risk_free = next(
            (item for item in advice_list if item["code"] == "RiskFree"), None
        )
        cash_row = next((item for item in advice_list if item["code"] == "Cash"), None)
        assert risk_free is None
        assert cash_row is not None
        assert cash_row["name"] == "现金"
        # No explicit RiskFree asset was selected, so all non-risky allocation stays in cash.
        expected_target_equity = 11000 * data["target_equity_ratio"]
        expected_cash = 11000 - expected_target_equity
        assert abs(cash_row["target_holding"] - expected_cash) < 1.0


def test_backtest_strategies_supports_cash_sleeve_without_explicit_risk_free_rate():
    dates = pd.date_range(start="2024-01-01", periods=14, freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0 - 0.02 * i for i in range(14)]}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])

        conservative = client.post(
            "/api/backtest_strategies",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 0.6, "RiskFree": 0.4},
                "fund_fees": {"000001": 0.0},
                "start_date": "2024-01-01",
                "end_date": "2025-02-28",
                "monthly_investment": 1000,
                "risk_free_rate": None,
                "strategy_mode": "fixed_weight",
            },
        )
        aggressive = client.post(
            "/api/backtest_strategies",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "fund_fees": {"000001": 0.0},
                "start_date": "2024-01-01",
                "end_date": "2025-02-28",
                "monthly_investment": 1000,
                "risk_free_rate": None,
                "strategy_mode": "fixed_weight",
            },
        )

    assert conservative.status_code == 200
    assert aggressive.status_code == 200
    conservative_payload = conservative.json()
    aggressive_payload = aggressive.json()
    assert (
        conservative_payload["lump_sum"]["final_value"]
        > aggressive_payload["lump_sum"]["final_value"]
    )


def test_current_recommendation_separates_riskfree_and_cash_rows():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])

        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 0.6, "RiskFree": 0.4},
                "current_holdings": {"000001": 0.0, "RiskFree": 1000.0},
                "current_cash": 100.0,
                "monthly_budget": 100.0,
                "minimum_cash_reserve": 100.0,
                "strategy_mode": "fixed_weight",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    advice_list = payload["fund_advice"]
    risk_free = next(item for item in advice_list if item["code"] == "RiskFree")
    cash_row = next(item for item in advice_list if item["code"] == "Cash")

    assert abs(payload["target_equity_value"] - 660.0) < 1e-9
    assert abs(payload["target_risk_free_value"] - 440.0) < 1e-9
    assert abs(payload["target_cash_value"] - 100.0) < 1e-9
    assert risk_free["action"] == "Sell"
    assert risk_free["amount"] == pytest.approx(560, abs=0.01)
    assert abs(risk_free["target_holding"] - 440.0) < 1e-9
    assert cash_row["action"] == "持有"
    assert abs(cash_row["target_holding"] - 100.0) < 1e-9
    assert abs(risk_free["executable_holding"] - 440.0) < 1e-9
    assert abs(cash_row["executable_holding"] - 100.0) < 1e-9
    assert "target_deviations" in payload
    assert "risk_limit_enforceable" not in payload


def test_current_recommendation_default_fixed_mode():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates), "000002": [2.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    mock_df.iloc[-1] = mock_df.iloc[-1] * 0.5

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A", "000002": "Fund B"},
            [],
        )

        request_data = {
            "fund_codes": ["000001", "000002"],
            "weights": {"000001": 0.6, "000002": 0.4},
            "current_holdings": {"000001": 5000, "000002": 3000},
            "current_cash": 2000.0,
            "monthly_budget": 1000,
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        payload = response.json()
        assert payload["strategy_mode"] == "fixed_weight"
        assert "optimizer_info" in payload
        assert "allocation_signal" in payload
        assert payload["target_equity_ratio"] == pytest.approx(9000 / 11000)
        assert payload["optimizer_info"]["policy"] == "fixed_weight"
        assert "projected_account_risk" in payload


def test_current_recommendation_changes_with_selected_riskfree_sleeve():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)
    mock_df.iloc[-1] = 0.5

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])

        conservative = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 0.6, "RiskFree": 0.4},
                "current_holdings": {"000001": 5000},
                "current_cash": 2000.0,
                "monthly_budget": 1000,
                "strategy_mode": "fixed_weight",
            },
        )
        aggressive = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "current_holdings": {"000001": 5000},
                "current_cash": 2000.0,
                "monthly_budget": 1000,
                "strategy_mode": "fixed_weight",
            },
        )

    assert conservative.status_code == 200
    assert aggressive.status_code == 200
    conservative_payload = conservative.json()
    aggressive_payload = aggressive.json()
    assert (
        conservative_payload["target_equity_value"]
        < aggressive_payload["target_equity_value"]
    )


def test_current_recommendation_optimized_mode_changes_with_selected_riskfree_sleeve():
    dates = pd.date_range(start="2023-01-01", periods=18, freq="ME")
    mock_df = pd.DataFrame(
        {"000001": [1.0 * (1.01**i) for i in range(len(dates))]}, index=dates
    )

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])

        conservative = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 0.6, "RiskFree": 0.4},
                "current_holdings": {"000001": 5000},
                "current_cash": 2000.0,
                "monthly_budget": 1000,
                "enable_cvar_constraint": False,
                "enable_drawdown_constraint": False,
            },
        )
        aggressive = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "current_holdings": {"000001": 5000},
                "current_cash": 2000.0,
                "monthly_budget": 1000,
                "enable_cvar_constraint": False,
                "enable_drawdown_constraint": False,
            },
        )

    assert conservative.status_code == 200
    assert aggressive.status_code == 200
    conservative_payload = conservative.json()
    aggressive_payload = aggressive.json()
    assert (
        conservative_payload["target_equity_value"]
        < aggressive_payload["target_equity_value"]
    )


def test_optimized_mode_uses_valuation_market_signal():
    dates = pd.date_range(start="2023-01-01", periods=40, freq="ME")
    trend = [1.0 * (1.02**i) for i in range(len(dates))]
    trend[-1] = trend[-1] * 1.3  # push price significantly above MA
    mock_df = pd.DataFrame({"000001": trend}, index=dates)

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
            "current_cash": 1000.0,
            "monthly_budget": 1000,
            "strategy_mode": "fixed_weight",
            "risk_free_rate": 0.02,
        }

        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        payload = response.json()

        # Market signal should reflect valuation (price vs MA), independent from fixed-target bound signal.
        assert payload["market_signal"] == "neutral"
        assert payload["allocation_signal"] in {"undervalued", "neutral", "overvalued"}


def test_invalid_strategy_params_return_400():
    request_data = {
        "fund_codes": ["000001"],
        "weights": {"000001": 1.0},
        "fund_fees": {"000001": 0.015},
        "start_date": "2023-01-15",
        "end_date": "2023-03-15",
        "monthly_investment": 1000,
        "estimation_window": 0,
    }

    response = client.post("/api/backtest_strategies", json=request_data)
    assert response.status_code == 400


def test_invalid_cvar_confidence_returns_400():
    request_data = {
        "fund_codes": ["000001"],
        "weights": {"000001": 1.0},
        "fund_fees": {"000001": 0.015},
        "start_date": "2023-01-15",
        "end_date": "2023-03-15",
        "monthly_investment": 1000,
        "cvar_confidence": 1.2,
    }

    response = client.post("/api/backtest_strategies", json=request_data)
    assert response.status_code == 400


def test_invalid_min_max_returns_400():
    request_data = {
        "fund_codes": ["000001"],
        "weights": {"000001": -1.0},
        "current_holdings": {"000001": 0},
        "current_cash": 0.0,
        "monthly_budget": 1000,
    }

    response = client.post("/api/current_recommendation", json=request_data)
    assert response.status_code == 400


def test_fixed_mode_reports_risk_without_scaling_target():
    dates = pd.date_range(start="2024-01-01", end="2025-01-01", freq="ME")
    data = {"000001": [1.0] * len(dates)}
    mock_df = pd.DataFrame(data, index=dates)
    mock_df.iloc[-1] = 0.5

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
            "strategy_mode": "fixed_weight",
            "enable_cvar_constraint": True,
            "cvar_limit": 0.0001,
            "enable_drawdown_constraint": True,
            "max_drawdown_limit": 0.0001,
        }
        response = client.post("/api/current_recommendation", json=request_data)
        assert response.status_code == 200
        payload = response.json()
        assert payload["market_signal"] == "neutral"
        assert abs(payload["target_equity_ratio"] - 1.0) < 1e-9


def test_backtest_strategies_changes_with_selected_riskfree_sleeve():
    with (
        patch("akshare.fund_name_em", return_value=mock_fund_name_em()),
        patch(
            "akshare.fund_open_fund_info_em", side_effect=mock_fund_open_fund_info_em
        ),
    ):
        conservative = client.post(
            "/api/backtest_strategies",
            json={
                "fund_codes": ["000001", "000002"],
                "weights": {"000001": 0.36, "000002": 0.24, "RiskFree": 0.40},
                "fund_fees": {"000001": 0.015, "000002": 0.01},
                "start_date": "2023-01-15",
                "end_date": "2023-03-15",
                "monthly_investment": 1000,
                "risk_free_rate": 0.02,
                "strategy_mode": "fixed_weight",
            },
        )
        aggressive = client.post(
            "/api/backtest_strategies",
            json={
                "fund_codes": ["000001", "000002"],
                "weights": {"000001": 0.6, "000002": 0.4},
                "fund_fees": {"000001": 0.015, "000002": 0.01},
                "start_date": "2023-01-15",
                "end_date": "2023-03-15",
                "monthly_investment": 1000,
                "risk_free_rate": 0.02,
                "strategy_mode": "fixed_weight",
            },
        )

    assert conservative.status_code == 200
    assert aggressive.status_code == 200
    conservative_payload = conservative.json()
    aggressive_payload = aggressive.json()
    assert (
        conservative_payload["fixed_target"]["final_value"]
        < aggressive_payload["fixed_target"]["final_value"]
    )


def test_current_recommendation_omits_synthetic_risk_free_by_default():
    dates = pd.date_range(start="2024-01-01", periods=13, freq="ME")
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
                "strategy_mode": "fixed_weight",
            },
        )
    assert response.status_code == 200
    assert mock_get_fund.call_args.args[3] is None
    assert all(item["code"] != "RiskFree" for item in response.json()["fund_advice"])


def test_current_recommendation_rejects_retired_risk_free_holding():
    dates = pd.date_range(start="2024-01-01", periods=13, freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)
    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])
        response = client.post(
            "/api/current_recommendation",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "current_holdings": {"000001": 1000.0, "RiskFree": 300.0},
                "monthly_budget": 100.0,
                "strategy_mode": "fixed_weight",
            },
        )
    assert response.status_code == 400
    assert "RiskFree" in response.json()["detail"]


def test_backtest_rejects_retired_risk_free_holding():
    dates = pd.date_range(start="2024-01-01", periods=13, freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)
    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])
        response = client.post(
            "/api/backtest_strategies",
            json={
                "fund_codes": ["000001"],
                "weights": {"000001": 1.0},
                "fund_fees": {},
                "start_date": "2024-01-01",
                "end_date": "2025-01-31",
                "monthly_investment": 100.0,
                "initial_holdings": {"000001": 1000.0, "RiskFree": 300.0},
                "strategy_mode": "fixed_weight",
            },
        )
    assert response.status_code == 400
    assert "RiskFree" in response.json()["detail"]
