from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

import core.data as data_module


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


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
    history = [
        {
            "FSRQ": "2024-01-30",
            "DWJZ": "1.0",
            "LJJZ": "2.0",
            "SDATE": "",
            "ACTUALSYI": "",
            "NAVTYPE": "1",
            "JZZZL": "0.0",
            "SGZT": "开放申购",
            "SHZT": "开放赎回",
            "FHFCZ": "",
            "FHFCZ10": "",
            "FHFCBZ": "",
            "DTYPE": None,
            "FHSP": "",
        },
        {
            "FSRQ": "2024-01-31",
            "DWJZ": "2.0",
            "LJJZ": "2.1",
            "SDATE": "",
            "ACTUALSYI": "",
            "NAVTYPE": "1",
            "JZZZL": "0.0",
            "SGZT": "开放申购",
            "SHZT": "开放赎回",
            "FHFCZ": "",
            "FHFCZ10": "",
            "FHFCBZ": "",
            "DTYPE": None,
            "FHSP": "",
        },
        {
            "FSRQ": "2024-02-01",
            "DWJZ": "1.5",
            "LJJZ": "2.2",
            "SDATE": "",
            "ACTUALSYI": "",
            "NAVTYPE": "1",
            "JZZZL": "0.0",
            "SGZT": "开放申购",
            "SHZT": "开放赎回",
            "FHFCZ": "",
            "FHFCZ10": "",
            "FHFCBZ": "",
            "DTYPE": None,
            "FHSP": "",
        },
        {
            "FSRQ": "2024-02-29",
            "DWJZ": "2.5",
            "LJJZ": "2.3",
            "SDATE": "",
            "ACTUALSYI": "",
            "NAVTYPE": "1",
            "JZZZL": "0.0",
            "SGZT": "开放申购",
            "SHZT": "开放赎回",
            "FHFCZ": "",
            "FHFCZ10": "",
            "FHFCBZ": "",
            "DTYPE": None,
            "FHSP": "",
        },
    ]
    history_response = FakeResponse(
        {"TotalCount": "4", "Data": {"LSJZList": history}}
    )

    with (
        patch("core.data.ak.fund_name_em", return_value=fund_list),
        patch("core.data.requests.get", return_value=history_response) as money_fetch,
        patch("core.data.ak.fund_open_fund_info_em") as open_fetch,
    ):
        nav, names, warnings = data_module.get_fund_data(
            ["001211"], date(2024, 1, 1), date(2024, 2, 29), None
        )

    money_fetch.assert_called_once()
    request_kwargs = money_fetch.call_args.kwargs
    assert request_kwargs["params"]["fundCode"] == "001211"
    assert request_kwargs["params"]["pageSize"] == "20"
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
