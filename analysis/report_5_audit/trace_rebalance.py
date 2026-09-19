"""Production-path trace only; independent counterexample is in recalculate.py."""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from core.rebalancing import plan_rebalance_sales  # noqa: E402
from core.execution import execute_monthly_plan  # noqa: E402

with sqlite3.connect(
    f"file:{ROOT / 'backend/data/audit.sqlite3'}?mode=ro", uri=True
) as con:
    raw, result = con.execute(
        "SELECT input_json,result_json FROM decisions WHERE decision_hash LIKE 'c35edbd581%'"
    ).fetchone()
i, r = json.loads(raw), json.loads(result)
ctx, execution = i["execution_context"], r["execution_plan"]
funds = execution["funds"]
buys = {c: f["gross_buy"] for c, f in funds.items()}
sales, diagnostic = plan_rebalance_sales(
    holdings=i["portfolio_state"]["holdings"],
    targets=r["target_allocation"],
    baseline_values={c: f["executable_holding"] for c, f in funds.items()},
    baseline_buys=buys,
    buy_fees=ctx["buy_fees"],
    sell_fees=ctx["sell_fees"],
    investment_limits=ctx["investment_limits"],
    redemption_limits=ctx["redemption_limits"],
    available_budget=ctx["monthly_budget"],
    band=ctx["rebalance_band"],
    timestamp=i["as_of"],
    planned_purchase_days=ctx["planned_purchase_days"],
)
preview = execute_monthly_plan(
    fund_codes=list(funds),
    current_holdings=i["portfolio_state"]["holdings"],
    target_holdings=r["target_allocation"],
    target_weights=i["strategy_spec"]["target_weights"],
    current_cash=i["portfolio_state"]["cash"],
    monthly_budget=ctx["monthly_budget"],
    buy_fees=ctx["buy_fees"],
    sell_fees=ctx["sell_fees"],
    investment_limits=ctx["investment_limits"],
    timestamp=i["as_of"],
    minimum_cash_reserve=ctx["minimum_cash_reserve"],
    target_cash=execution["target_cash"],
    planned_purchase_days=ctx["planned_purchase_days"],
    planned_sales=sales,
    reuse_settled_sale_proceeds=True,
)
output = dict(
    sales=sales,
    diagnostic=diagnostic,
    net_proceeds=preview.total_net_sell_proceeds,
    incremental_buy_capacity=preview.total_gross_buy - sum(buys.values()),
    uncovered_proceeds=preview.total_net_sell_proceeds
    - (preview.total_gross_buy - sum(buys.values())),
)
Path(__file__).with_name("rebalance_trace.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps(output, ensure_ascii=False, indent=2))
