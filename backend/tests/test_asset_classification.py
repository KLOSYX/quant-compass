import asyncio
from unittest.mock import patch

import pandas as pd
import pytest

from api.models import (
    AnalysisRequest,
    CurrentRecommendationRequest,
    StrategyBacktestRequest,
)
from api.routes import get_current_recommendation
from core.backtest import backtest_fixed_target
from core.classification import (
    aggregate_category_values,
    calculate_exposure_metrics,
    normalize_asset_categories,
)


def test_asset_categories_require_explicit_valid_values_without_name_guessing():
    categories = normalize_asset_categories(
        ["E", "B", "M", "RiskFree"],
        {"E": "equity", "B": "bond", "M": "money_market"},
    )

    assert categories == {
        "E": "equity",
        "B": "bond",
        "M": "money_market",
        "RiskFree": "cash_equivalent",
    }


def test_unclassified_fund_defaults_to_other_and_is_treated_as_risk_asset():
    categories = normalize_asset_categories(["X"], {})
    metrics = calculate_exposure_metrics(
        {"X": 300.0}, total_wealth=1000.0, asset_categories=categories
    )

    assert categories["X"] == "other"
    assert metrics["category_values"] == {"other": 300.0}
    assert metrics["category_exposures"] == {"other": pytest.approx(0.3)}
    assert metrics["equity_exposure"] == 0.0
    assert metrics["risk_asset_exposure"] == pytest.approx(0.3)


def test_category_aggregation_keeps_low_risk_funds_separate_from_cash():
    categories = {"E": "equity", "M": "money_market", "C": "cash_equivalent"}
    values = aggregate_category_values({"E": 400.0, "M": 200.0, "C": 100.0}, categories)

    assert values == {"equity": 400.0, "money_market": 200.0, "cash_equivalent": 100.0}


@pytest.mark.parametrize("category", ["stock", "货币基金", ""])
def test_invalid_asset_category_is_rejected(category):
    with pytest.raises(ValueError, match="invalid asset category"):
        normalize_asset_categories(["A"], {"A": category})


def test_category_metadata_outside_universe_is_rejected():
    with pytest.raises(ValueError, match="outside the current analysis universe"):
        normalize_asset_categories(["A"], {"B": "bond"})


@pytest.mark.parametrize(
    "request_model",
    [AnalysisRequest, StrategyBacktestRequest, CurrentRecommendationRequest],
)
def test_dead_execution_parameters_are_deprecated_in_api_schema(request_model):
    properties = request_model.model_json_schema()["properties"]
    assert "max_buy_multiplier" not in properties
    assert "sell_threshold" not in properties


def test_recommendation_reports_fund_equity_risk_and_category_exposures():
    dates = pd.date_range("2024-01-31", periods=13, freq="ME")
    nav = pd.DataFrame({"E": [1.0] * 13, "B": [1.0] * 13, "M": [1.0] * 13}, index=dates)
    request = CurrentRecommendationRequest(
        fund_codes=["E", "B", "M"],
        fund_fees={},
        asset_categories={"E": "equity", "B": "bond", "M": "money_market"},
        weights={"E": 0.5, "B": 0.3, "M": 0.2},
        current_holdings={"E": 400.0, "M": 200.0},
        current_cash=400.0,
        monthly_budget=1000.0,
        strategy_mode="fixed_weight",
    )

    with patch(
        "api.routes.get_fund_data",
        return_value=(nav, {"E": "Equity", "B": "Bond", "M": "Money"}, []),
    ):
        result = asyncio.run(get_current_recommendation(request))

    assert result["target_fund_ratio"] == pytest.approx(0.8)
    assert result["target_equity_exposure"] == pytest.approx(0.4)
    assert result["target_risk_asset_exposure"] == pytest.approx(0.64)
    assert result["target_category_exposures"] == {
        "equity": pytest.approx(0.4),
        "bond": pytest.approx(0.24),
        "money_market": pytest.approx(0.16),
    }
    assert result["current_fund_ratio"] == pytest.approx(0.6)
    assert result["current_equity_exposure"] == pytest.approx(0.4)
    assert result["current_risk_asset_exposure"] == pytest.approx(0.4)


def test_fund_ratio_includes_risk_free_asset_but_not_cash():
    dates = pd.date_range("2024-01-31", periods=13, freq="ME")
    nav = pd.DataFrame({"E": [1.0] * 13}, index=dates)
    request = CurrentRecommendationRequest(
        fund_codes=["E"],
        weights={"E": 0.5, "RiskFree": 0.5},
        current_holdings={"E": 400.0, "RiskFree": 200.0},
        current_cash=400.0,
        monthly_budget=1000.0,
        risk_free_rate=0.02,
        strategy_mode="fixed_weight",
    )

    with patch(
        "api.routes.get_fund_data",
        return_value=(nav, {"E": "Equity"}, []),
    ):
        result = asyncio.run(get_current_recommendation(request))

    assert result["target_fund_ratio"] == pytest.approx(0.8)
    assert result["target_equity_ratio"] == pytest.approx(0.4)
    assert result["target_category_exposures"]["cash_equivalent"] == pytest.approx(0.4)
    assert result["current_fund_ratio"] == pytest.approx(0.6)


def test_backtest_records_category_attribution_separately_from_cash():
    dates = pd.date_range("2024-01-31", periods=2, freq="ME")
    nav = pd.DataFrame({"E": [1.0, 1.0], "M": [1.0, 1.0]}, index=dates)

    result = backtest_fixed_target(
        nav,
        {"E": 0.5, "M": 0.5},
        monthly_investment=100.0,
        strategy_mode="fixed_weight",
        asset_categories={"E": "equity", "M": "money_market"},
    )

    first = result["category_attribution"]["2024-01"]
    assert first["category_values"] == {
        "equity": pytest.approx(50.0),
        "money_market": pytest.approx(50.0),
    }
    assert first["risk_asset_exposure"] == pytest.approx(0.5)
    assert first["cash_value"] == pytest.approx(0.0)
