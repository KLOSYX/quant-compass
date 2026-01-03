import pandas as pd


def mock_fund_name_em():
    data = {
        "基金代码": ["000001", "000002", "000003"],
        "基金简称": ["Fund A", "Fund B", "Fund C"],
    }
    return pd.DataFrame(data)


def mock_fund_open_fund_info_em(symbol, indicator):
    dates = pd.to_datetime(
        pd.date_range(start="2023-01-01", end="2023-04-01", freq="D")
    )
    nav = pd.Series(range(len(dates)), index=dates, name="单位净值")

    if symbol == "000001":
        nav = nav * 1.01
    elif symbol == "000002":
        nav = nav * 1.02
    else:
        nav = nav * 1.03

    df = pd.DataFrame(nav)
    df["净值日期"] = df.index
    return df
