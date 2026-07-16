"""Independent audit of the three-year asset diagnostics.

This module intentionally does not import quant-compass calculation or data modules.
It reads source histories through AKShare and recomputes every displayed metric.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import akshare as ak
import numpy as np
import pandas as pd
from scipy.optimize import minimize


AS_OF_DATE = date(2026, 7, 12)
START_DATE = date(2023, 7, 12)
FUND_CODES = [
    "016149",
    "270023",
    "007339",
    "002611",
    "007280",
    "539002",
    "457001",
    "012643",
    "001211",
    "006662",
    "014429",
]
RETURN_SHRINKAGE = 0.35
COVARIANCE_SHRINKAGE = 0.20
MAX_SINGLE_WEIGHT = 0.50
MIN_DISPLAY_WEIGHT = 0.01
FRONTIER_POINTS = 20
CACHE_DIR = Path(__file__).resolve().parent / ".audit_cache"


@dataclass(frozen=True)
class ExpectedScreenshotMetric:
    cumulative_return: float
    cagr: float
    optimizer_return: float
    volatility: float
    frontier_points_used: int
    max_weight: float


SCREENSHOT_METRICS = {
    "016149": ExpectedScreenshotMetric(0.1146, 0.0379, 0.0770, 0.0118, 14, 0.5000),
    "270023": ExpectedScreenshotMetric(1.6275, 0.3928, 0.2799, 0.2161, 18, 0.5000),
    "007339": ExpectedScreenshotMetric(0.2991, 0.0939, 0.1186, 0.1641, 6, 0.0345),
    "002611": ExpectedScreenshotMetric(0.8170, 0.2273, 0.1947, 0.1842, 19, 0.4538),
    "007280": ExpectedScreenshotMetric(0.4395, 0.1331, 0.1391, 0.1389, 6, 0.0559),
    "539002": ExpectedScreenshotMetric(2.6360, 0.5569, 0.3656, 0.3009, 19, 0.5000),
    "457001": ExpectedScreenshotMetric(1.4250, 0.3550, 0.2745, 0.3008, 1, 0.0132),
    "012643": ExpectedScreenshotMetric(-0.0404, -0.0140, 0.0505, 0.1367, 1, 0.0123),
    "001211": ExpectedScreenshotMetric(0.0422, 0.0143, 0.0624, 0.0012, 7, 0.2462),
    "006662": ExpectedScreenshotMetric(0.0002, 0.0001, 0.0535, 0.0085, 7, 0.2665),
    "014429": ExpectedScreenshotMetric(0.0505, 0.0170, 0.0641, 0.0028, 9, 0.2404),
}


def load_fund_catalog() -> pd.DataFrame:
    catalog = ak.fund_name_em().copy()
    catalog["基金代码"] = catalog["基金代码"].astype(str).str.zfill(6)
    return catalog.set_index("基金代码")


def read_source_history(code: str, fund_type: str) -> pd.Series:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{code}.csv"
    if cache_path.exists():
        cached = pd.read_csv(cache_path, parse_dates=["date"])
        return pd.Series(cached["nav"].to_numpy(), index=cached["date"], name=code)

    if "货币" in fund_type:
        raw = ak.fund_money_fund_info_em(symbol=code)
        dates = pd.to_datetime(raw["净值日期"], errors="coerce")
        income = pd.to_numeric(raw["每万份收益"], errors="coerce")
        valid = dates.notna() & income.notna()
        returns = income.loc[valid] / 10000.0
        series = (1.0 + returns).cumprod()
        series.index = dates.loc[valid]
    else:
        raw = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        dates = pd.to_datetime(raw["净值日期"], errors="coerce")
        values = pd.to_numeric(raw["单位净值"], errors="coerce")
        valid = dates.notna() & values.notna()
        series = pd.Series(values.loc[valid].to_numpy(), index=dates.loc[valid])

    series = series[~series.index.duplicated(keep="last")].sort_index().astype(float)
    pd.DataFrame({"date": series.index, "nav": series.to_numpy()}).to_csv(
        cache_path, index=False
    )
    series.name = code
    return series


def build_monthly_panel() -> tuple[pd.DataFrame, pd.DataFrame]:
    catalog = load_fund_catalog()
    source_quality_rows = []
    monthly_series = {}

    for code in FUND_CODES:
        row = catalog.loc[code]
        fund_type = str(row.get("基金类型", ""))
        daily = read_source_history(code, fund_type)
        source_quality_rows.append(
            {
                "code": code,
                "name": row["基金简称"],
                "type": fund_type,
                "rows": len(daily),
                "duplicate_dates": int(daily.index.duplicated().sum()),
                "null_values": int(daily.isna().sum()),
                "first_date": daily.index.min(),
                "last_date": daily.index.max(),
                "nonpositive_values": int((daily <= 0).sum()),
            }
        )
        monthly_series[code] = daily.resample("ME").last()

    panel = pd.DataFrame(monthly_series).sort_index()
    latest_start = max(panel[code].first_valid_index() for code in panel.columns)
    start = max(pd.Timestamp(START_DATE), latest_start)
    end = min(pd.Timestamp(AS_OF_DATE), panel.index.max())
    panel = panel.loc[start:end].ffill().dropna()
    return panel, pd.DataFrame(source_quality_rows)


def independent_metric_table(panel: pd.DataFrame) -> pd.DataFrame:
    monthly_returns = panel.pct_change().fillna(0.0)
    raw_monthly_means = monthly_returns.mean()
    optimizer_monthly_means = (
        1.0 - RETURN_SHRINKAGE
    ) * raw_monthly_means + RETURN_SHRINKAGE * raw_monthly_means.mean()
    years = (panel.index[-1] - panel.index[0]).days / 365.25

    result = pd.DataFrame(index=panel.columns)
    result["cumulative_return"] = panel.iloc[-1] / panel.iloc[0] - 1.0
    result["cagr"] = (panel.iloc[-1] / panel.iloc[0]) ** (1.0 / years) - 1.0
    result["arithmetic_annual_return"] = raw_monthly_means * 12.0
    result["optimizer_return"] = optimizer_monthly_means * 12.0
    result["volatility"] = monthly_returns.std(ddof=0) * np.sqrt(12.0)
    return result


def independent_frontier(panel: pd.DataFrame) -> tuple[list[dict], pd.Series]:
    returns = panel.pct_change().fillna(0.0)
    raw_means = returns.mean()
    expected = (
        1.0 - RETURN_SHRINKAGE
    ) * raw_means + RETURN_SHRINKAGE * raw_means.mean()
    sample_cov = returns.cov()
    diagonal = np.diag(np.diag(sample_cov.to_numpy()))
    covariance = (
        1.0 - COVARIANCE_SHRINKAGE
    ) * sample_cov.to_numpy() + COVARIANCE_SHRINKAGE * diagonal
    count = len(panel.columns)
    bounds = [(0.0, MAX_SINGLE_WEIGHT)] * count
    equal = np.full(count, 1.0 / count)

    def variance(weights: np.ndarray) -> float:
        return float(weights @ covariance @ weights)

    budget_constraint = {"type": "eq", "fun": lambda weights: weights.sum() - 1.0}
    minimum = minimize(
        variance,
        equal,
        method="SLSQP",
        bounds=bounds,
        constraints=budget_constraint,
    )
    if not minimum.success:
        raise RuntimeError(
            f"Independent minimum-variance solve failed: {minimum.message}"
        )

    max_return_weights = np.zeros(count)
    remaining = 1.0
    for idx in np.argsort(-expected.to_numpy()):
        allocation = min(MAX_SINGLE_WEIGHT, remaining)
        max_return_weights[idx] = allocation
        remaining -= allocation
        if remaining <= 1e-12:
            break

    target_returns = np.linspace(
        float(expected.to_numpy() @ minimum.x),
        float(expected.to_numpy() @ max_return_weights),
        FRONTIER_POINTS,
    )
    frontier = []
    guess = minimum.x

    for target in target_returns:
        constraints = (
            budget_constraint,
            {
                "type": "eq",
                "fun": lambda weights, target=target: (
                    expected.to_numpy() @ weights - target
                ),
            },
        )
        candidates = [guess, equal]
        candidates.extend(np.eye(count))
        best = None
        best_variance = np.inf
        for candidate in candidates:
            solved = minimize(
                variance,
                candidate,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
            )
            if solved.success and variance(solved.x) < best_variance:
                best = solved
                best_variance = variance(solved.x)
        if best is None:
            continue
        guess = best.x
        weights = pd.Series(np.clip(best.x, 0.0, None), index=panel.columns)
        weights /= weights.sum()
        displayed = weights.copy()
        displayed[(displayed > 0.0) & (displayed < MIN_DISPLAY_WEIGHT)] = 0.0
        if displayed.sum() > 0:
            displayed /= displayed.sum()
        if (displayed > MAX_SINGLE_WEIGHT + 1e-9).any():
            displayed = weights
        frontier.append(
            {
                "risk": np.sqrt(best_variance) * np.sqrt(12.0),
                "return": float(expected @ displayed) * 12.0,
                "weights": displayed,
            }
        )

    usage = pd.DataFrame([point["weights"] for point in frontier])
    summary = pd.Series(index=panel.columns, dtype=object)
    for code in panel.columns:
        summary.loc[code] = (
            int((usage[code] > 1e-6).sum()),
            float(usage[code].max()),
        )
    return frontier, summary


def compare_to_screenshot(
    metrics: pd.DataFrame, frontier_summary: pd.Series
) -> pd.DataFrame:
    rows = []
    for code in FUND_CODES:
        expected = SCREENSHOT_METRICS[code]
        used, maximum = frontier_summary.loc[code]
        rows.append(
            {
                "code": code,
                "cum_independent": metrics.loc[code, "cumulative_return"],
                "cum_screenshot": expected.cumulative_return,
                "cagr_independent": metrics.loc[code, "cagr"],
                "cagr_screenshot": expected.cagr,
                "optimizer_independent": metrics.loc[code, "optimizer_return"],
                "optimizer_screenshot": expected.optimizer_return,
                "vol_independent": metrics.loc[code, "volatility"],
                "vol_screenshot": expected.volatility,
                "used_independent": used,
                "used_screenshot": expected.frontier_points_used,
                "max_weight_independent": maximum,
                "max_weight_screenshot": expected.max_weight,
            }
        )
    return pd.DataFrame(rows).set_index("code")


def main() -> None:
    panel, quality = build_monthly_panel()
    metrics = independent_metric_table(panel)
    frontier, summary = independent_frontier(panel)
    comparison = compare_to_screenshot(metrics, summary)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 240)
    print("DATA_QUALITY")
    print(quality.to_string(index=False))
    print()
    print("PANEL", panel.index.min(), panel.index.max(), panel.shape)
    print()
    print("METRICS")
    print(metrics.to_string(float_format=lambda value: f"{value:.8f}"))
    print()
    print("SCREENSHOT_COMPARISON")
    print(comparison.to_string(float_format=lambda value: f"{value:.8f}"))
    print()
    print("FRONTIER_POINT_COUNT", len(frontier))


if __name__ == "__main__":
    main()
