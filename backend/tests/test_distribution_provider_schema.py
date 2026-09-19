"""Real provider schema regressions: units, payout dates and quality diagnostics."""

import pandas as pd
import pytest

from core.data import _distribution_per_share, _get_fund_nav


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"每10份分红": "每10份派现金0.0500元"}, 0.005),
        ({"每10份分红": "每10份派现金1.8700元"}, 0.187),
        ({"每10份分红": 0.03}, 0.003),
        ({"每份分红": 0.03}, 0.03),
        ({"分红方案": "每10份派现金0.8200元"}, 0.082),
    ],
)
def test_distribution_amount_respects_provider_unit(row, expected):
    assert _distribution_per_share(row) == pytest.approx(expected)


def test_actual_provider_columns_reconcile_and_keep_payment_date(monkeypatch):
    dates = pd.to_datetime(["2026-09-04", "2026-09-07", "2026-09-08"])
    frames = {
        "单位净值走势": pd.DataFrame(
            {"净值日期": dates, "单位净值": [1.0, 0.995, 0.996]}
        ),
        "累计净值走势": pd.DataFrame(
            {"净值日期": dates, "累计净值": [1.0, 1.0, 1.001]}
        ),
        "分红送配详情": pd.DataFrame(
            [
                {
                    "年份": "2026年",
                    "权益登记日": "2026-09-07",
                    "除息日": "2026-09-07",
                    "每10份分红": "每10份派现金0.0500元",
                    "分红发放日": "2026-09-08",
                }
            ]
        ),
        "拆分详情": pd.DataFrame(),
    }
    monkeypatch.setattr(
        "core.data.ak.fund_open_fund_info_em",
        lambda symbol, indicator: frames[indicator].copy(),
    )
    nav = _get_fund_nav("006662", "债券")
    assert nav.attrs["return_quality"].status == "reconciled"
    (action,) = nav.attrs["corporate_actions"]
    assert action.cash_per_share == pytest.approx(0.005)
    assert action.payment_date == dates[2]
    assert action.ex_date == dates[1]
    frames["分红送配详情"].loc[0, "每10份分红"] = "未知分红格式"
    bad = _get_fund_nav("006662", "债券")
    assert bad.attrs["return_quality"].status == "partial"
    assert "无法解析分红字段" in bad.attrs["return_quality"].warnings[0]


def test_completed_dividend_before_replay_does_not_block_current_holdings():
    from core.domain import CorporateAction
    from core.ledger import AccountUnitLedger

    ledger = AccountUnitLedger(shares={"457001": 100}, cash=10, total_units=110)
    action = CorporateAction(
        asset_code="457001",
        kind="cash_distribution",
        ex_date="2021-01-15",
        record_date="2021-01-18",
        payment_date="2021-01-19",
        cash_per_share=0.082,
    )
    prices = pd.DataFrame(
        {"457001": [1.0, 1.0]}, index=pd.to_datetime(["2026-01-31", "2026-02-28"])
    )
    ledger.process_actions([action], prices.index[0], prices.index[1], prices)
    assert ledger.shares == {"457001": 100}
    assert ledger.cash == 10
    assert ledger.receivables == {}


def test_monthly_estimation_does_not_add_future_month_end():
    from core.data import estimation_total_return_nav
    from core.domain import ReturnQuality

    nav = pd.DataFrame(
        {"A": [1.0, 1.1]}, index=pd.to_datetime(["2026-07-31", "2026-08-31"])
    )
    nav.attrs["return_quality"] = {
        "A": ReturnQuality(status="reconciled", corporate_action_coverage_complete=True)
    }
    nav.attrs["monthly_total_return"] = pd.DataFrame(
        {"A": [1.0, 1.1, 1.12]},
        index=pd.to_datetime(["2026-07-31", "2026-08-31", "2026-09-30"]),
    )
    result, _ = estimation_total_return_nav(nav)
    assert result.index.equals(nav.index)
    assert result["A"].tolist() == [1.0, 1.1]


def test_insufficient_completed_months_returns_target_without_fake_risk(monkeypatch):
    import asyncio
    from api.models import AnalysisRequest
    from api.routes import analyze_portfolio

    nav = pd.DataFrame(
        {"A": [1.0, 1.1]}, index=pd.to_datetime(["2026-07-31", "2026-08-31"])
    )
    monkeypatch.setattr(
        "api.routes.get_fund_data", lambda *a, **k: (nav, {"A": "A"}, [])
    )
    result = asyncio.run(
        analyze_portfolio(AnalysisRequest(fund_codes=["A"], fund_fees={}))
    )
    assert result["analysis_status"] == "target_only_fallback"
    assert result["fallback_target"]["weights"] == {"A": 1.0}
    assert result["fallback_target"]["risk"] is None
    assert "不足3期" in result["fallback_message"]
