"""Independent accounting oracles for portfolio backtests."""

import numpy as np
import pandas as pd
import pytest

from core.backtest import backtest_dca, backtest_kelly_dca, backtest_lump_sum


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


def test_kelly_dca_uses_only_new_cash_for_underweight_assets():
    dates = pd.date_range("2024-01-31", periods=3, freq="ME")
    nav = pd.DataFrame(
        {"A": [1.0, 2.0, 2.0], "B": [1.0, 1.0, 1.0]},
        index=dates,
    )

    actual = backtest_kelly_dca(
        nav,
        {"A": 0.5, "B": 0.5},
        100.0,
        initial_holdings={"A": 500.0, "B": 500.0},
        max_buy_multiplier=10.0,
        sell_threshold=0.05,
        min_weight=1.0,
        max_weight=1.0,
        buy_fee={},
        sell_fee={},
        strategy_mode="legacy_linear",
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    february = actual["attribution"]["2024-02"]
    assert february["A"] == pytest.approx(1100.0)
    assert february["B"] == pytest.approx(650.0)
    assert february["Cash"] == pytest.approx(0.0)
    assert february["A"] > february["B"]  # DCA does not force a sell-side rebalance


def test_kelly_dca_independent_fee_ledger_uses_fixed_budget_without_sales():
    dates = pd.date_range("2024-01-31", periods=2, freq="ME")
    nav = pd.DataFrame(
        {"A": [1.0, 2.0], "B": [1.0, 1.0]},
        index=dates,
    )

    actual = backtest_kelly_dca(
        nav,
        {"A": 0.5, "B": 0.5},
        100.0,
        initial_holdings={"A": 500.0, "B": 500.0},
        max_buy_multiplier=10.0,
        sell_threshold=0.05,
        min_weight=1.0,
        max_weight=1.0,
        buy_fee={"B": 0.02},
        sell_fee={"A": 0.01},
        strategy_mode="legacy_linear",
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    february = actual["attribution"]["2024-02"]
    # January equalizes net asset additions. B's fee therefore requires a
    # slightly larger gross allocation than A under the fixed gross budget.
    # In February A is overweight after doubling, so the entire 100 budget goes
    # to B; no A sale and no use of the sell fee is permitted.
    january_net_each = 100.0 / (1.0 + 1.02)
    january_a = 500.0 + january_net_each
    january_b = 500.0 + january_net_each
    expected_a = january_a * 2.0
    expected_b = january_b + 100.0 / 1.02

    assert february["A"] == pytest.approx(expected_a)
    assert february["B"] == pytest.approx(expected_b)
    assert february["Cash"] == pytest.approx(0.0)
    assert actual["final_value"] == pytest.approx(expected_a + expected_b)


def test_window_robustness_uses_fixed_windows_without_proxy_history():
    from core.strategy import assess_kelly_window_robustness

    short_nav = pd.Series(
        [1.0] * 37, index=pd.date_range("2022-01-31", periods=37, freq="ME")
    )
    short_result = assess_kelly_window_robustness(
        reference_portfolio_nav=short_nav,
        min_weight=0.3,
        max_weight=0.8,
        kelly_fraction=0.5,
        risk_free_rate=0.0,
        total_wealth=10000.0,
        minimum_cash_reserve=0.0,
        enable_cvar_constraint=False,
        cvar_confidence=0.95,
        cvar_limit=0.08,
        enable_drawdown_constraint=False,
        max_drawdown_limit=0.2,
    )
    assert short_result["status"] == "insufficient_data"
    assert any(
        item["window_months"] == 48 and not item["available"]
        for item in short_result["measurements"]
    )

    long_nav = pd.Series(
        [1.0] * 61, index=pd.date_range("2020-01-31", periods=61, freq="ME")
    )
    long_result = assess_kelly_window_robustness(
        reference_portfolio_nav=long_nav,
        min_weight=0.3,
        max_weight=0.8,
        kelly_fraction=0.5,
        risk_free_rate=0.0,
        total_wealth=10000.0,
        minimum_cash_reserve=0.0,
        enable_cvar_constraint=False,
        cvar_confidence=0.95,
        cvar_limit=0.08,
        enable_drawdown_constraint=False,
        max_drawdown_limit=0.2,
    )
    assert long_result["status"] == "stable"
    assert long_result["target_ratio_spread"] == pytest.approx(0.0)
