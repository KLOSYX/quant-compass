import pandas as pd
from fastapi.testclient import TestClient

from core import data as data_module
from main import app


client = TestClient(app)


def test_resolves_fund_names_without_fetching_nav(monkeypatch):
    fund_list = pd.DataFrame(
        {
            "基金代码": ["016149", "270023"],
            "基金简称": ["招商安泰债券A", "广发全球精选股票"],
            "基金类型": ["债券型", "QDII"],
        }
    ).set_index("基金代码")
    monkeypatch.setattr(data_module, "FUND_LIST_CACHE", fund_list)

    response = client.post(
        "/api/fund_names",
        json={"fund_codes": ["016149", "270023", "999999", "016149"]},
    )

    assert response.status_code == 200
    assert response.json() == {
        "fund_names": {
            "016149": "招商安泰债券A",
            "270023": "广发全球精选股票",
            "999999": "999999 (名称未找到)",
        }
    }
