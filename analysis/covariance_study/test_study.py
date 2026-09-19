"""Independent controls for offline research calculations."""

import numpy as np
import pandas as pd
import pytest
import run_study as study


def test_two_asset_solution_matches_closed_form_and_is_unit_invariant():
    covariance = np.array([[0.04, 0.01], [0.01, 0.09]])
    expected = np.array([8 / 11, 3 / 11])
    np.testing.assert_allclose(study.solve(covariance), expected, atol=1e-6)
    np.testing.assert_allclose(study.solve(covariance * 10000), expected, atol=1e-6)


def test_metrics_include_initial_loss_and_use_arithmetic_sharpe():
    result = study.metrics([-0.2, 0, 0.1])
    assert result["max_drawdown"] == pytest.approx(0.2)
    assert result["terminal_multiple"] == pytest.approx(0.88)
    assert result["sharpe"] == pytest.approx(
        np.mean([-0.2, 0, 0.1]) * 12 / result["volatility"]
    )


def test_future_returns_cannot_change_prior_targets(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "OUT", tmp_path)
    (tmp_path / "traces").mkdir()
    rng = np.random.default_rng(22)
    frame = pd.DataFrame(
        rng.normal(0, 0.03, (90, 3)),
        index=pd.date_range("2000-01-31", periods=90, freq="ME"),
    )
    study.historical("original", frame, 36, "gmv", "ledoit_wolf")
    altered = frame.copy()
    altered.iloc[-10:] *= 10
    study.historical("altered", altered, 36, "gmv", "ledoit_wolf")
    a = np.load(tmp_path / "traces/original__36__gmv__ledoit_wolf__lag0.npz")["weights"]
    b = np.load(tmp_path / "traces/altered__36__gmv__ledoit_wolf__lag0.npz")["weights"]
    np.testing.assert_allclose(a[:-9], b[:-9], atol=1e-12)


def test_zero_return_zero_contribution_account_matches_cash_identity():
    frame = pd.DataFrame(
        {"A": np.zeros(96), "B": np.zeros(96)},
        index=pd.date_range("2000-01-31", periods=96, freq="ME"),
    )
    result = study.execute_account(
        "constant", frame, "fixed", "zero_contribution", "fixed_20"
    )[0]
    assert result["terminal_wealth"] == pytest.approx(105000)
    assert result["gross_sold"] == 0
    assert result["fees"] == 0
