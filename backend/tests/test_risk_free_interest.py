import pandas as pd

from core.backtest import backtest_kelly_dca


def test_cash_balance_does_not_earn_risk_free_interest():
    dates = pd.date_range(start="2023-01-01", end="2023-12-31", freq="ME")
    df_nav = pd.DataFrame({"000001": [1.0] * len(dates)}, index=dates)

    weights = {"000001": 0.5}
    monthly_investment = 1000.0

    result_zero = backtest_kelly_dca(
        df_nav=df_nav,
        weights_dict=weights,
        monthly_investment=monthly_investment,
        risk_free_rate=0.0,
    )
    result_interest = backtest_kelly_dca(
        df_nav=df_nav,
        weights_dict=weights,
        monthly_investment=monthly_investment,
        risk_free_rate=0.10,
    )

    assert result_interest["final_value"] == result_zero["final_value"]


def test_explicit_riskfree_asset_retains_yield_in_kelly_backtest():
    dates = pd.date_range(start="2023-01-01", end="2023-12-31", freq="ME")
    df_zero = pd.DataFrame(
        {
            "000001": [1.0] * len(dates),
            "RiskFree": [1.0] * len(dates),
        },
        index=dates,
    )
    df_interest = pd.DataFrame(
        {
            "000001": [1.0] * len(dates),
            "RiskFree": [1.0 * (1.10 ** (i / 12)) for i in range(len(dates))],
        },
        index=dates,
    )

    result_zero = backtest_kelly_dca(
        df_nav=df_zero,
        weights_dict={"000001": 0.5, "RiskFree": 0.5},
        monthly_investment=1000.0,
        risk_free_rate=0.0,
    )
    result_interest = backtest_kelly_dca(
        df_nav=df_interest,
        weights_dict={"000001": 0.5, "RiskFree": 0.5},
        monthly_investment=1000.0,
        risk_free_rate=0.10,
    )

    assert result_interest["final_value"] > result_zero["final_value"]
