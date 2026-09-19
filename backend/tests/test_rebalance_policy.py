"""Independent monetary examples for the single fixed-target execution path."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from core.backtest import backtest_fixed_target
from core.decision import ExecutionContext, PortfolioState, StrategySpec, plan_month
from core.portfolio import decompose_selected_weights
from core.risk import estimate_account_risk


def plan(holdings=None, cash=0, weights=None, **settings):
    weights = weights or {"A": 0.5, "B": 0.5}
    return plan_month(
        columns=list(weights),
        state=PortfolioState(holdings or {"A": 70000, "B": 30000}, cash),
        strategy=StrategySpec("confirmed", weights),
        context=ExecutionContext(**{"monthly_budget": 10000, **settings}),
        as_of="2026-09-19",
    )


def test_new_money_first_then_only_necessary_sale_and_conditional_purchase():
    result = plan()
    assert result.funds["B"].gross_buy == 10000
    assert result.funds["A"].gross_sell == pytest.approx(15000, abs=0.01)
    assert result.pending_sale_proceeds == pytest.approx(15000, abs=0.01)
    assert result.cash_after == 0
    assert result.allocation_diagnostics["rebalancing"][
        "conditional_buys_after_settlement"
    ]["B"] == pytest.approx(15000, abs=0.01)


def test_contributions_sufficient_to_restore_target_require_no_sales():
    result = plan(holdings={"A": 60000, "B": 40000}, monthly_budget=20000)
    assert result.total_gross_sell == 0
    assert result.funds["B"].executable_holding == 60000


@pytest.mark.parametrize("cap,sale", [(0, 0), (10000, 0), (12000, 2000)])
def test_sales_require_additional_receiving_capacity(cap, sale):
    result = plan(investment_limits={"B": {"monthly_limit": cap}})
    assert result.total_gross_sell == pytest.approx(sale, abs=0.01)
    assert result.funds["B"].gross_buy <= cap


def test_shortfall_below_purchase_minimum_does_not_trigger_a_sale():
    result = plan(monthly_budget=0, min_purchase_amount=50000)
    assert result.total_gross_sell == 0


@pytest.mark.parametrize("buy_fee", [0, 0.01])
def test_blocked_target_preserves_partial_rebalance_without_overbuying(buy_fee):
    result = plan(
        holdings={"A": 100000, "B": 0, "C": 0},
        weights={"A": 0.2, "B": 0.3, "C": 0.5},
        monthly_budget=10000,
        cash=5000,
        minimum_cash_reserve=5000,
        investment_limits={"C": {"monthly_limit": 0}},
        buy_fees={"B": buy_fee},
    )
    diagnostic = result.allocation_diagnostics["rebalancing"]
    conditional = diagnostic["conditional_buys_after_settlement"]
    assert diagnostic["status"] == "rebalance_planned"
    assert result.funds["A"].gross_sell >= 23000 - 0.01
    assert result.funds["C"].gross_buy == 0
    assert result.funds["B"].gross_buy == 10000
    assert result.cash_after == 5000
    assert result.pending_sale_proceeds == pytest.approx(result.total_gross_sell)
    assert sum(conditional.values()) >= result.pending_sale_proceeds - 0.01
    assert (10000 + conditional["B"]) / (1 + buy_fee) <= 33000 + 0.01
    assert (
        sum(f.executable_holding for f in result.funds.values())
        + result.cash_after
        + result.pending_sale_proceeds
        + result.transaction_fees
    ) == pytest.approx(115000)


def test_high_sale_fee_is_accounted_for_but_does_not_veto_restoration():
    result = plan(sell_fees={"A": 0.02})
    assert result.total_gross_sell == pytest.approx(15000 / 0.99, abs=0.02)
    assert result.transaction_fees == pytest.approx(result.total_gross_sell * 0.02)
    assert (
        sum(f.executable_holding for f in result.funds.values())
        + result.cash_after
        + result.pending_sale_proceeds
        + result.transaction_fees
        == pytest.approx(110000)
    )


@pytest.mark.parametrize(
    "extra", [{"pending_sale_proceeds": 15000}, {"pending_sell_amounts": {"A": 15000}}]
)
def test_pending_orders_prevent_repeated_discretionary_sale(extra):
    result = plan(**extra)
    assert result.total_gross_sell == 0
    assert (
        result.allocation_diagnostics["rebalancing"]["status"]
        == "awaiting_existing_orders"
    )


def test_pending_redemption_prevents_opposite_purchase():
    result = plan(holdings={"A": 10000, "B": 90000}, pending_sell_amounts={"A": 1000})
    assert result.funds["A"].gross_buy == 0


def test_pending_cash_does_not_cover_today_reserve_or_repeat_repair():
    result = plan(
        holdings={"A": 1000, "B": 0},
        monthly_budget=0,
        minimum_cash_reserve=500,
        pending_sale_proceeds=500,
    )
    assert result.total_gross_sell == 0
    assert result.cash_reserve_shortfall == 500
    assert result.total_gross_buy == 0


def test_redemption_limit_and_submitted_amount_are_respected():
    result = plan(redemption_limits={"A": 100})
    assert result.total_gross_sell == pytest.approx(100, abs=0.01)
    with pytest.raises(ValueError, match="exceeds current holding"):
        plan(pending_sell_amounts={"A": 70001})


def test_only_explicitly_authorized_cash_can_be_invested():
    held = {"A": 50000, "B": 50000}
    locked = plan(
        holdings=held, cash=20000, monthly_budget=0, minimum_cash_reserve=5000
    )
    assert locked.cash_after == 20000
    allowed = plan(
        holdings=held,
        cash=20000,
        monthly_budget=0,
        minimum_cash_reserve=5000,
        available_existing_cash=15000,
    )
    assert allowed.cash_after == 5000
    assert allowed.total_gross_buy == 15000
    with pytest.raises(ValueError, match="cannot exceed"):
        plan(cash=100, available_existing_cash=101)


def test_small_deviation_does_not_trigger_sale_and_large_confirmed_change_does():
    held = {"A": 50000, "B": 50000}
    small = plan(holdings=held, monthly_budget=0, weights={"A": 0.51, "B": 0.49})
    assert small.total_gross_sell == 0
    large = plan(holdings=held, monthly_budget=0, weights={"A": 0.7, "B": 0.3})
    assert large.funds["B"].gross_sell == pytest.approx(20000, abs=0.01)


def test_solver_failure_retains_buy_plan():
    from types import SimpleNamespace

    with patch(
        "core.rebalancing.linprog",
        return_value=SimpleNamespace(success=False, message="unavailable"),
    ):
        result = plan()
    assert result.funds["B"].gross_buy == 10000
    assert result.total_gross_sell == 0
    assert result.allocation_diagnostics["rebalancing"]["status"] == "solver_fallback"


@pytest.mark.parametrize(
    "weights",
    [
        {"A": 0.6, "B": 0.2},
        {"A": 1.2, "B": -0.2},
        {"A": 1, "outside": 0},
        {"A": float("nan")},
    ],
)
def test_invalid_complete_weights_are_rejected_without_normalization(weights):
    with pytest.raises((HTTPException, ValueError)):
        decompose_selected_weights(weights, ["A", "B"])


def test_purchase_dates_must_be_real_remaining_plan_dates():
    for dates in [("2026-09-18",), ("2026-10-19",), ("2026-09-21", "2026-09-21")]:
        with pytest.raises(ValueError, match="purchase dates"):
            plan(planned_purchase_dates=dates)
    result = plan(planned_purchase_dates=("2026-09-21", "2026-10-01"))
    assert len(result.allocation_diagnostics["purchase_dates"]) == 2


def test_settled_rebalance_proceeds_are_reinvested_on_next_replay_period():
    prices = pd.DataFrame(
        {"A": [1, 1, 1], "B": [1, 1, 1]},
        index=pd.date_range("2026-01-31", periods=3, freq="ME"),
    )
    result = backtest_fixed_target(
        prices, {"A": 0.5, "B": 0.5}, 0, initial_holdings={"A": 70000, "B": 30000}
    )
    assert result["attribution"]["2026-01"]["PendingCash"] == pytest.approx(
        20000, abs=0.01
    )
    assert result["attribution"]["2026-02"]["A"] == pytest.approx(50000, abs=0.01)
    assert result["attribution"]["2026-02"]["B"] == pytest.approx(50000, abs=0.01)
    assert result["execution"]["2026-02"]["total_gross_sell"] == 0


def test_risk_uses_actual_cash_dilution_and_missing_returns_are_not_zero():
    values = [1.0] * 80 + [0.5] * 80
    nav = pd.DataFrame(
        {"A": values}, index=pd.bdate_range("2025-01-01", periods=len(values))
    )
    whole = estimate_account_risk(nav, {"A": 1000}, 1000)
    half = estimate_account_risk(nav, {"A": 500}, 1000)
    assert whole["max_drawdown"] == pytest.approx(0.5)
    assert half["max_drawdown"] == pytest.approx(0.25)
    assert estimate_account_risk(nav, {"B": 500}, 1000)["cvar_loss"] is None


def test_randomized_cash_fee_limit_and_holdings_conservation():
    rng = np.random.default_rng(20260920)
    for _ in range(100):
        holdings = dict(zip(["A", "B"], rng.uniform(0, 10000, 2)))
        cash, budget, reserve = rng.uniform(0, 1000, 3)
        cap = float(rng.choice([0, 100, 500, 5000]))
        result = plan(
            holdings=holdings,
            cash=cash,
            monthly_budget=budget,
            minimum_cash_reserve=reserve,
            buy_fees={"A": 0.015, "B": 0.002},
            sell_fees={"A": 0.02, "B": 0.01},
            investment_limits={c: {"monthly_limit": cap} for c in holdings},
        )
        assert result.cash_after >= min(cash + budget, reserve) - 1e-6
        assert sum(holdings.values()) + cash + budget == pytest.approx(
            sum(f.executable_holding for f in result.funds.values())
            + result.cash_after
            + result.pending_sale_proceeds
            + result.transaction_fees
        )
        for c, f in result.funds.items():
            assert 0 <= f.gross_sell <= holdings[c]
            assert f.gross_buy <= cap + 1e-6
            assert not (f.gross_sell > 0 and f.gross_buy > 0)


def test_solver_exception_preserves_new_money_plan():
    with patch(
        "core.rebalancing.linprog", side_effect=ValueError("solver unavailable")
    ):
        result = plan()
    assert result.total_gross_sell == 0
    assert result.funds["B"].gross_buy == 10000
    assert result.allocation_diagnostics["rebalancing"]["status"] == "solver_fallback"


def test_exact_trigger_boundary_does_not_sell_but_material_excess_does():
    edge = plan(holdings={"A": 52000, "B": 48000}, monthly_budget=0)
    assert edge.total_gross_sell == 0
    beyond = plan(holdings={"A": 52100, "B": 47900}, monthly_budget=0)
    assert beyond.total_gross_sell == pytest.approx(2100, abs=0.01)


def test_small_monthly_market_oscillations_do_not_create_round_trip_sales():
    nav = pd.DataFrame(
        {"A": [1.0, 1.01] * 12, "B": [1.0] * 24},
        index=pd.date_range("2024-01-31", periods=24, freq="ME"),
    )
    result = backtest_fixed_target(
        nav,
        {"A": 0.5, "B": 0.5},
        0,
        initial_holdings={"A": 50000, "B": 50000},
    )
    assert all(month["total_gross_sell"] == 0 for month in result["execution"].values())


def test_removed_modes_and_unknown_parameters_are_rejected():
    from pydantic import ValidationError
    from api.models import CurrentRecommendationRequest

    for retired in [{"strategy_mode": "retired_mode"}, {"retired_parameter": 0.5}]:
        with pytest.raises(ValidationError):
            CurrentRecommendationRequest(fund_codes=["A"], weights={"A": 1}, **retired)
