from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from fastapi.testclient import TestClient

from main import (
    app,
    backtest_dca,
    backtest_kelly_dca,
    backtest_lump_sum,
    calculate_efficient_frontier,
    decompose_selected_weights,
    get_effective_single_weight_cap,
)

client = TestClient(app)


def test_decompose_selected_weights_preserves_full_and_risky_semantics():
    result = decompose_selected_weights(
        {"AssetA": 0.42, "AssetB": 0.18, "RiskFree": 0.40},
        ["AssetA", "AssetB", "RiskFree"],
    )

    assert result["full_weights"].to_dict() == {
        "AssetA": 0.42,
        "AssetB": 0.18,
        "RiskFree": 0.40,
    }
    assert abs(result["base_risky_ratio"] - 0.60) < 1e-9
    assert abs(result["base_risk_free_ratio"] - 0.40) < 1e-9
    assert result["risky_weights"].to_dict() == {
        "AssetA": 0.7,
        "AssetB": 0.3,
    }


def test_riskfree_holdings_and_cash_are_tracked_separately_in_baselines():
    dates = pd.date_range(start="2024-01-31", periods=3, freq="ME")
    df_nav = pd.DataFrame(
        {"AssetA": [1.0, 1.0, 1.0], "RiskFree": [1.0, 1.001, 1.002]},
        index=dates,
    )

    lump = backtest_lump_sum(
        df_nav,
        {"RiskFree": 1.0},
        total_investment=0.0,
        initial_holdings={"RiskFree": 1000.0},
        initial_cash=500.0,
    )
    dca = backtest_dca(
        df_nav,
        {"RiskFree": 1.0},
        monthly_investment=0.0,
        initial_holdings={"RiskFree": 1000.0},
        initial_cash=500.0,
    )

    assert abs(lump["final_value"] - 1502.0) < 1e-9
    assert abs(dca["final_value"] - 1502.0) < 1e-9


def test_kelly_backtest_changes_with_selected_riskfree_sleeve():
    dates = pd.date_range(start="2023-01-31", periods=6, freq="ME")
    df_nav = pd.DataFrame(
        {"AssetA": [1.0, 1.05, 1.1, 1.0, 1.08, 1.12], "RiskFree": [1.0] * 6},
        index=dates,
    )

    conservative = backtest_kelly_dca(
        df_nav,
        {"AssetA": 0.6, "RiskFree": 0.4},
        monthly_investment=1000.0,
        strategy_mode="legacy_linear",
        min_weight=0.5,
        max_weight=0.5,
    )
    aggressive = backtest_kelly_dca(
        df_nav,
        {"AssetA": 1.0},
        monthly_investment=1000.0,
        strategy_mode="legacy_linear",
        min_weight=0.5,
        max_weight=0.5,
    )

    assert conservative["effective_risky_weights"] == {"AssetA": 1.0}
    assert conservative["final_value"] < aggressive["final_value"]
    assert conservative["final_unit_nav"] < aggressive["final_unit_nav"]


def test_kelly_backtest_uses_lagged_signal():
    dates = pd.date_range(start="2023-01-31", periods=3, freq="ME")
    df_nav = pd.DataFrame({"AssetA": [1.0, 1.0, 0.1]}, index=dates)

    result = backtest_kelly_dca(
        df_nav,
        {"AssetA": 1.0},
        monthly_investment=1000.0,
        strategy_mode="legacy_linear",
        min_weight=0.0,
        max_weight=1.0,
        ma_window=2,
    )

    assert result["market_signal"] == "neutral"
    assert result["allocation_signal"] == "neutral"


