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
    # Unit NAV must be strictly positive; zero is not a valid fund price.
    nav = pd.Series(range(1, len(dates) + 1), index=dates, name="单位净值")

    if symbol == "000001":
        nav = nav * 1.01
    elif symbol == "000002":
        nav = nav * 1.02
    else:
        nav = nav * 1.03

    if indicator == "分红送配详情":
        return pd.DataFrame(columns=["权益登记日", "除息日", "红利发放日", "每份分红"])
    if indicator == "拆分详情":
        return pd.DataFrame(columns=["拆分折算日", "拆分比例"])
    if indicator == "累计净值走势":
        return pd.DataFrame({"净值日期": nav.index, "累计净值": nav.values})
    df = pd.DataFrame(nav)
    df["净值日期"] = df.index
    return df
