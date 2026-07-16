#!/usr/bin/env python3
"""Compute auditable fund metrics from product and cumulative-NAV CSV files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED_PRODUCT_COLUMNS = {"code", "name", "type", "purchase_status", "channel_status"}
REQUIRED_NAV_COLUMNS = {"code", "date", "cumulative_nav"}


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"{label} missing columns: {', '.join(missing)}")


def calculate_metrics(group: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    nav = group[["date", "cumulative_nav"]].copy()
    nav["date"] = pd.to_datetime(nav["date"], errors="coerce")
    nav["cumulative_nav"] = pd.to_numeric(nav["cumulative_nav"], errors="coerce")
    nav = nav.dropna().sort_values("date").drop_duplicates("date", keep="last")
    nav = nav[(nav["date"] <= as_of) & (nav["cumulative_nav"] > 0)]
    if len(nav) < 2:
        return {"valid": False, "reason": "insufficient_nav"}

    days = int((nav["date"].iloc[-1] - nav["date"].iloc[0]).days)
    total_return = float(
        nav["cumulative_nav"].iloc[-1] / nav["cumulative_nav"].iloc[0] - 1
    )
    annual_return = (
        float((1 + total_return) ** (365.25 / days) - 1)
        if days > 0 and total_return > -1
        else None
    )
    daily = nav.set_index("date")["cumulative_nav"].pct_change().dropna()
    annual_volatility = (
        float(daily.std(ddof=1) * np.sqrt(252)) if len(daily) > 1 else None
    )
    wealth = nav["cumulative_nav"] / nav["cumulative_nav"].iloc[0]
    drawdown = wealth / wealth.cummax() - 1
    max_drawdown = float(drawdown.min())
    monthly = (
        nav.set_index("date")["cumulative_nav"]
        .resample("ME")
        .last()
        .pct_change()
        .dropna()
    )
    positive_month_ratio = float((monthly > 0).mean()) if len(monthly) else None
    calmar = (
        float(annual_return / abs(max_drawdown))
        if annual_return is not None and max_drawdown < 0
        else None
    )
    stale_days = int((as_of.normalize() - nav["date"].iloc[-1].normalize()).days)

    return {
        "valid": True,
        "start_date": nav["date"].iloc[0].date().isoformat(),
        "end_date": nav["date"].iloc[-1].date().isoformat(),
        "history_days": days,
        "nav_points": int(len(nav)),
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "max_drawdown": max_drawdown,
        "positive_month_ratio": positive_month_ratio,
        "calmar": calmar,
        "stale_days": stale_days,
    }


def percentile_score(series: pd.Series, higher_is_better: bool) -> pd.Series:
    values = series.astype(float)
    score = values.rank(pct=True, method="average")
    return score if higher_is_better else 1 - score + (1 / max(len(values), 1))


def analyze(profile: dict, products: pd.DataFrame, nav: pd.DataFrame) -> dict:
    _require_columns(products, REQUIRED_PRODUCT_COLUMNS, "products")
    _require_columns(nav, REQUIRED_NAV_COLUMNS, "nav")
    as_of = pd.Timestamp(profile.get("as_of_date", pd.Timestamp.today().date()))
    allowed = set(profile.get("allowed_types") or [])
    max_drawdown_limit = profile.get("max_drawdown")

    records = []
    for product in products.to_dict("records"):
        code = str(product["code"]).zfill(6)
        metrics = calculate_metrics(
            nav[nav["code"].astype(str).str.zfill(6) == code], as_of
        )
        record = {**product, "code": code, **metrics, "hard_filter_reasons": []}
        if allowed and record["type"] not in allowed:
            record["hard_filter_reasons"].append("type_not_allowed")
        if record["purchase_status"] not in {
            "开放申购",
            "场内交易",
            "open",
            "exchange",
        }:
            record["hard_filter_reasons"].append("not_currently_open")
        if not metrics.get("valid"):
            record["hard_filter_reasons"].append(metrics.get("reason", "invalid_nav"))
        else:
            if metrics["history_days"] < int(profile.get("min_history_days", 365)):
                record["hard_filter_reasons"].append("history_too_short")
            if metrics["nav_points"] < int(profile.get("min_nav_points", 120)):
                record["hard_filter_reasons"].append("too_few_nav_points")
            if metrics["stale_days"] > int(profile.get("max_stale_days", 14)):
                record["hard_filter_reasons"].append("stale_nav")
            if max_drawdown_limit is not None and abs(metrics["max_drawdown"]) > float(
                max_drawdown_limit
            ):
                record["hard_filter_reasons"].append("drawdown_exceeds_limit")
        record["eligible"] = not record["hard_filter_reasons"]
        records.append(record)

    eligible = pd.DataFrame([r for r in records if r["eligible"]])
    if not eligible.empty:
        scored_groups = []
        for _, group in eligible.groupby("type", dropna=False):
            group = group.copy()
            components = {
                "annual_return": percentile_score(group["annual_return"], True),
                "max_drawdown": percentile_score(group["max_drawdown"].abs(), False),
                "annual_volatility": percentile_score(
                    group["annual_volatility"], False
                ),
                "positive_month_ratio": percentile_score(
                    group["positive_month_ratio"], True
                ),
                "stale_days": percentile_score(group["stale_days"], False),
            }
            group["peer_score"] = 100 * (
                0.25 * components["annual_return"]
                + 0.30 * components["max_drawdown"]
                + 0.20 * components["annual_volatility"]
                + 0.15 * components["positive_month_ratio"]
                + 0.10 * components["stale_days"]
            )
            scored_groups.append(group)
        eligible = pd.concat(scored_groups).sort_values(
            ["type", "peer_score"], ascending=[True, False]
        )
        score_map = dict(zip(eligible["code"], eligible["peer_score"].round(1)))
        for record in records:
            record["peer_score"] = score_map.get(record["code"])

    failures = sum(1 for r in records if not r.get("valid", False))
    return {
        "as_of_date": as_of.date().isoformat(),
        "coverage": {
            "products_received": len(records),
            "nav_valid": len(records) - failures,
            "nav_failed": failures,
            "eligible": sum(1 for r in records if r["eligible"]),
            "filtered": sum(1 for r in records if not r["eligible"]),
        },
        "channel_note": "public_open does not mean ant_verified",
        "candidates": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--products", required=True)
    parser.add_argument("--nav", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    profile = json.loads(Path(args.profile).read_text(encoding="utf-8"))
    products = pd.read_csv(args.products, dtype={"code": str})
    nav = pd.read_csv(args.nav, dtype={"code": str})
    result = analyze(profile, products, nav)
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