def test_current_recommendation_does_not_double_count_management_fee_by_default():
    dates = pd.date_range(start="2024-01-01", periods=14, freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("main.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])
        request_data = {
            "fund_codes": ["000001"],
            "fund_fees": {"000001": 0.24},
            "weights": {"000001": 1.0},
            "current_holdings": {"000001": 0},
            "current_cash": 1000.0,
            "monthly_budget": 1000,
            "strategy_mode": "legacy_linear",
            "min_weight": 0.0,
            "max_weight": 1.0,
        }

        response = client.post("/api/current_recommendation", json=request_data)

    assert response.status_code == 200
    payload = response.json()
    assert payload["market_signal"] == "neutral"
    assert payload["allocation_signal"] == "neutral"
    assert payload["warnings"]


def test_analyze_returns_recommended_point_for_long_sample():
    dates = pd.date_range(start="2021-01-31", periods=36, freq="ME")
    mock_df = pd.DataFrame(
        {
            "000001": [1 + 0.01 * i for i in range(36)],
            "000002": [1 + 0.006 * i + (0.02 if i % 5 == 0 else 0) for i in range(36)],
        },
        index=dates,
    )

    with patch("main.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {"000001": "Fund A", "000002": "Fund B"},
            [],
        )
        request_data = {
            "fund_codes": ["000001", "000002"],
            "fund_fees": {"000001": 0.0, "000002": 0.0},
            "start_date": "2021-01-31",
            "end_date": "2023-12-31",
        }
        response = client.post("/api/analyze", json=request_data)

    assert response.status_code == 200
    payload = response.json()
    assert payload["recommended_point_index"] is not None
    recommended = payload["efficient_frontier"][payload["recommended_point_index"]]
    assert "robust_score" in recommended


def test_frontier_cleaning_does_not_break_single_asset_caps():
    dates = pd.date_range(start="2023-01-31", periods=12, freq="ME")
    df_nav = pd.DataFrame(
        {
            "AssetA": [1.0 + 0.02 * i for i in range(12)],
            "AssetB": [1.0 + 0.015 * i for i in range(12)],
            "AssetC": [1.0 + 0.01 * i for i in range(12)],
            "AssetD": [1.0 + 0.005 * i for i in range(12)],
        },
        index=dates,
    )

    optimized_weights = [0.5, 0.49, 0.005, 0.005]
    mock_result = SimpleNamespace(success=True, x=optimized_weights)

    with patch("core.frontier.minimize", return_value=mock_result):
        frontier = calculate_efficient_frontier(
            df_nav, {column: 0.0 for column in df_nav.columns}
        )

    assert frontier
    first_point = frontier[0]["weights"]
    cap = get_effective_single_weight_cap(len(df_nav.columns))

    assert max(first_point.values()) <= cap + 1e-9
    assert first_point["AssetC"] > 0
    assert first_point["AssetD"] > 0


def test_current_recommendation_rejects_weights_for_missing_assets():
    dates = pd.date_range(start="2024-01-01", periods=14, freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("main.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])
        request_data = {
            "fund_codes": ["000001"],
            "fund_fees": {"000001": 0.0},
            "weights": {"GhostAsset": 1.0},
            "current_holdings": {"000001": 1000.0},
            "current_cash": 0.0,
            "monthly_budget": 1000.0,
            "risk_free_rate": None,
            "strategy_mode": "legacy_linear",
            "min_weight": 0.0,
            "max_weight": 1.0,
        }

        response = client.post("/api/current_recommendation", json=request_data)

    assert response.status_code == 400
    assert "GhostAsset" in response.json()["detail"]


def test_backtest_strategies_rejects_weights_for_missing_assets():
    dates = pd.date_range(start="2024-01-01", periods=14, freq="ME")
    mock_df = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    with patch("main.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (mock_df, {"000001": "Fund A"}, [])
        request_data = {
            "fund_codes": ["000001"],
            "fund_fees": {"000001": 0.0},
            "weights": {"GhostAsset": 1.0},
            "start_date": "2024-01-01",
            "end_date": "2025-02-28",
            "monthly_investment": 1000.0,
            "risk_free_rate": None,
            "initial_holdings": {"000001": 1000.0},
            "strategy_mode": "legacy_linear",
            "min_weight": 0.0,
            "max_weight": 1.0,
        }

        response = client.post("/api/backtest_strategies", json=request_data)

    assert response.status_code == 400
    assert "GhostAsset" in response.json()["detail"]
