from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from api.models import CurrentRecommendationRequest
from core.audit_store import AuditStore, SCHEMA_VERSION
from core.backtest import backtest_fixed_target
from core.decision import (
    DecisionInput,
    ExecutionContext,
    PortfolioState,
    StrategySpec,
    decide,
)
from core.domain import (
    CorporateAction,
    ExecutionAmount,
    ReturnQuality,
    SubstitutionGroup,
    canonical_hash,
)
from core.execution import execute_monthly_plan
from core.ledger import AccountUnitLedger
from core.market_data import build_market_snapshot, reconstruct_total_return_index
from core.reference import build_reference_basket


def _aggregate_gap_inputs():
    return dict(
        fund_codes=["A", "B"],
        current_holdings={"A": 800.0, "B": 0.0},
        target_holdings={"A": 450.0, "B": 450.0},
        target_weights={"A": 0.5, "B": 0.5},
        current_cash=0.0,
        monthly_budget=200.0,
        buy_fees={},
        sell_fees={},
        investment_limits={},
        timestamp="2026-08-31",
    )


@pytest.mark.parametrize(
    ("method", "covariance", "expected_status"),
    [
        ("proportional_gap", None, "ok"),
        ("constrained_tracking", None, "fallback_missing_covariance"),
        (
            "constrained_tracking",
            pd.DataFrame(np.nan, index=["A", "B"], columns=["A", "B"]),
            "fallback_invalid_covariance",
        ),
    ],
)
def test_aggregate_target_gap_caps_every_normal_and_degraded_path(
    method, covariance, expected_status
):
    result = execute_monthly_plan(
        **_aggregate_gap_inputs(),
        allocation_method=method,
        execution_covariance=covariance,
    )

    assert result.total_gross_buy == pytest.approx(100.0)
    assert sum(
        item.executable_holding for item in result.funds.values()
    ) == pytest.approx(900.0)
    assert result.allocation_diagnostics["status"] == expected_status


def test_solver_failure_uses_same_aggregate_target_gap_cap():
    covariance = pd.DataFrame(np.eye(2), index=["A", "B"], columns=["A", "B"])
    with patch(
        "core.execution_optimizer.minimize",
        return_value=SimpleNamespace(success=False, message="forced failure"),
    ):
        result = execute_monthly_plan(
            **_aggregate_gap_inputs(),
            allocation_method="constrained_tracking",
            execution_covariance=covariance,
        )

    assert result.total_gross_buy == pytest.approx(100.0)
    assert result.allocation_diagnostics["status"] == "fallback_solver_failure"


def test_substitute_group_cannot_consume_unrelated_strategic_gap():
    codes = ["A", "C", "S"]
    covariance = pd.DataFrame(np.ones((3, 3)), index=codes, columns=codes)
    result = execute_monthly_plan(
        fund_codes=codes,
        current_holdings={"A": 0.0, "C": 0.0, "S": 0.0},
        target_holdings={"A": 100.0, "C": 900.0, "S": 0.0},
        target_weights={"A": 0.1, "C": 0.9, "S": 0.0},
        current_cash=0.0,
        monthly_budget=1000.0,
        buy_fees={},
        sell_fees={},
        investment_limits={
            "A": {"monthly_limit": 0.0},
            "C": {"monthly_limit": 0.0},
        },
        timestamp="2026-08-31",
        allocation_method="constrained_tracking",
        execution_covariance=covariance,
        substitute_for={"S": "A"},
    )

    assert result.funds["S"].gross_buy == pytest.approx(100.0, abs=1e-4)
    assert result.total_gross_buy <= 100.0 + 1e-4


def test_substitute_does_not_activate_while_primary_is_fully_executable():
    covariance = pd.DataFrame(
        [[1.0, 1.0], [1.0, 1.0]], index=["A", "S"], columns=["A", "S"]
    )
    result = execute_monthly_plan(
        fund_codes=["A", "S"],
        current_holdings={},
        target_holdings={"A": 100.0, "S": 0.0},
        target_weights={"A": 1.0, "S": 0.0},
        current_cash=0.0,
        monthly_budget=100.0,
        buy_fees={},
        sell_fees={},
        investment_limits={},
        timestamp="2026-08-31",
        allocation_method="constrained_tracking",
        execution_covariance=covariance,
        substitute_for={"S": "A"},
    )

    assert result.funds["A"].gross_buy == pytest.approx(100.0)
    assert result.funds["S"].gross_buy == pytest.approx(0.0)


