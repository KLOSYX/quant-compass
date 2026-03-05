from fastapi.testclient import TestClient
from unittest.mock import patch
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
