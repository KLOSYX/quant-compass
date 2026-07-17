from unittest.mock import patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from core.frontier import calculate_efficient_frontier
from core.walk_forward import (
    WALK_FORWARD_STRATEGIES,
    _select_stable_kelly_platform,
    evaluate_executable_walk_forward,
)
from main import app


client = TestClient(app)


def test_stable_kelly_platform_keeps_36_month_default_when_on_platform():
    metrics = {
        24: {
            "observations": 12,
            "annualized_return": 0.20,
            "sharpe": 1.8,
            "max_drawdown": 0.18,
        },
        36: {
            "observations": 12,
            "annualized_return": 0.10,
            "sharpe": 0.8,
            "max_drawdown": 0.12,
        },
        48: {
            "observations": 12,
            "annualized_return": 0.11,
            "sharpe": 0.9,
            "max_drawdown": 0.13,
        },
        60: {
            "observations": 12,
            "annualized_return": 0.30,
            "sharpe": 2.0,
            "max_drawdown": 0.25,
        },
    }

    selected = _select_stable_kelly_platform(metrics)

    assert selected["selected_window_months"] == 36
    assert selected["selection_rule"] == "median_long_window_platform"


def test_one_asset_walk_forward_matches_independent_cash_flow_ledger():
    dates = pd.date_range("2024-01-31", periods=4, freq="ME")
    nav = pd.DataFrame({"A": [1.0, 1.0, 2.0, 2.0]}, index=dates)

    result = evaluate_executable_walk_forward(
        nav,
        {},
        monthly_investment=100.0,
        min_train_months=2,
        min_weight=1.0,
        max_weight=1.0,
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
            min_weight=0.5,
            max_weight=1.0,
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
        min_weight=1.0,
        max_weight=1.0,
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
