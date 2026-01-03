import pandas as pd
from main import backtest_kelly_dca


def test_risk_free_interest_impact():
    """
    Verify that providing a positive risk_free_rate results in higher final value
    than a zero risk_free_rate, due to interest on cash holdings.
    """
    # Create simple NAV data: Flat market, so strategy will likely hold some cash
    dates = pd.date_range(start="2023-01-01", end="2023-12-31", freq="ME")
    nav_data = {"000001": [1.0] * len(dates)}
    df_nav = pd.DataFrame(nav_data, index=dates)

    weights = {"000001": 0.5}  # Allocate 50% to fund, so significant cash remains
    monthly_investment = 1000.0

    # 1. Run with 0% risk free rate
    result_zero = backtest_kelly_dca(
        df_nav=df_nav,
        weights_dict=weights,
        monthly_investment=monthly_investment,
        risk_free_rate=0.0,
    )

    # 2. Run with 10% risk free rate (high enough to show clear difference)
    result_interest = backtest_kelly_dca(
        df_nav=df_nav,
        weights_dict=weights,
        monthly_investment=monthly_investment,
        risk_free_rate=0.10,
    )

    print(f"Final Value (0% rate): {result_zero['final_value']:.2f}")
    print(f"Final Value (10% rate): {result_interest['final_value']:.2f}")

    # The result with interest should accurately be higher
    assert result_interest["final_value"] > result_zero["final_value"]

    # Calculate expected rough lower bound difference
    # Total invested ~12000. Avg cash ~6000? 10% rate -> ~600 extra?
    diff = result_interest["final_value"] - result_zero["final_value"]
    assert diff > 100, "Difference should be significant"


if __name__ == "__main__":
    test_risk_free_interest_impact()
