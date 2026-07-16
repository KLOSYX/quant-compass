from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

import core.data as data_module


@pytest.fixture(autouse=True)
def reset_fund_list_cache():
    data_module.FUND_LIST_CACHE = None
    data_module.MONEY_FUND_NAV_CACHE.clear()
    yield
    data_module.FUND_LIST_CACHE = None
    data_module.MONEY_FUND_NAV_CACHE.clear()


def test_money_fund_uses_income_per_10k_to_build_nav():
    fund_list = pd.DataFrame(
        {
            "基金代码": ["001211"],
            "基金简称": ["中欧滚钱宝货币A"],
            "基金类型": ["货币型-普通货币"],
        }
    )
    history = pd.DataFrame(
        {
            "净值日期": pd.to_datetime(
                ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-29"]
            ),
            "每万份收益": [1.0, 2.0, 1.5, 2.5],
            "7日年化收益率": [2.0, 2.1, 2.2, 2.3],
        }
    )

    with (
        patch("core.data.ak.fund_name_em", return_value=fund_list),
        patch(
            "core.data.ak.fund_money_fund_info_em", return_value=history
        ) as money_fetch,
        patch("core.data.ak.fund_open_fund_info_em") as open_fetch,
    ):
        nav, names, warnings = data_module.get_fund_data(
            ["001211"], date(2024, 1, 1), date(2024, 2, 29), None
        )

    money_fetch.assert_called_once_with(symbol="001211")
    open_fetch.assert_not_called()
    assert names == {"001211": "中欧滚钱宝货币A"}
    assert warnings == [
        "注意：部分基金在您选择的开始日期 2024-01-01 尚未成立，实际回测已从 2024-01-31 开始。"
    ]
    assert list(nav.columns) == ["001211"]
    assert nav.index.tolist() == [
        pd.Timestamp("2024-01-31"),
        pd.Timestamp("2024-02-29"),
    ]
    expected_january = (1 + 1.0 / 10000) * (1 + 2.0 / 10000)
    expected_february = expected_january * (1 + 1.5 / 10000) * (1 + 2.5 / 10000)
    assert nav.iloc[0, 0] == pytest.approx(expected_january)
    assert nav.iloc[1, 0] == pytest.approx(expected_february)


def test_regular_fund_keeps_using_open_fund_nav():
    fund_list = pd.DataFrame(
        {
            "基金代码": ["000001"],
            "基金简称": ["普通基金"],
            "基金类型": ["混合型"],
        }
    )
    history = pd.DataFrame(
        {
            "净值日期": pd.to_datetime(["2024-01-31", "2024-02-29"]),
            "单位净值": [1.0, 1.1],
        }
    )

    with (
        patch("core.data.ak.fund_name_em", return_value=fund_list),
        patch(
            "core.data.ak.fund_open_fund_info_em", return_value=history
        ) as open_fetch,
        patch("core.data.ak.fund_money_fund_info_em") as money_fetch,
    ):
        nav, names, _ = data_module.get_fund_data(
            ["000001"], date(2024, 1, 1), date(2024, 2, 29), None
        )

    open_fetch.assert_called_once_with(symbol="000001", indicator="单位净值走势")
    money_fetch.assert_not_called()
    assert names == {"000001": "普通基金"}
    assert nav.iloc[-1, 0] == pytest.approx(1.1)
