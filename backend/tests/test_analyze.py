from fastapi.testclient import TestClient
from unittest.mock import patch
import pandas as pd
from main import app

from .mock_data import mock_fund_name_em, mock_fund_open_fund_info_em

client = TestClient(app)


def test_analyze_portfolio_success():
    """Test successful portfolio analysis."""
    with (
        patch("akshare.fund_name_em", return_value=mock_fund_name_em()),
        patch(
            "akshare.fund_open_fund_info_em", side_effect=mock_fund_open_fund_info_em
        ),
    ):
        request_data = {
            "fund_codes": ["000001", "000002"],
            "fund_fees": {"000001": 0.015, "000002": 0.01},
            "start_date": "2023-01-15",
            "end_date": "2023-03-15",
            "risk_free_rate": 0.02,
        }

        response = client.post("/api/analyze", json=request_data)

    assert response.status_code == 200
    data = response.json()
    assert "efficient_frontier" in data
    assert "fund_names" in data
    assert "backtest_period" in data
    assert len(data["efficient_frontier"]) > 0
    assert any("全样本静态估计" in warning for warning in data["warnings"])
    assert data["fund_names"] == {
        "000001": "Fund A",
        "000002": "Fund B",
        "RiskFree": "无风险资产",
    }


def test_analyze_no_funds_fails():
    """Test analysis with no funds and no risk-free rate, expecting failure."""
    request_data = {
        "fund_codes": [],
        "fund_fees": {},
    }
    response = client.post("/api/analyze", json=request_data)
    assert response.status_code == 400


def test_analyze_with_risk_free_only():
    """Test analysis with only the risk-free asset."""
    with (
        patch("akshare.fund_name_em", return_value=mock_fund_name_em()),
        patch(
            "akshare.fund_open_fund_info_em", side_effect=mock_fund_open_fund_info_em
        ),
    ):
        request_data = {
            "fund_codes": [],
            "fund_fees": {},
            "start_date": "2023-01-15",
            "end_date": "2023-03-15",
            "risk_free_rate": 0.03,
        }
        response = client.post("/api/analyze", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert len(data["efficient_frontier"]) == 1
    assert data["efficient_frontier"][0]["risk"] == 0
    assert "return" in data["efficient_frontier"][0]
    assert data["efficient_frontier"][0]["weights"] == {"RiskFree": 1.0}
    assert data["fund_names"] == {"RiskFree": "无风险资产"}


def test_analyze_warns_management_fee_not_reapplied_by_default():
    with (
        patch("akshare.fund_name_em", return_value=mock_fund_name_em()),
        patch(
            "akshare.fund_open_fund_info_em", side_effect=mock_fund_open_fund_info_em
        ),
    ):
        request_data = {
            "fund_codes": ["000001"],
            "fund_fees": {"000001": 0.015},
            "start_date": "2023-01-15",
            "end_date": "2023-03-15",
        }
        response = client.post("/api/analyze", json=request_data)
    assert response.status_code == 200
    data = response.json()
    assert any("默认不额外扣减管理费" in warning for warning in data["warnings"])


def test_analyze_returns_asset_diagnostics():
    dates = pd.date_range(start="2020-01-31", periods=60, freq="ME")
    mock_df = pd.DataFrame(
        {
            "270023": (
                1
                + pd.Series(
                    [0.03 if i % 2 == 0 else -0.01 for i in range(60)], index=dates
                )
            ).cumprod(),
            "016149": (
                1
                + pd.Series(
                    [0.004 if i % 2 == 0 else 0.002 for i in range(60)], index=dates
                )
            ).cumprod(),
            "002611": (
                1
                + pd.Series(
                    [
                        0.025 if i % 3 == 0 else -0.005 if i % 3 == 1 else 0.018
                        for i in range(60)
                    ],
                    index=dates,
                )
            ).cumprod(),
            "RiskFree": (1 + pd.Series([0.02 / 12] * 60, index=dates)).cumprod(),
        },
        index=dates,
    )

    with patch("api.routes.get_fund_data") as mock_get_fund:
        mock_get_fund.return_value = (
            mock_df,
            {
                "270023": "Fund A",
                "016149": "Bond Fund",
                "002611": "Gold Fund",
                "RiskFree": "无风险资产",
            },
            [],
        )

        response = client.post(
            "/api/analyze",
            json={
                "fund_codes": ["270023", "016149", "002611"],
                "fund_fees": {},
                "start_date": "2020-01-31",
                "end_date": "2024-12-31",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    diagnostics = {item["code"]: item for item in payload["asset_diagnostics"]}

    assert diagnostics["016149"]["sample_total_return"] > 0
    assert diagnostics["016149"]["frontier_points_used"] > 0
    assert diagnostics["016149"]["max_frontier_weight"] > 0.0
    assert diagnostics["016149"]["status"] == "selected_on_frontier"
    assert diagnostics["RiskFree"]["status"] == "risk_free_anchor"
