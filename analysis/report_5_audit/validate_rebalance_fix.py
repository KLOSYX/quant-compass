"""Replay the archived report input through the corrected production planner."""

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from core.decision import (  # noqa: E402
    DECISION_ENGINE_VERSION,
    ExecutionContext,
    PortfolioState,
    StrategySpec,
    plan_month,
)

with sqlite3.connect(
    f"file:{ROOT / 'backend/data/audit.sqlite3'}?mode=ro", uri=True
) as con:
    raw, original = con.execute(
        "SELECT input_json,result_json FROM decisions WHERE decision_hash LIKE 'c35edbd581%'"
    ).fetchone()
inputs, original = json.loads(raw), json.loads(original)
ctx = dict(inputs["execution_context"])
cov = ctx["execution_covariance"]
ctx["execution_covariance"] = pd.DataFrame(
    cov["values"], index=cov["index"], columns=cov["columns"]
)
state = PortfolioState(**inputs["portfolio_state"])
strategy = StrategySpec(**inputs["strategy_spec"])
result = plan_month(
    columns=list(strategy.target_weights),
    state=state,
    strategy=strategy,
    context=ExecutionContext(**ctx),
    as_of=inputs["as_of"],
)
diagnostic = result.allocation_diagnostics["rebalancing"]
conditional = diagnostic["conditional_buys_after_settlement"]
targets = original["target_allocation"]
old_deviation = sum(
    abs(f["executable_holding"] - targets[c])
    for c, f in original["execution_plan"]["funds"].items()
)
settled_deviation = sum(
    abs(
        f.executable_holding
        + conditional.get(c, 0) / (1 + ctx["buy_fees"].get(c, 0))
        - targets[c]
    )
    for c, f in result.funds.items()
)
assert settled_deviation < old_deviation
assert diagnostic["status"] == "rebalance_planned"
assert sum(conditional.values()) >= result.pending_sale_proceeds - 0.01
assert result.cash_after >= ctx["minimum_cash_reserve"]
assert (
    abs(result.total_gross_buy - original["execution_plan"]["total_gross_buy"]) < 0.01
)
assert (
    abs(
        sum(state.holdings.values())
        + state.cash
        + ctx["monthly_budget"]
        - sum(f.executable_holding for f in result.funds.values())
        - result.risk_free_after
        - result.cash_after
        - result.pending_sale_proceeds
        - result.transaction_fees
    )
    < 1e-6
)
output = {
    "engine_version": DECISION_ENGINE_VERSION,
    "old_status": original["execution_plan"]["allocation_diagnostics"]["rebalancing"][
        "status"
    ],
    "new_status": diagnostic["status"],
    "sales": {c: f.gross_sell for c, f in result.funds.items() if f.gross_sell},
    "new_money_gross_buys": result.total_gross_buy,
    "pending_net_sale_proceeds": result.pending_sale_proceeds,
    "conditional_buys_after_settlement": conditional,
    "cash_after": result.cash_after,
    "original_target_l1_deviation_before_fix": old_deviation,
    "original_target_l1_deviation_after_conditional_buys": settled_deviation,
    "transaction_fees_before_conditional_buys": result.transaction_fees,
    "checks": "receiving capacity, unchanged new-money buys, reserve, conservation passed",
}
Path(__file__).with_name("rebalance_fix_validation.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps(output, ensure_ascii=False, indent=2))
