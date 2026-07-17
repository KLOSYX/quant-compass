import numpy as np
import pandas as pd

from core.frontier import (
    calculate_efficient_frontier,
    calculate_frontier_walk_forward_metrics,
    estimate_covariance,
    evaluate_covariance_shrinkage_ablation,
)


def test_single_asset_frontier_is_feasible():
    dates = pd.date_range(start="2023-01-31", periods=12, freq="ME")
    df = pd.DataFrame({"AssetA": [1 + 0.01 * i for i in range(12)]}, index=dates)

    frontier = calculate_efficient_frontier(df, {})

    assert frontier
    assert frontier[0]["weights"]["AssetA"] == 1.0


def test_frontier_allows_full_risk_free_allocation():
    dates = pd.date_range(start="2020-01-31", periods=60, freq="ME")
    asset_returns = [0.012 + (0.002 if i % 2 == 0 else -0.0015) for i in range(60)]
    risk_free_returns = [0.02 / 12] * 60
    df = pd.DataFrame(
        {
            "AssetA": (1 + pd.Series(asset_returns, index=dates)).cumprod(),
            "RiskFree": (1 + pd.Series(risk_free_returns, index=dates)).cumprod(),
        },
        index=dates,
    )

    frontier = calculate_efficient_frontier(df, {})

    assert frontier
    assert len(frontier) > 1
    assert frontier[0]["risk"] < 1e-6
    assert frontier[0]["weights"]["RiskFree"] > 0.99
    assert frontier[0]["weights"]["AssetA"] < 0.01


def test_frontier_does_not_shrink_riskfree_expected_return():
    dates = pd.date_range(start="2020-01-31", periods=60, freq="ME")
    asset_returns = [0.03] * 60
    risk_free_returns = [0.02 / 12] * 60
    df = pd.DataFrame(
        {
            "AssetA": (1 + pd.Series(asset_returns, index=dates)).cumprod(),
            "RiskFree": (1 + pd.Series(risk_free_returns, index=dates)).cumprod(),
        },
        index=dates,
    )

    frontier = calculate_efficient_frontier(df, {})
    expected_risk_free_return = df.pct_change().fillna(0)["RiskFree"].mean() * 12

    assert frontier
    assert frontier[0]["weights"]["RiskFree"] > 0.99
    assert abs(frontier[0]["return"] - expected_risk_free_return) < 1e-6


def test_frontier_includes_high_sharpe_low_vol_asset_near_riskfree_segment():
    dates = pd.date_range(start="2020-01-31", periods=60, freq="ME")
    df = pd.DataFrame(
        {
            "AssetA": (
                1
                + pd.Series(
                    [0.03 if i % 2 == 0 else -0.01 for i in range(60)], index=dates
                )
            ).cumprod(),
            "BondLike": (
                1
                + pd.Series(
                    [0.004 if i % 2 == 0 else 0.002 for i in range(60)], index=dates
                )
            ).cumprod(),
            "GoldLike": (
                1
                + pd.Series(
                    [
                        0.025 if i % 3 == 0 else -0.005 if i % 3 == 1 else 0.018
                        for i in range(60)
                    ],
                    index=dates,
                )
            ).cumprod(),
            "RiskFree": (1 + pd.Series([0.02 / 12] * 60, index=dates)).cumprod(),
        },
        index=dates,
    )

    frontier = calculate_efficient_frontier(df, {})

    assert frontier
    assert max(point["weights"].get("BondLike", 0.0) for point in frontier) > 0.2


def test_walk_forward_metrics_present_for_long_sample():
    dates = pd.date_range(start="2021-01-31", periods=36, freq="ME")
    df = pd.DataFrame(
        {
            "AssetA": [1 + 0.01 * i for i in range(36)],
            "AssetB": [1 + 0.008 * i + (0.03 if i % 6 == 0 else 0) for i in range(36)],
        },
        index=dates,
    )

    metrics = calculate_frontier_walk_forward_metrics(df, {})

    assert metrics
    assert any(item["frontier_walk_forward_observations"] > 0 for item in metrics)
    assert all("frontier_walk_forward_sharpe" in item for item in metrics)
    assert all("frontier_walk_forward_cvar_loss" in item for item in metrics)
    assert all("robust_score" not in item for item in metrics)


def test_ledoit_wolf_covariance_is_finite_and_preserves_riskfree_zero_risk():
    returns = pd.DataFrame(
        {
            "AssetA": [0.01, -0.02, 0.03, 0.01, -0.01],
            "AssetB": [0.02, -0.01, 0.01, 0.00, 0.02],
            "RiskFree": [0.001] * 5,
        }
    )

    covariance, intensity = estimate_covariance(returns, "ledoit_wolf")

    assert 0 <= intensity <= 1
    assert covariance.notna().all().all()
    assert (covariance.loc["RiskFree"] == 0).all()
    assert (covariance["RiskFree"] == 0).all()
    assert (
        np.linalg.eigvalsh(covariance.loc[["AssetA", "AssetB"], ["AssetA", "AssetB"]])
        >= -1e-12
    ).all()


def test_covariance_ablation_never_auto_switches_the_default():
    rng = np.random.default_rng(7)
    dates = pd.date_range("2015-01-31", periods=120, freq="ME")
    returns = pd.DataFrame(
        rng.normal([0.008, 0.006, 0.004], [0.04, 0.025, 0.02], size=(120, 3)),
        index=dates,
        columns=["Equity", "Bond", "Gold"],
    )
    nav = (1 + returns).cumprod()

    result = evaluate_covariance_shrinkage_ablation(nav)

    assert result["default_method"] == "fixed_20"
    assert result["auto_switched"] is False
    assert result["promotion_status"] in {"candidate", "retain_fixed"}
    assert set(result["segments"]) == {"full_sample", "first_half", "second_half"}
    assert all(
        segment["fixed_20"]["observations"] == segment["ledoit_wolf"]["observations"]
        for segment in result["segments"].values()
    )
