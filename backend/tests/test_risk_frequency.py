from unittest.mock import patch

import pandas as pd
import pytest

from core import data as data_module
from core.risk import (
    calculate_cvar_diagnostics,
    calculate_max_drawdown,
    calculate_rolling_horizon_returns,
)
from core.strategy import calculate_target_ratio_optimized


def test_rolling_21_day_returns_keep_monthly_risk_horizon():
    dates = pd.bdate_range("2024-01-02", periods=43)
    daily_nav = pd.Series(1.01 ** (pd.Series(range(43), index=dates)), index=dates)

    horizon_returns = calculate_rolling_horizon_returns(daily_nav, 21)

    assert len(horizon_returns) == 22
    assert horizon_returns.iloc[0] == pytest.approx(1.01**21 - 1)


def test_cvar_diagnostics_marks_tiny_tail_as_low_confidence():
    returns = pd.Series([0.01] * 34 + [-0.05, -0.08])

    diagnostics = calculate_cvar_diagnostics(
        returns,
        0.95,
        risk_horizon_days=21,
        minimum_tail_observations=5,
    )

    assert diagnostics["return_observations"] == 36
    assert diagnostics["cvar_effective_tail_count"] == 2
    assert diagnostics["confidence_status"] == "low"
    assert diagnostics["risk_horizon_days"] == 21


def test_daily_path_captures_intramonth_drawdown_missed_by_month_ends():
    dates = pd.bdate_range("2024-01-02", "2024-03-29")
    nav = pd.Series(1.0, index=dates)
    nav.loc["2024-02-12":"2024-02-16"] = 0.6
    nav.loc["2024-02-19":] = 1.0

    daily_drawdown = abs(calculate_max_drawdown(nav))
    monthly_drawdown = abs(calculate_max_drawdown(nav.resample("ME").last()))

    assert daily_drawdown == pytest.approx(0.4)
    assert monthly_drawdown == pytest.approx(0.0)


def test_fund_data_retains_daily_nav_alongside_monthly_analysis_data(monkeypatch):
    dates = pd.bdate_range("2024-01-02", "2024-04-30")
    daily_nav = pd.Series(range(1, len(dates) + 1), index=dates, dtype=float)
    fund_list = pd.DataFrame(
        {
            "基金代码": ["A"],
            "基金简称": ["Fund A"],
            "基金类型": ["股票型"],
        }
    ).set_index("基金代码")
    monkeypatch.setattr(data_module, "FUND_LIST_CACHE", fund_list)

    with patch("core.data._get_fund_nav", return_value=daily_nav):
        monthly, _, _ = data_module.get_fund_data(
            ["A"],
            pd.Timestamp("2024-01-02").date(),
            pd.Timestamp("2024-04-30").date(),
            None,
        )

    retained_daily = monthly.attrs["daily_nav"]
    assert list(monthly.columns) == ["A"]
    assert list(retained_daily.columns) == ["A"]
    assert len(retained_daily) > len(monthly)


def test_low_confidence_monthly_cvar_becomes_warning_only():
    dates = pd.date_range("2021-01-31", periods=37, freq="ME")
    nav = pd.Series(1.0, index=dates)
    nav.iloc[-1] = 0.5

    _, _, info = calculate_target_ratio_optimized(
        reference_portfolio_nav=nav,
        timestamp=dates[-1],
        min_weight=0.0,
        max_weight=1.0,
        kelly_fraction=0.5,
        estimation_window=36,
        risk_free_rate=0.0,
        total_wealth=10000.0,
        minimum_cash_reserve=0.0,
        enable_cvar_constraint=True,
        cvar_confidence=0.95,
        cvar_limit=0.08,
        enable_drawdown_constraint=False,
        max_drawdown_limit=0.2,
    )

    assert info["cvar_data_source"] == "monthly_fallback"
    assert info["cvar_effective_tail_count"] == 2
    assert info["cvar_confidence_status"] == "low"
    assert info["cvar_hard_constraint_requested"] is True
    assert info["cvar_hard_constraint_effective"] is False
    assert info["cvar_warning_only"] is True


def test_daily_risk_pipeline_uses_rolling_horizon_and_daily_drawdown():
    monthly_dates = pd.date_range("2023-01-31", periods=13, freq="ME")
    monthly_nav = pd.Series(1.0, index=monthly_dates)
    daily_dates = pd.bdate_range("2023-01-02", "2024-01-31")
    daily_nav = pd.Series(1.0, index=daily_dates)
    daily_nav.loc["2023-08-01":"2023-08-15"] = 0.6
    daily_nav.loc["2023-08-16":] = 1.0

    _, _, info = calculate_target_ratio_optimized(
        reference_portfolio_nav=monthly_nav,
        timestamp=monthly_dates[-1],
        min_weight=0.5,
        max_weight=1.0,
        kelly_fraction=0.5,
        estimation_window=36,
        risk_free_rate=0.0,
        total_wealth=10000.0,
        minimum_cash_reserve=0.0,
        enable_cvar_constraint=True,
        cvar_confidence=0.95,
        cvar_limit=0.08,
        enable_drawdown_constraint=True,
        max_drawdown_limit=0.2,
        daily_reference_nav=daily_nav,
        risk_horizon_days=21,
    )

    assert info["cvar_data_source"] == "daily_rolling_horizon"
    assert info["drawdown_data_source"] == "daily_path"
    assert info["risk_horizon_days"] == 21
    assert info["cvar_return_observations"] > 200
    assert info["cvar_effective_return_observations"] < 20
    assert info["cvar_effective_tail_count"] == 1
    assert info["cvar_hard_constraint_effective"] is False
    assert info["max_feasible_ratio_by_drawdown"] < 1.0
    assert info["drawdown_estimate_at_target"] <= 0.2
