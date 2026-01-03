import pandas as pd
from main import backtest_kelly_dca, backtest_lump_sum, backtest_dca


def test_va_short_selling_prevention():
    # Create bull market data where ratio might trigger sell
    dates = pd.date_range(start="2023-01-01", end="2024-01-01", freq="ME")
    # Fund A goes up a lot
    nav_a = [1.0 + i * 0.2 for i in range(len(dates))]
    df_nav = pd.DataFrame({"000001": nav_a}, index=dates)

    weights = {"000001": 1.0}
    # Initial holdings skewed: We have NO shares but we set high min_weight
    # Or better: we have small shares but large Sell signal
    initial_holdings = {"000001": 100}
    monthly_investment = 0  # No new money

    # Force a sell signal by having current price high above MA
    # MA will be lagging
    result = backtest_kelly_dca(
        df_nav,
        weights,
        monthly_investment,
        initial_holdings=initial_holdings,
        sell_threshold=0.0,  # Immediate sell
        min_weight=0.0,  # Target 0% equity
        max_weight=0.8,
    )

    # Check that in every month, attribution for 000001 is NOT negative
    for date, attr in result["attribution"].items():
        assert attr["000001"] >= -0.01, (
            f"Short selling detected at {date}: {attr['000001']}"
        )


def test_strategy_consistency_initial_holdings():
    dates = pd.date_range(start="2023-01-01", end="2023-03-31", freq="ME")
    df_nav = pd.DataFrame({"000001": [1.0, 1.1, 1.2]}, index=dates)
    weights = {"000001": 1.0}
    initial_holdings = {"000001": 1000}
    monthly_investment = 100

    lump = backtest_lump_sum(df_nav, weights, 300, initial_holdings=initial_holdings)
    dca = backtest_dca(
        df_nav, weights, monthly_investment, initial_holdings=initial_holdings
    )
    va = backtest_kelly_dca(
        df_nav, weights, monthly_investment, initial_holdings=initial_holdings
    )

    # All should have same initial_invested approx
    # Lump Sum: 1000 (initial) + 300 (new) = 1300
    # DCA/VA: 1000 (initial) + 100 (month 1) = 1100 at start?
    # Wait, my loop adds investment at start of each month.
    # Month 1: 1000 + 100 = 1100
    # Month 2: 1100 + 100 = 1200
    # Month 3: 1200 + 100 = 1300

    assert lump["total_invested"] == 1300
    assert dca["total_invested"] == 1300
    assert va["total_invested"] == 1300


if __name__ == "__main__":
    test_va_short_selling_prevention()
    test_strategy_consistency_initial_holdings()
    print("All tests passed!")
