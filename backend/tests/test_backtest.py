from fastapi.testclient import TestClient
from main import app
from .mock_data import mock_fund_name_em, mock_fund_open_fund_info_em

client = TestClient(app)

def test_backtest_strategies_success(mocker):
    """Test successful strategy backtesting."""
    mocker.patch('akshare.fund_name_em', return_value=mock_fund_name_em())
    mocker.patch('akshare.fund_open_fund_info_em', side_effect=mock_fund_open_fund_info_em)

    request_data = {
        "fund_codes": ["000001", "000002"],
        "weights": {"000001": 0.6, "000002": 0.4},
        "fund_fees": {"000001": 0.015, "000002": 0.01},
        "start_date": "2023-01-15",
        "end_date": "2023-03-15",
        "monthly_investment": 1000,
        "risk_free_rate": 0.02
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
