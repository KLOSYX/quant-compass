import pandas as pd
from main import backtest_dca, backtest_kelly_dca


def test_dca_drawdown_with_rising_market():
    # Scenario: Market doubles then stays flat. Drawdown should be 0.
    dates = pd.date_range(start="2023-01-01", periods=5, freq="MS")
    # NAV: 1.0, 2.0, 2.0, 2.0, 2.0
    nav_data = [1.0, 2.0, 2.0, 2.0, 2.0]
    df_nav = pd.DataFrame(data=nav_data, index=dates, columns=["AssetA"])
    weights = {"AssetA": 1.0}
    monthly_investment = 1000.0

    results = backtest_dca(df_nav, weights, monthly_investment)

    # Drawdown NAV should be 0.0 (or very close)
    print(f"Computed DCA Drawdown: {results['max_drawdown_nav']}")
    # Tolerance for floating point
    assert abs(results["max_drawdown_nav"]) < 0.0001, (
        f"Expected ~0 drawdown, got {results['max_drawdown_nav']}"
    )


def test_kelly_dca_drawdown_with_rising_market():
    # Similar scenario for Kelly
    dates = pd.date_range(start="2023-01-01", periods=5, freq="MS")
    nav_data = [1.0, 2.0, 2.0, 2.0, 2.0]
    df_nav = pd.DataFrame(data=nav_data, index=dates, columns=["AssetA"])
    weights = {"AssetA": 1.0}
    monthly_investment = 1000.0

    results = backtest_kelly_dca(
        df_nav,
        weights,
        monthly_investment,
        ma_window=2,  # Short window to ensure signals stable
    )

    print(f"Computed Kelly Drawdown: {results['max_drawdown_nav']}")
    assert abs(results["max_drawdown_nav"]) < 0.01, (
        f"Expected <1% drawdown, got {results['max_drawdown_nav']}"
    )


def test_dca_drawdown_real_drop():
    # Scenario: Market starts 1.0, rises to 2.0, drops to 1.5 (-25% drop).
    # Unit NAV should reflect -25% drawdown.
    dates = pd.date_range(start="2023-01-01", periods=3, freq="MS")
    nav_data = [1.0, 2.0, 1.5]
    df_nav = pd.DataFrame(data=nav_data, index=dates, columns=["AssetA"])
    weights = {"AssetA": 1.0}
    monthly_investment = 1000.0

    results = backtest_dca(df_nav, weights, monthly_investment)

    # Drawdown calculation on High(2.0) -> Low(1.5).
    # (1.5 - 2.0)/2.0 = -0.25.
    expected_dd = -0.25

    print(f"Computed DCA Real Drop Drawdown: {results['max_drawdown_nav']}")
    assert abs(results["max_drawdown_nav"] - expected_dd) < 0.0001
