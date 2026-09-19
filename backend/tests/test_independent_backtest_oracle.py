"""Independent accounting oracles for portfolio backtests."""

import numpy as np
import pandas as pd
import pytest

from core.backtest import backtest_dca, backtest_fixed_target, backtest_lump_sum


def _manual_drawdown(values):
    values = np.asarray(values, dtype=float)
    peaks = np.maximum.accumulate(values)
    return float(np.min(values / peaks - 1.0))


def test_lump_sum_matches_independent_share_ledger():
    dates = pd.date_range("2024-01-31", periods=4, freq="ME")
    nav = pd.DataFrame(
        {"A": [1.0, 1.2, 0.9, 1.4], "B": [2.0, 1.8, 2.2, 2.4]},
        index=dates,
    )
    weights = {"A": 0.6, "B": 0.4}
    initial_holdings = {"A": 200.0, "B": 300.0}
    initial_cash = 100.0
    lump_sum = 1200.0

    shares_a = 200.0 / 1.0 + lump_sum * 0.6 / 1.0
    shares_b = 300.0 / 2.0 + lump_sum * 0.4 / 2.0
    expected_values = nav["A"] * shares_a + nav["B"] * shares_b + initial_cash
    committed = sum(initial_holdings.values()) + initial_cash + lump_sum
    years = (dates[-1] - dates[0]).days / 365.25
    expected_cagr = (expected_values.iloc[-1] / committed) ** (1 / years) - 1

    actual = backtest_lump_sum(
        nav,
        weights,
        lump_sum,
        initial_holdings=initial_holdings,
        initial_cash=initial_cash,
    )

    assert actual["total_invested"] == pytest.approx(committed)
    assert actual["final_value"] == pytest.approx(expected_values.iloc[-1])
    assert actual["annualized_return"] == pytest.approx(expected_cagr)
    assert actual["max_drawdown_nav"] == pytest.approx(
        _manual_drawdown(expected_values / committed)
    )


def test_dca_matches_independent_unitized_cashflow_ledger():
    dates = pd.date_range("2024-01-31", periods=4, freq="ME")
    nav = pd.DataFrame(
        {"A": [1.0, 1.2, 0.9, 1.4], "B": [2.0, 1.8, 2.2, 2.4]},
        index=dates,
    )
    weights = {"A": 0.6, "B": 0.4}
    initial_holdings = {"A": 200.0, "B": 300.0}
    initial_cash = 100.0
    contribution = 100.0

    shares = pd.Series({"A": 200.0 / 1.0, "B": 300.0 / 2.0})
    total_units = sum(initial_holdings.values()) + initial_cash
    cash = initial_cash
    expected_values = []
    expected_unit_nav = []

    for _, prices in nav.iterrows():
        wealth_before_flow = float((shares * prices).sum() + cash)
        unit_nav = wealth_before_flow / total_units
        expected_unit_nav.append(unit_nav)
        total_units += contribution / unit_nav
        shares += contribution * pd.Series(weights) / prices
        expected_values.append(float((shares * prices).sum() + cash))

    actual = backtest_dca(
        nav,
        weights,
        contribution,
        initial_holdings=initial_holdings,
        initial_cash=initial_cash,
    )

    assert actual["final_value"] == pytest.approx(expected_values[-1])
    assert actual["final_unit_nav"] == pytest.approx(expected_unit_nav[-1])
    assert actual["max_drawdown_nav"] == pytest.approx(
        _manual_drawdown(expected_unit_nav)
    )


def test_fixed_target_uses_only_new_cash_for_underweight_assets():
    dates = pd.date_range("2024-01-31", periods=3, freq="ME")
    nav = pd.DataFrame(
        {"A": [1.0, 2.0, 2.0], "B": [1.0, 1.0, 1.0]},
        index=dates,
    )

    actual = backtest_fixed_target(
        nav,
        {"A": 0.5, "B": 0.5},
        100.0,
        initial_holdings={"A": 500.0, "B": 500.0},
        buy_fee={},
        sell_fee={},
        strategy_mode="fixed_weight",
        rebalance_enabled=False,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    february = actual["attribution"]["2024-02"]
    assert february["A"] == pytest.approx(1100.0)
    assert february["B"] == pytest.approx(650.0)
    assert february["Cash"] == pytest.approx(0.0)
    assert february["A"] > february["B"]  # DCA does not force a sell-side rebalance


def test_fixed_target_independent_fee_ledger_uses_fixed_budget_without_sales():
    dates = pd.date_range("2024-01-31", periods=2, freq="ME")
    nav = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [1.0, 1.0]},
        index=dates,
    )

    actual = backtest_fixed_target(
        nav,
        {"A": 0.5, "B": 0.5},
        100.0,
        initial_holdings={"A": 500.0, "B": 500.0},
        buy_fee={"B": 0.02},
        sell_fee={"A": 0.01},
        strategy_mode="fixed_weight",
        rebalance_enabled=False,
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    february = actual["attribution"]["2024-02"]
    # January equalizes net asset additions. B's fee therefore requires a
    # slightly larger gross allocation than A under the fixed gross budget.
    # In February A is overweight after doubling, so the entire 100 budget goes
    # to B; no A sale and no use of the sell fee is permitted.
    # Executable gross orders are rounded down to cents; 0.01 stays in Cash.
    january_a = 500.0 + 49.50
    january_b = 500.0 + 50.49 / 1.02
    expected_a = january_a * 2.0
    expected_b = january_b + 100.0 / 1.02

    assert february["A"] == pytest.approx(expected_a)
    assert february["B"] == pytest.approx(expected_b)
    assert february["Cash"] == pytest.approx(0.01)
    assert actual["final_value"] == pytest.approx(expected_a + expected_b + 0.01)
