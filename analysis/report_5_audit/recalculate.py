"""Independent arithmetic audit: no application portfolio/risk helpers imported."""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[2]
HASH_PREFIX = "c35edbd581"


def recalculate():
    with sqlite3.connect(
        f"file:{ROOT / 'backend/data/audit.sqlite3'}?mode=ro", uri=True
    ) as con:
        h, raw, result, created = con.execute(
            "SELECT decision_hash,input_json,result_json,created_at FROM decisions WHERE decision_hash LIKE ?",
            (HASH_PREFIX + "%",),
        ).fetchone()
    source, result = json.loads(raw), json.loads(result)
    snapshot = source["market_snapshot"]
    codes = list(source["strategy_spec"]["target_weights"])
    daily = pd.DataFrame(
        {
            code: pd.Series(
                item["values"]["values"], index=pd.to_datetime(item["values"]["index"])
            )
            for code, item in snapshot["total_return_series"].items()
        }
    )[codes]
    reconstruction_errors = {}
    for code in codes:
        raw = snapshot["price_series"][code]["values"]
        prices = pd.Series(raw["values"], index=pd.to_datetime(raw["index"]))
        factors = prices.to_numpy()[1:] / prices.to_numpy()[:-1]
        dividends = np.zeros(len(prices))
        splits = np.ones(len(prices))
        for event in snapshot["corporate_actions"]:
            if event["asset_code"] != code or not event.get("ex_date"):
                continue
            index = prices.index.searchsorted(pd.Timestamp(event["ex_date"]))
            if not 0 < index < len(prices):
                continue
            if event["kind"] == "cash_distribution":
                dividends[index] += event["cash_per_share"]
            elif event["kind"] == "split":
                splits[index] *= event["split_ratio"]
        factors = (
            prices.to_numpy()[1:] * splits[1:] + dividends[1:]
        ) / prices.to_numpy()[:-1]
        rebuilt = np.r_[1.0, np.cumprod(factors)]
        reconstruction_errors[code] = float(
            np.max(np.abs(rebuilt - daily[code].to_numpy()))
        )
        assert reconstruction_errors[code] < 1e-10
    month_nav = daily.resample("ME").last()
    month_nav = month_nav.loc[month_nav.index <= daily.index.max()]
    # Explicit division and centered outer product, independent of production helpers.
    returns = month_nav.to_numpy()[1:] / month_nav.to_numpy()[:-1] - 1
    mean = returns.sum(axis=0) / len(returns)
    centered = returns - mean
    sample = centered.T @ centered / (len(returns) - 1)
    model = 0.8 * sample + 0.2 * np.diag(np.diag(sample))
    target = np.array([source["strategy_spec"]["target_weights"][c] for c in codes])
    execution = result["execution_plan"]
    old = np.array([source["portfolio_state"]["holdings"].get(c, 0) for c in codes])
    after = np.array([execution["funds"][c]["executable_holding"] for c in codes])
    cash = execution["cash_after"]
    wealth = after.sum() + cash
    budget = source["execution_context"]["monthly_budget"]
    before_wealth = old.sum() + source["portfolio_state"]["cash"]
    target_account = target * (old.sum() + budget) / (before_wealth + budget)

    def metrics(w):
        series = returns @ w
        nav = np.r_[1.0, np.cumprod(1 + series)]
        return dict(
            arithmetic_annual_return=float(w @ mean * 12),
            model_annual_volatility=float(np.sqrt(w @ model @ w * 12)),
            sample_annual_volatility=float(np.std(series, ddof=1) * np.sqrt(12)),
            historical_monthly_rebalanced_cagr=float(nav[-1] ** (12 / len(series)) - 1),
            monthly_max_drawdown=float(np.max(1 - nav / np.maximum.accumulate(nav))),
        )

    gross = sum(f["gross_buy"] for f in execution["funds"].values())
    fees = sum(
        f["gross_buy"] - f["gross_buy"] / (1 + f["buy_fee_rate"])
        for f in execution["funds"].values()
    )
    assert abs(before_wealth + budget - fees - wealth) < 1e-7
    assert abs(target.sum() - 1) < 1e-10
    assert np.allclose(
        after,
        old
        + np.array(
            [
                execution["funds"][c]["gross_buy"]
                / (1 + execution["funds"][c]["buy_fee_rate"])
                for c in codes
            ]
        ),
    )
    # A separate linear oracle certifies the convex objective at the reported weights.
    q = model / (np.trace(model) / len(codes))
    gradient = 2 * q @ target
    spread = np.ptp(mean)
    scaled_mean = (mean - mean.min()) / spread
    oracle = linprog(
        gradient,
        A_ub=-scaled_mean.reshape(1, -1),
        b_ub=[-scaled_mean @ target],
        A_eq=np.ones((1, len(codes))),
        b_eq=[1.0],
        bounds=(0, 1),
        method="highs",
    )
    assert oracle.success
    gap = max(0.0, float(gradient @ target - oracle.fun))
    assert gap < 1e-7
    html = BeautifulSoup(
        (ROOT / "quant-compass-investment-plan-2026-09-19 (5).html").read_text(),
        "html.parser",
    )
    tables = [
        [
            [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
            for row in table.find_all("tr")[1:]
        ]
        for table in html.find_all("table")
    ]

    def number(text):
        return float(text.replace("¥", "").replace(",", "").replace("%", ""))

    weight_rows = tables[1]
    ordered_codes = [
        "270023",
        "457001",
        "539002",
        "016149",
        "007339",
        "002611",
        "007280",
        "012643",
        "001211",
        "006662",
        "014429",
        "021189",
    ]
    names = dict(zip([row[0] for row in weight_rows], ordered_codes))
    assert len(names) == len(codes)
    for name, weight in weight_rows:
        assert (
            abs(
                number(weight) / 100
                - source["strategy_spec"]["target_weights"][names[name]]
            )
            <= 0.00005
        )
    for row in tables[0]:
        expected = (
            source["portfolio_state"]["cash"]
            if row[0] == "闲置现金"
            else source["portfolio_state"]["holdings"][names[row[0]]]
        )
        assert abs(number(row[1]) - expected) < 0.005
    for row in tables[-1]:
        if row[0] == "现金":
            assert abs(number(row[3]) - cash) < 0.005
        else:
            fund = execution["funds"][names[row[0]]]
            assert abs(number(row[2]) - fund["gross_buy"]) < 0.005
            assert abs(number(row[3]) - fund["executable_holding"]) < 0.005
            assert abs(number(row[4]) - fund["target_holding"]) < 0.005
    summary = dict(
        decision_hash=h,
        decision_created=created,
        source=snapshot["source"],
        nav_start=str(month_nav.index[0].date()),
        nav_end=str(month_nav.index[-1].date()),
        return_start=str(month_nav.index[1].date()),
        observations=len(returns),
        target_weight_sum=float(target.sum()),
        independent_normalized_optimality_gap=gap,
        raw_nav_reconstruction_max_error=max(reconstruction_errors.values()),
        report_rows_matched=True,
        before_wealth=float(before_wealth),
        after_wealth=float(wealth),
        budget=budget,
        gross_buys=gross,
        buy_fees=fees,
        unspent=budget - gross,
        cash=cash,
        target_fund_portfolio=metrics(target),
        target_account=metrics(target_account),
        actual_account_before=metrics(old / before_wealth),
        actual_account_after=metrics(after / wealth),
        actual_fund_portfolio_after=metrics(after / after.sum()),
        rebalancing=execution["allocation_diagnostics"]["rebalancing"],
        assets=[
            dict(
                code=c,
                target_weight=float(target[i]),
                historical_return=float(mean[i] * 12),
                return_contribution=float(target[i] * mean[i] * 12),
                variance_contribution=float(
                    target[i] * (model @ target)[i] / (target @ model @ target)
                ),
                actual_account_weight=float(after[i] / wealth),
                actual_fund_weight=float(after[i] / after.sum()),
            )
            for i, c in enumerate(codes)
        ],
    )
    # Construct a feasible counterexample from declared fees/limits only.
    # This is a hypothetical after-settlement allocation, NOT an order recommendation.
    ctx = source["execution_context"]
    goals = np.array([execution["funds"][c]["target_holding"] for c in codes])
    alternative_buys = np.zeros(len(codes))
    for k, code in enumerate(codes):
        fee = ctx["buy_fees"].get(code, 0)
        config = ctx["investment_limits"].get(code, {})
        cap = (
            min(
                config.get("daily_limit")
                if config.get("daily_limit") is not None
                else np.inf,
                np.inf,
            )
            * ctx["planned_purchase_days"]
        )
        cap = min(
            cap,
            config.get("monthly_limit")
            if config.get("monthly_limit") is not None
            else np.inf,
        )
        desired = min(max(0.0, goals[k] - old[k]) * (1 + fee), cap)
        alternative_buys[k] = np.floor((desired + 1e-9) * 100) / 100
    needed_sale = round(float(alternative_buys.sum() - budget), 2)
    sold_index = codes.index("014429")
    assert ctx["sell_fees"]["014429"] == 0 and 0 < needed_sale <= old[sold_index]
    alt = old.copy()
    alt[sold_index] -= needed_sale
    for k, code in enumerate(codes):
        alt[k] += alternative_buys[k] / (1 + ctx["buy_fees"].get(code, 0))
    alt_fees = float(old.sum() + budget - alt.sum())
    alt_cash = (
        source["portfolio_state"]["cash"]
        + budget
        + needed_sale
        - alternative_buys.sum()
    )
    assert abs(alt_cash - source["portfolio_state"]["cash"]) < 1e-7
    assert alt_cash >= ctx["minimum_cash_reserve"]
    base_error = float(np.abs(after - target * (old.sum() + budget - fees)).sum())
    alt_error = float(np.abs(alt - target * (old.sum() + budget - alt_fees)).sum())
    assert alt_error < base_error
    summary["feasible_after_settlement_counterexample"] = dict(
        hypothetical_only=True,
        sold_code="014429",
        gross_sale=needed_sale,
        gross_buys={
            c: float(alternative_buys[k])
            for k, c in enumerate(codes)
            if alternative_buys[k] > 0
        },
        cash=float(alt_cash),
        fees=alt_fees,
        actual_plan_l1_tracking_error=base_error,
        counterexample_l1_tracking_error=alt_error,
        limitation="Requires actual redeemability and settlement; these are not independently verified. No immediate use of pending proceeds.",
    )
    # Independent direct weighted-series variance must equal w'Sw.
    assert abs(np.var(returns @ target, ddof=1) - target @ sample @ target) < 1e-12
    for months in (12, 18, 24, 26):
        x = returns[-months:]
        c = x - x.mean(axis=0)
        cov = c.T @ c / (len(x) - 1)
        shrink = 0.8 * cov + 0.2 * np.diag(np.diag(cov))
        summary.setdefault("window_sensitivity", []).append(
            dict(
                months=len(x),
                annual_return=float(x.mean(axis=0) @ target * 12),
                annual_volatility=float(np.sqrt(target @ shrink @ target * 12)),
            )
        )
    Path(__file__).with_name("results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        json.dumps(
            {k: v for k, v in summary.items() if k != "assets"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    recalculate()
