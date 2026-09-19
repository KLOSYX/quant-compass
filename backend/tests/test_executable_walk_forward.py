from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.frontier import calculate_efficient_frontier
from core.risk import calculate_drawdown_from_returns
from core.walk_forward import (
    WALK_FORWARD_STRATEGIES,
    evaluate_executable_walk_forward,
)
from main import app


client = TestClient(app)


def test_one_asset_walk_forward_matches_independent_cash_flow_ledger():
    dates = pd.date_range("2024-01-31", periods=4, freq="ME")
    nav = pd.DataFrame({"A": [1.0, 1.0, 2.0, 2.0]}, index=dates)

    result = evaluate_executable_walk_forward(
        nav,
        {},
        monthly_investment=100.0,
        min_train_months=2,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    assert result["status"] == "ok"
    assert result["evaluation_months"] == 2
    for name in WALK_FORWARD_STRATEGIES:
        strategy = result["strategies"][name]
        assert strategy["final_wealth"] == pytest.approx(300.0)
        assert strategy["total_external_capital"] == pytest.approx(200.0)
        assert strategy["unit_nav_history"] == {
            "2024-03": pytest.approx(2.0),
            "2024-04": pytest.approx(2.0),
        }
        assert strategy["average_holding_period_months"] == pytest.approx(0.5)


def test_external_contributions_do_not_create_returns_or_hide_drawdowns():
    dates = pd.date_range("2024-01-31", periods=5, freq="ME")
    flat_nav = pd.DataFrame({"A": [1.0] * 5}, index=dates)

    result = evaluate_executable_walk_forward(
        flat_nav,
        {},
        monthly_investment=100.0,
        initial_cash=100.0,
        min_train_months=2,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    for metrics in result["strategies"].values():
        assert metrics["metric_basis"] == "unit_nav_after_external_cash_flows"
        assert metrics["annualized_return"] == pytest.approx(0.0)
        assert metrics["annualized_volatility"] == pytest.approx(0.0)
        assert metrics["sharpe"] == pytest.approx(0.0)
        assert metrics["max_drawdown"] == pytest.approx(0.0)
        assert all(
            value == pytest.approx(1.0)
            for value in metrics["unit_nav_history"].values()
        )

    assert calculate_drawdown_from_returns(pd.Series([-0.10])) == pytest.approx(0.10)


def test_complete_strategy_reports_account_risk_exposure_and_transmission_audit():
    dates = pd.date_range("2023-01-31", periods=8, freq="ME")
    nav = pd.DataFrame(
        {
            "A": [1.00, 1.03, 1.01, 1.06, 1.04, 1.10, 1.08, 1.13],
            "B": [1.00, 1.00, 1.02, 1.01, 1.04, 1.03, 1.06, 1.05],
            "RiskFree": [1.00, 1.001, 1.002, 1.003, 1.004, 1.005, 1.006, 1.007],
        },
        index=dates,
    )

    result = evaluate_executable_walk_forward(
        nav,
        {},
        monthly_investment=100.0,
        initial_cash=500.0,
        min_train_months=4,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
        asset_categories={
            "A": "equity",
            "B": "bond",
            "RiskFree": "cash_equivalent",
        },
    )

    metrics = result["strategies"]["full_strategy"]
    assert result["metric_basis"]["return_series"] == (
        "unit_nav_after_external_cash_flows"
    )
    for key in (
        "annualized_volatility",
        "sharpe",
        "sortino",
        "worst_month",
        "cvar_loss",
        "average_asset_category_exposures",
        "average_frontier_target_weight_change",
        "average_actual_basket_weight_change",
        "average_frontier_change_transmission",
        "evidence_quality",
    ):
        assert key in metrics
    assert metrics["evidence_quality"]["status"] == "low"
    assert metrics["cvar_confidence_status"] == "low"
    assert metrics["cvar_tail_observations"] == 1
    assert metrics["sortino_confidence_status"] == "low"
    assert set(metrics["average_asset_category_exposures"]) >= {
        "equity",
        "bond",
        "cash_equivalent",
    }
    assert len(metrics["data_access_audit"]) == result["evaluation_months"]
    for month in metrics["data_access_audit"]:
        assert {
            "actual_fund_position",
            "frontier_target_weight_change",
            "actual_basket_weight_change",
            "frontier_change_transmission",
        } <= month.keys()


def test_walk_forward_never_passes_realized_nav_to_training_optimizer():
    dates = pd.date_range("2020-01-31", periods=8, freq="ME")
    nav = pd.DataFrame(
        {
            "A": [1.0, 1.05, 1.02, 1.1, 1.2, 0.9, 1.3, 1.4],
            "B": [1.0, 0.99, 1.03, 1.04, 1.02, 1.1, 1.08, 1.12],
        },
        index=dates,
    )
    training_ends = []

    def audited_frontier(train_df, fees):
        training_ends.append(train_df.index.max())
        return calculate_efficient_frontier(train_df, fees)

    with patch("core.walk_forward.calculate_efficient_frontier", audited_frontier):
        result = evaluate_executable_walk_forward(
            nav,
            {},
            monthly_investment=100.0,
            min_train_months=4,
            enable_cvar_constraint=False,
            enable_drawdown_constraint=False,
        )

    assert result["status"] == "ok"
    full_audit = result["strategies"]["full_strategy"]["data_access_audit"]
    assert len(training_ends) == result["evaluation_months"]
    for training_end, audit in zip(training_ends, full_audit):
        assert training_end.strftime("%Y-%m-%d") == audit["training_end"]
        assert audit["training_end"] == audit["execution_date"]
        assert audit["training_end"] < audit["realized_date"]


def test_short_history_returns_explicit_insufficient_data_status():
    dates = pd.date_range("2024-01-31", periods=12, freq="ME")
    nav = pd.DataFrame({"A": range(1, 13)}, index=dates)

    result = evaluate_executable_walk_forward(
        nav,
        {},
        monthly_investment=100.0,
    )

    assert result == {
        "status": "insufficient_data",
        "minimum_training_months": 24,
        "available_months": 11,
        "strategies": {},
    }


def test_walk_forward_rejects_holdings_outside_its_universe():
    dates = pd.date_range("2020-01-31", periods=5, freq="ME")
    nav = pd.DataFrame({"A": [1.0] * 5}, index=dates)

    with pytest.raises(ValueError, match="outside the walk-forward universe"):
        evaluate_executable_walk_forward(
            nav,
            {},
            monthly_investment=100.0,
            initial_holdings={"retired": 500.0},
            min_train_months=2,
        )


def test_all_benchmarks_share_fees_limits_and_external_cash_flows():
    dates = pd.date_range("2024-01-31", periods=4, freq="ME")
    nav = pd.DataFrame({"A": [1.0] * 4}, index=dates)

    result = evaluate_executable_walk_forward(
        nav,
        {},
        monthly_investment=100.0,
        buy_fees={"A": 0.1},
        fund_investment_limits={"A": {"monthly_limit": 50.0}},
        min_train_months=2,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    strategies = result["strategies"]
    external_capital = {
        metrics["total_external_capital"] for metrics in strategies.values()
    }
    fee_totals = {
        round(metrics["total_transaction_fees"], 10) for metrics in strategies.values()
    }
    assert external_capital == {200.0}
    assert fee_totals == {round(2 * (50.0 - 50.0 / 1.1), 10)}
    assert all(
        metrics["total_unfilled_target_gap"] > 0 for metrics in strategies.values()
    )


def test_complete_walk_forward_can_ablate_covariance_with_identical_cash_flows():
    dates = pd.date_range("2020-01-31", periods=10, freq="ME")
    nav = pd.DataFrame(
        {
            "A": [1.00, 1.04, 1.02, 1.08, 1.06, 1.12, 1.10, 1.16, 1.14, 1.20],
            "B": [1.00, 1.01, 1.03, 1.02, 1.05, 1.04, 1.07, 1.06, 1.09, 1.08],
            "C": [1.00, 0.99, 1.02, 1.00, 1.04, 1.03, 1.06, 1.05, 1.08, 1.07],
        },
        index=dates,
    )

    result = evaluate_executable_walk_forward(
        nav,
        {},
        monthly_investment=100.0,
        initial_cash=1000.0,
        min_train_months=4,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
        include_covariance_ablation=True,
    )

    ablation = result["covariance_ablation"]
    assert ablation["evaluation_scope"] == "complete_executable_strategy"
    assert ablation["auto_switched"] is False
    assert set(ablation["segments"]) == {
        "full_sample",
        "first_half",
        "second_half",
    }
    assert all(
        segment["fixed_20"]["observations"] == segment["ledoit_wolf"]["observations"]
        for segment in ablation["segments"].values()
    )
    assert ablation["segments"]["full_sample"]["fixed_20"]["status"] == (
        "insufficient_data"
    )
    assert ablation["segments"]["first_half"]["fixed_20"]["status"] == (
        "insufficient_data"
    )
    assert ablation["promotion_status"] == "descriptive_only"


def test_walk_forward_compares_tracking_allocator_with_proportional_baseline():
    dates = pd.date_range("2024-01-31", periods=10, freq="ME")
    returns = [0.02, -0.01, 0.015, -0.005, 0.018, -0.012, 0.01, -0.004, 0.012]
    nav = pd.DataFrame(
        {
            "A": [1.0, *pd.Series([1 + value for value in returns]).cumprod()],
            "B": [
                1.0,
                *pd.Series([1 + 0.95 * value for value in returns]).cumprod(),
            ],
        },
        index=dates,
    )

    result = evaluate_executable_walk_forward(
        nav,
        {},
        monthly_investment=100.0,
        min_train_months=4,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
        fund_investment_limits={"A": {"monthly_limit": 20.0}},
        substitute_for={"B": "A"},
        execution_allocation_method="constrained_tracking",
    )

    ablation = result["allocation_ablation"]
    assert ablation["baseline_method"] == "proportional_gap"
    assert ablation["candidate_method"] == "constrained_tracking"
    assert ablation["auto_switched"] is False
    assert ablation["candidate"]["total_substitute_purchases"] > 0.0
    assert ablation["baseline"]["total_substitute_purchases"] == 0.0


def test_backtest_api_only_runs_executable_walk_forward_when_requested():
    dates = pd.date_range("2024-01-31", periods=3, freq="ME")
    nav = pd.DataFrame({"A": [1.0, 1.1, 1.2]}, index=dates)
    expected = {"status": "ok", "strategies": {}}
    request = {
        "fund_codes": ["A"],
        "weights": {"A": 1.0},
        "fund_fees": {},
        "start_date": "2024-01-31",
        "end_date": "2024-03-31",
        "monthly_investment": 100.0,
        "initial_holdings": {"A": 500.0},
        "include_walk_forward": True,
        "enable_cvar_constraint": False,
        "enable_drawdown_constraint": False,
    }

    with (
        patch("api.routes.get_fund_data", return_value=(nav, {"A": "Fund A"}, [])),
        patch(
            "api.routes.evaluate_executable_walk_forward", return_value=expected
        ) as evaluator,
    ):
        response = client.post("/api/backtest_strategies", json=request)

    assert response.status_code == 200
    assert response.json()["walk_forward"] == expected
    assert evaluator.call_count == 1
    assert evaluator.call_args.kwargs["initial_holdings"] == {"A": 500.0}
