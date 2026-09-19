"""Offline numerical/window checks; uses local snapshots, never downloads data."""

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from core.frontier import calculate_efficient_frontier  # noqa: E402


def validate():
    data = ROOT / "analysis/covariance_study/data"
    metadata = json.loads((data / "provenance.json").read_text())
    universes = {
        name: pd.read_csv(data / f"{name}.csv", index_col=0).dropna(axis=1)
        for name in metadata["french"]
    }
    universes["cn_local15"] = pd.concat(
        [pd.read_csv(path, index_col=0) for path in sorted(data.glob("cn_*.csv"))],
        axis=1,
        join="inner",
    ).dropna()
    with sqlite3.connect(
        f"file:{ROOT / 'backend/data/audit.sqlite3'}?mode=ro", uri=True
    ) as con:
        row = con.execute(
            "SELECT input_json FROM decisions WHERE created_at LIKE ?",
            ("%2026-09-19 06:34:52%",),
        ).fetchone()
    snapshot = json.loads(row[0])["market_snapshot"]["total_return_series"]
    actual_nav = pd.DataFrame(
        {
            code: pd.Series(
                item["values"]["values"], index=pd.to_datetime(item["values"]["index"])
            )
            for code, item in snapshot.items()
        }
    )
    actual_nav = actual_nav.resample("ME").last().loc["2023-09-19":"2026-08-31"]
    actual_returns = actual_nav.pct_change(fill_method=None).dropna()
    universes["user_snapshot"] = actual_returns
    rows = []
    for name, frame in universes.items():
        for window in (24, 36, 60):
            for endpoint in sorted(set([len(frame) // 2, len(frame)])):
                if endpoint < window:
                    continue
                returns = frame.iloc[endpoint - window : endpoint]
                nav = pd.concat(
                    [
                        pd.DataFrame(
                            [np.ones(len(frame.columns))], columns=frame.columns
                        ),
                        (1 + returns).cumprod(),
                    ],
                    ignore_index=True,
                )
                for method in ("sample", "fixed_20"):
                    points = calculate_efficient_frontier(
                        nav, {}, covariance_method=method
                    )
                    certified = [p for p in points if not p.get("solver_fallback_used")]
                    for p in certified:
                        w = pd.Series(p["weights"])
                        assert abs(w.sum() - 1) < 1e-8 and w.min() >= 0
                        assert abs(p["return"] - w @ returns.mean() * 12) < 1e-9
                        assert p["solver_optimality_gap"] <= 1e-7
                    rows.append(
                        dict(
                            universe=name,
                            window=window,
                            endpoint=str(frame.index[endpoint - 1]),
                            method=method,
                            points=len(certified),
                            maximum_gap=max(
                                (p["solver_optimality_gap"] for p in certified),
                                default=None,
                            ),
                            maximum_weight=max(
                                (p["max_asset_weight"] for p in certified), default=None
                            ),
                            minimum_variance_weights=points[0]["weights"],
                        )
                    )
    report = [
        "# 零基线离线核验",
        "",
        "这是数值正确性与估计窗口检查，不是独立收益预测验证。数据沿用本地历史快照；未下载或提交大体积数据。",
        "",
        f"覆盖 {len(universes)} 个资产池、{len(rows)} 组窗口/截点/协方差组合。",
        "",
        "|资产池|组合数|通过证书的点数|少于20点的组合|最大归一化最优性差距|",
        "|---|---:|---:|---:|---:|",
    ]
    for name in universes:
        group = [r for r in rows if r["universe"] == name]
        report.append(
            f"|{name}|{len(group)}|{sum(r['points'] for r in group)}|{sum(r['points'] < 20 for r in group)}|{max(r['maximum_gap'] or 0 for r in group):.3g}|"
        )
    report += [
        "",
        "同一资产池、同一截点和协方差方法，仅改变24/36/60期窗口：",
        "",
        "|资产池|最低方差权重最大单边换手差异|",
        "|---|---:|",
    ]
    for name in universes:
        group = [r for r in rows if r["universe"] == name]
        drifts = [
            float(
                (
                    pd.Series(a["minimum_variance_weights"])
                    - pd.Series(b["minimum_variance_weights"])
                )
                .abs()
                .sum()
                / 2
            )
            for a in group
            for b in group
            if a["endpoint"] == b["endpoint"]
            and a["method"] == b["method"]
            and a["window"] != b["window"]
        ]
        if drifts:
            report.append(f"|{name}|{max(drifts):.1%}|")
    report += [
        "",
        "用户原报告快照中，016149 自身历史算术年化收益为 "
        f"{actual_returns['016149'].mean() * 1200:.4f}%，没有向股票/黄金平均收益收缩。",
        "",
        "最高收益端点可以集中于单一资产；这符合当前无集中度偏好的数学可行域，不代表适合家庭配置。最低方差目标也可能随窗口改变，需用户确认固定。",
        "",
        "未通过证书的点不展示为最优；少于20点必须检查失败记录。样本协方差在资产数超过样本数时可以奇异，权重可能不唯一。",
        "",
        "复现：`OPENBLAS_NUM_THREADS=1 backend/.venv/bin/python analysis/zero_baseline/validate.py`。输入来源与偏差见 `analysis/covariance_study/data/provenance.json`；用户快照依赖本地审计库。",
    ]
    Path(__file__).with_name("VALIDATION.md").write_text("\n".join(report) + "\n")
    Path("/tmp/zero_baseline_validation.json").write_text(json.dumps(rows, indent=2))
    print("\n".join(report[:18]))


if __name__ == "__main__":
    validate()