def test_limit_basis_net_asset_add_converts_to_gross_cash_out():
    result = execute_monthly_plan(
        fund_codes=["A"],
        current_holdings={},
        target_holdings={"A": 1000.0},
        target_weights={"A": 1.0},
        current_cash=0.0,
        monthly_budget=500.0,
        buy_fees={"A": 0.1},
        sell_fees={},
        investment_limits={
            "A": {"monthly_limit": 100.0, "limit_basis": "net_asset_add"}
        },
        timestamp="2026-08-31",
    )

    assert result.funds["A"].gross_buy == pytest.approx(110.0)
    assert result.funds["A"].executable_holding == pytest.approx(100.0)


def test_non_finite_values_are_rejected_at_api_and_core_boundaries():
    with pytest.raises(ValidationError):
        CurrentRecommendationRequest(
            fund_codes=["A"], weights={"A": 1.0}, monthly_budget=float("nan")
        )
    with pytest.raises(ValueError, match="must be finite"):
        execute_monthly_plan(
            fund_codes=["A"],
            current_holdings={},
            target_holdings={"A": 100.0},
            target_weights={"A": 1.0},
            current_cash=float("inf"),
            monthly_budget=100.0,
            buy_fees={},
            sell_fees={},
            investment_limits={},
            timestamp="2026-08-31",
        )


def test_execution_amount_and_hash_contracts_are_deterministic():
    amount = ExecutionAmount(gross_cash_out=110.0, net_asset_add=100.0, buy_fee=10.0)
    group = SubstitutionGroup("equity-a", "A", ("S1", "S2"), 100.0)

    assert amount.gross_cash_out == pytest.approx(110.0)
    assert canonical_hash({"amount": amount, "group": group}) == canonical_hash(
        {"group": group, "amount": amount}
    )


def test_reference_basket_is_invariant_to_asset_nav_scale():
    dates = pd.date_range("2026-01-31", periods=4, freq="ME")
    levels = pd.DataFrame(
        {"A": [1.0, 1.1, 1.05, 1.2], "B": [1.0, 0.9, 1.0, 0.95]},
        index=dates,
    )
    scaled = levels.copy()
    scaled["B"] *= 10.0

    expected = build_reference_basket(levels, {"A": 0.5, "B": 0.5})
    actual = build_reference_basket(scaled, {"B": 0.5, "A": 0.5})

    pd.testing.assert_series_equal(actual, expected)


def test_dynamic_reference_weights_only_affect_returns_after_effective_date():
    dates = pd.date_range("2026-01-31", periods=4, freq="ME")
    levels = pd.DataFrame(
        {"A": [1.0, 1.1, 1.21, 1.331], "B": [1.0, 1.0, 1.0, 1.0]}, index=dates
    )
    schedule = pd.DataFrame(
        [{"A": 1.0, "B": 0.0}, {"A": 0.0, "B": 1.0}],
        index=[dates[0], dates[1]],
    )

    basket = build_reference_basket(levels, schedule)

    assert basket.iloc[1] == pytest.approx(1.1)
    assert basket.iloc[2] == pytest.approx(1.1)


def test_distribution_and_split_total_return_do_not_create_mechanical_loss():
    dates = pd.to_datetime(["2026-01-30", "2026-02-02", "2026-02-03"])
    prices = pd.Series([10.0, 9.0, 4.5], index=dates)
    actions = [
        CorporateAction(
            asset_code="A",
            kind="cash_distribution",
            ex_date=dates[1],
            cash_per_share=1.0,
        ),
        CorporateAction(
            asset_code="A",
            kind="split",
            ex_date=dates[2],
            split_ratio=2.0,
        ),
    ]

    total_return = reconstruct_total_return_index(prices, actions)

    assert total_return.tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_account_ledger_preserves_wealth_across_distribution_and_split():
    prices = {"A": 10.0}
    ledger = AccountUnitLedger.initialize({"A": 10.0}, 0.0, prices)
    distribution = CorporateAction(
        asset_code="A",
        kind="cash_distribution",
        ex_date="2026-02-02",
        payment_date="2026-02-03",
        cash_per_share=1.0,
    )
    split = CorporateAction(
        asset_code="A", kind="split", ex_date="2026-02-04", split_ratio=2.0
    )

    ledger.accrue_distribution(distribution)
    assert ledger.wealth({"A": 9.0}) == pytest.approx(100.0)
    ledger.settle_distribution(distribution, policy="cash")
    assert ledger.wealth({"A": 9.0}) == pytest.approx(100.0)
    ledger.apply_split(split)
    assert ledger.wealth({"A": 4.5}) == pytest.approx(100.0)


