#!/usr/bin/env python3
"""Fetch a public-fund candidate pool and cumulative NAVs through AKShare."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import time

import pandas as pd


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fund-type",
        required=True,
        help="AKShare rank category, e.g. 债券型 or 指数型",
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--limit", type=int, default=0, help="0 means all candidates")
    parser.add_argument("--delay", type=float, default=0.25)
    args = parser.parse_args()
    try:
        import akshare as ak
    except ImportError as exc:
        raise SystemExit(
            "akshare is required: install it in the active Python environment"
        ) from exc

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    fetched_at = datetime.now(timezone.utc).isoformat()
    ranked = ak.fund_open_fund_rank_em(symbol=args.fund_type)
    if args.limit > 0:
        ranked = ranked.head(args.limit)
    code_col = next((c for c in ranked.columns if "基金代码" in str(c)), None)
    name_col = next(
        (c for c in ranked.columns if "基金简称" in str(c) or "基金名称" in str(c)),
        None,
    )
    if not code_col or not name_col:
        raise SystemExit(f"unexpected rank columns: {list(ranked.columns)}")

    products = []
    nav_frames = []
    failures = []
    for row in ranked.to_dict("records"):
        code = str(row[code_col]).zfill(6)
        products.append(
            {
                "code": code,
                "name": row[name_col],
                "type": args.fund_type,
                "purchase_status": "待查询",
                "channel_status": "unknown",
                "source": "akshare.fund_open_fund_rank_em",
                "fetched_at": fetched_at,
            }
        )
        try:
            frame = ak.fund_open_fund_info_em(
                symbol=code, indicator="累计净值走势", period="成立来"
            )
            frame = frame.rename(
                columns={"净值日期": "date", "累计净值": "cumulative_nav"}
            )
            if not {"date", "cumulative_nav"}.issubset(frame.columns):
                raise ValueError(f"unexpected NAV columns: {list(frame.columns)}")
            frame = frame[["date", "cumulative_nav"]].copy()
            frame.insert(0, "code", code)
            nav_frames.append(frame)
        except Exception as exc:  # preserve per-product failures for coverage reporting
            failures.append(
                {"code": code, "error": type(exc).__name__, "message": str(exc)[:300]}
            )
        time.sleep(max(args.delay, 0))

    pd.DataFrame(products).to_csv(
        out / "products.csv", index=False, encoding="utf-8-sig"
    )
    if nav_frames:
        pd.concat(nav_frames, ignore_index=True).to_csv(
            out / "nav.csv", index=False, encoding="utf-8-sig"
        )
    (out / "fetch_manifest.json").write_text(
        json.dumps(
            {
                "fund_type": args.fund_type,
                "fetched_at": fetched_at,
                "candidate_count": len(products),
                "nav_success_count": len(nav_frames),
                "nav_failure_count": len(failures),
                "failures": failures,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
