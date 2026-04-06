import pandas as pd

from main import calculate_efficient_frontier, calculate_frontier_walk_forward_metrics


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
    assert any(item["walk_forward_observations"] > 0 for item in metrics)
    assert all("robust_score" in item for item in metrics)