def test_external_contribution_does_not_change_account_unit_nav():
    ledger = AccountUnitLedger.initialize({"A": 10.0}, 0.0, {"A": 10.0})
    before = ledger.unit_nav({"A": 10.0})

    ledger.contribute("2026-02-01", 50.0, {"A": 10.0})

    assert ledger.unit_nav({"A": 10.0}) == pytest.approx(before)
    assert ledger.total_units == pytest.approx(150.0)


def test_same_decision_input_produces_identical_decision_result(tmp_path):
    observed = pd.Timestamp("2026-08-31 18:00", tz="Asia/Shanghai")
    levels = pd.DataFrame(
        {"A": [1.0, 1.1]}, index=pd.to_datetime(["2026-07-31", "2026-08-31"])
    )
    snapshot = build_market_snapshot(
        price_series=levels,
        corporate_actions=(),
        return_quality={
            "A": ReturnQuality(
                status="verified",
                corporate_action_coverage_complete=True,
                availability_quality="observed",
            )
        },
        source="synthetic",
        fetched_at=observed,
        available_at={"A": observed},
    )
    decision_input = DecisionInput(
        as_of=observed + pd.Timedelta(hours=1),
        portfolio_state=PortfolioState(holdings={"A": 0.0}, cash=0.0),
        market_snapshot=snapshot,
        strategy_spec=StrategySpec(
            policy_id="fixed-weight-v1",
            target_weights={"A": 1.0},
        ),
        execution_context=ExecutionContext(monthly_budget=100.0),
    )

    first = decide(decision_input)
    second = decide(decision_input)

    assert canonical_hash(first) == canonical_hash(second)
    assert first.decision_readiness == "manual_review_required"
    assert first.execution_plan.total_gross_buy == pytest.approx(100.0)
    with AuditStore(tmp_path / "decision-audit.sqlite3") as store:
        store.save_snapshot(snapshot, observed)
        decision_hash = store.save_decision(decision_input, first, observed)
        store.save_actual_fill(
            decision_hash,
            {"fills": {"A": 100.0}, "fees": {"A": 0.0}},
            observed + pd.Timedelta(days=1),
        )
        assert (
            store.connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            == 1
        )
        assert (
            store.connection.execute("SELECT COUNT(*) FROM actual_fills").fetchone()[0]
            == 1
        )


def test_last_period_fee_is_in_final_unit_nav_twr_and_drawdown():
    dates = pd.to_datetime(["2026-01-31", "2026-02-28"])
    nav = pd.DataFrame({"A": [1.0, 1.0]}, index=dates)

    result = backtest_fixed_target(
        nav,
        {"A": 1.0},
        monthly_investment=100.0,
        initial_holdings={"A": 100.0},
        buy_fee={"A": 0.1},
        strategy_mode="fixed_weight",
        enable_cvar_constraint=False,
        enable_drawdown_constraint=False,
    )

    assert result["final_value"] == pytest.approx(281.8181818)
    assert result["final_unit_nav"] == pytest.approx(0.92471591)
    assert result["fee_after_twr"] == pytest.approx(-0.07528409)
    assert result["monthly_max_drawdown"] == pytest.approx(-0.07528409)


def test_audit_store_migration_and_decision_round_trip(tmp_path):
    path = tmp_path / "audit.sqlite3"
    store = AuditStore(path)
    version = store.connection.execute("SELECT version FROM schema_meta").fetchone()[0]
    store.close()

    reopened = AuditStore(path)
    second_version = reopened.connection.execute(
        "SELECT version FROM schema_meta"
    ).fetchone()[0]
    reopened.close()

    assert version == SCHEMA_VERSION
    assert second_version == SCHEMA_VERSION
