"""Independent numerical identities for the rebuilt candidate baseline."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from core.frontier import _solve_minimum_variance, calculate_efficient_frontier
from core.risk import calculate_asset_diagnostics


@pytest.mark.parametrize("scale", [1e-10, 1, 1e10])
def test_qp_matches_closed_form_and_certifies_global_gap(scale):
    covariance = np.array([[0.04, 0.01], [0.01, 0.09]]) * scale
    weights, gap = _solve_minimum_variance(covariance)
    np.testing.assert_allclose(weights, [8 / 11, 3 / 11], atol=1e-7)
    assert gap < 1e-7
    # The binding mean constraint uniquely determines the two-asset weights.
    weights, gap = _solve_minimum_variance(covariance, np.array([0.01, 0.03]), 0.025)
    np.testing.assert_allclose(weights, [0.25, 0.75], atol=1e-7)
    assert gap < 1e-7


def test_diagonal_three_asset_optimum_and_permutation():
    covariance = np.diag([1.0, 4.0, 9.0])
    expected = np.array([1.0, 1 / 4, 1 / 9])
    expected /= expected.sum()
    weights, _ = _solve_minimum_variance(covariance)
    np.testing.assert_allclose(weights, expected, atol=1e-6)
    order = [2, 0, 1]
    reordered, _ = _solve_minimum_variance(covariance[np.ix_(order, order)])
    np.testing.assert_allclose(reordered, weights[order], atol=1e-6)


def test_infeasible_return_and_indefinite_covariance_are_rejected():
    assert _solve_minimum_variance(np.eye(2), np.array([0.01, 0.02]), 0.03) is None
    assert _solve_minimum_variance(np.array([[1.0, 2.0], [2.0, 1.0]])) is None


def test_false_solver_success_cannot_masquerade_as_optimal(monkeypatch):
    monkeypatch.setattr(
        "core.frontier.minimize",
        lambda *a, **k: SimpleNamespace(success=True, x=np.array([1.0, 0.0])),
    )
    assert _solve_minimum_variance(np.diag([4.0, 1.0])) is None
    returns = pd.DataFrame(
        {"A": [0.01, -0.01, 0.02, -0.02], "B": [0.004, -0.004, 0.005, -0.005]}
    )
    nav = pd.concat(
        [pd.DataFrame({"A": [1.0], "B": [1.0]}), (1 + returns).cumprod()],
        ignore_index=True,
    )
    result = calculate_efficient_frontier(nav, {})
    assert result[0]["solver_fallback_used"]
    assert result[0]["solver_optimality_gap"] is None


def test_returns_are_asset_local_and_diagnostics_match_frontier():
    dates = pd.date_range("2020-01-31", periods=25, freq="ME")
    nav = pd.DataFrame(
        {"Bond": (1.003) ** np.arange(25), "Stock": (1.03) ** np.arange(25)},
        index=dates,
    )
    frontier = calculate_efficient_frontier(nav, {})
    diagnostics = calculate_asset_diagnostics(nav, {}, frontier)
    bond = next(row for row in diagnostics if row["code"] == "Bond")
    assert bond["optimizer_expected_return"] == pytest.approx(0.036)
    nav["Stock"] = (1.1) ** np.arange(25)
    changed = calculate_asset_diagnostics(nav, {}, [])
    assert next(row for row in changed if row["code"] == "Bond")[
        "optimizer_expected_return"
    ] == pytest.approx(0.036)
    means = pd.Series({"Bond": 0.003, "Stock": 0.03})
    for point in frontier:
        weights = pd.Series(point["weights"])
        assert point["return"] == pytest.approx(float(weights @ means * 12))
        assert point["return_basis"] == "historical_arithmetic_mean"
        assert point["forecast_available"] is False


def test_equal_means_and_duplicate_assets_are_feasible():
    for covariance in [np.zeros((2, 2)), np.ones((2, 2)), np.diag([0.0, 0.01])]:
        result = _solve_minimum_variance(covariance, np.array([0.01, 0.01]), 0.01)
        assert result is not None
        weights, gap = result
        assert weights.sum() == pytest.approx(1)
        assert weights.min() >= 0
        assert gap < 1e-7


def test_maximum_return_face_chooses_minimum_variance_among_ties():
    weights, gap = _solve_minimum_variance(
        np.diag([1.0, 4.0, 1.0]), np.array([0.03, 0.03, 0.01]), 0.03
    )
    np.testing.assert_allclose(weights, [0.8, 0.2, 0], atol=1e-7)
    assert gap < 1e-7


def test_insufficient_returns_do_not_generate_fictitious_risk():
    with pytest.raises(ValueError, match="two complete"):
        calculate_efficient_frontier(pd.DataFrame({"A": [1.0, 1.1]}), {})


def test_reference_selection_does_not_chase_sharpe_or_reject_risk_warnings():
    from api.routes import _select_recommended_frontier_point

    points = [
        {
            "risk": 0.1,
            "frontier_walk_forward_sharpe": -1.0,
            "frontier_walk_forward_max_drawdown": 0.4,
        },
        {"risk": 0.2, "frontier_walk_forward_sharpe": 5.0},
    ]
    selected, evidence = _select_recommended_frontier_point(
        points, maximum_drawdown=0.1, cvar_enabled=True, maximum_cvar_loss=0.01
    )
    assert selected == 0
    assert evidence["requires_target_confirmation"]
    assert evidence["candidate_diagnostics"][0]["risk_warnings"] == [
        "max_drawdown_exceeded"
    ]
    assert evidence["candidate_diagnostics"][0]["eligible"]
