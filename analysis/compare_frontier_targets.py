"""Read-only matched-target sensitivity analysis of local audit snapshots."""

import json
import sqlite3
import sys
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import linprog, minimize

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from core.frontier import estimate_covariance  # noqa: E402


# Frozen legacy formula, only for reproducing the pre-rebuild comparison.
def shrink_frontier_expected_returns(values):
    return 0.65 * values + 0.35 * values.mean()


codes = [
    "270023",
    "539002",
    "016149",
    "007339",
    "002611",
    "007280",
    "012643",
    "001211",
    "006662",
    "014429",
]
with sqlite3.connect(
    f"file:{ROOT / 'backend/data/audit.sqlite3'}?mode=ro", uri=True
) as con:
    records = {
        key: json.loads(
            con.execute(
                "SELECT input_json FROM decisions WHERE created_at LIKE ?",
                ("%2026-09-19 " + stamp + "%",),
            ).fetchone()[0]
        )
        for key, stamp in [("old", "05:40:26"), ("new", "06:34:52")]
    }
frames = {
    key: pd.DataFrame(
        {
            code: pd.Series(
                data["values"]["values"], index=pd.to_datetime(data["values"]["index"])
            )
            for code, data in record["market_snapshot"]["total_return_series"].items()
        }
    )[codes]
    .resample("ME")
    .last()
    .loc["2023-09-19":"2026-08-31"]
    for key, record in records.items()
}
assert np.array_equal(frames["old"].values, frames["new"].values)
r = frames["new"].pct_change().dropna()
mu = shrink_frontier_expected_returns(r.mean()).to_numpy()
cov = estimate_covariance(r, "fixed_20")[0].to_numpy()
old = np.array([records["old"]["strategy_spec"]["target_weights"][c] for c in codes])
new = np.array([records["new"]["strategy_spec"]["target_weights"][c] for c in codes])
targets = {"old_return": old @ mu, "new_return": new @ mu}


def solve(c, means, target, lower=None, upper=None):
    n = len(means)
    lo = np.zeros(n) if lower is None else lower
    hi = np.ones(n) if upper is None else upper
    q = c / (np.trace(c) / n)
    v = means / max(abs(means))
    a = np.array([np.ones(n), v])
    b = np.array([1, target / max(abs(means))])
    bounds = list(zip(lo, hi))
    feasible = linprog(np.zeros(n), A_eq=a, b_eq=b, bounds=bounds, method="highs")
    if not feasible.success:
        return None
    res = minimize(
        lambda w: w @ q @ w,
        feasible.x,
        jac=lambda w: 2 * q @ w,
        bounds=bounds,
        constraints={"type": "eq", "fun": lambda w: a @ w - b, "jac": lambda w: a},
        method="SLSQP",
        options={"ftol": 1e-13, "maxiter": 2000},
    )
    assert res.success, res.message
    w = res.x
    assert max(abs(a @ w - b)) < 1e-8 and min(w - lo) > -1e-8 and min(hi - w) > -1e-8
    free = (w > lo + 1e-7) & (w < hi - 1e-7)
    g = 2 * q @ w
    lam = np.linalg.lstsq(a[:, free].T, -g[free], rcond=None)[0]
    station = g + a.T @ lam
    residual = max(
        np.max(abs(station[free]), initial=0),
        np.max(-station[w <= lo + 1e-7], initial=0),
        np.max(station[w >= hi - 1e-7], initial=0),
    )
    assert residual < 2e-5, residual
    return w, float(residual)


def stats(w):
    return {
        "weights": dict(zip(codes, w.tolist())),
        "annual_model_return": float(w @ mu * 12),
        "annual_model_vol": float(np.sqrt(w @ cov @ w * 12)),
        "bond_weight": float(w[2]),
        "largest_weight": float(max(w)),
        "old_target_reallocation": float(abs(w - old).sum() / 2),
    }


rows = []
for label, w in [("original_old", old), ("original_new", new)]:
    rows.append({"case": label, **stats(w)})
maxkkt = 0
for label, target in targets.items():
    for cap in [1.0, 0.5, 0.3, 0.2]:
        result = solve(cov, mu, target, upper=np.full(len(codes), cap))
        assert result is not None
        w, kkt = result
        maxkkt = max(maxkkt, kkt)
        scaled = solve(
            cov * 10000, mu * 100, target * 100, upper=np.full(len(codes), cap)
        )[0]
        assert max(abs(w - scaled)) < 1e-6
        rows.append(
            {"case": label + "_cap_" + str(cap), "kkt_residual": kkt, **stats(w)}
        )
    for band in [0.05, 0.10]:
        result = solve(
            cov,
            mu,
            target,
            lower=np.maximum(0, old - band),
            upper=np.minimum(1, old + band),
        )
        rows.append(
            {
                "case": label + "_band_" + str(band),
                **(stats(result[0]) if result else {"infeasible": True}),
            }
        )

sensitivity = []
for cap in [1.0, 0.5]:
    for perturb in [-0.005, 0, 0.005]:
        means = mu.copy()
        means[2] += perturb / 12
        result = solve(
            cov, means, targets["old_return"], upper=np.full(len(codes), cap)
        )
        sensitivity.append(
            {
                "case": "bond_mean_annual_shift",
                "shift": perturb,
                "cap": cap,
                **stats(result[0]),
            }
        )
    for factor in [1.0, 1.5, 2.0]:
        diagonal = np.ones(len(codes))
        diagonal[2] = factor
        c = cov * np.outer(diagonal, diagonal)
        result = solve(c, mu, targets["old_return"], upper=np.full(len(codes), cap))
        sensitivity.append(
            {
                "case": "bond_vol_multiplier",
                "factor": factor,
                "cap": cap,
                **stats(result[0]),
            }
        )

rng = np.random.default_rng(20260919)
boot = []
for rep in range(200):
    starts = rng.integers(0, len(r), size=int(np.ceil(len(r) / 3)))
    ix = ((starts[:, None] + np.arange(3)) % len(r)).flatten()[: len(r)]
    sample = r.iloc[ix]
    means = shrink_frontier_expected_returns(sample.mean()).to_numpy()
    c = estimate_covariance(sample, "fixed_20")[0].to_numpy()
    for cap in [1.0, 0.5]:
        result = solve(c, means, targets["old_return"], upper=np.full(len(codes), cap))
        boot.append(
            {
                "replicate": rep,
                "cap": cap,
                "feasible": result is not None,
                **(
                    {
                        "bond_weight": float(result[0][2]),
                        "largest_weight": float(max(result[0])),
                    }
                    if result
                    else {}
                ),
            }
        )

output = {
    "protocol": "Same 35 realized monthly returns, fixed20 covariance, 35% mean shrinkage, matched annual model return; cap/band settings are unapproved sensitivity cases. 200 circular 3-month block resamples describe conditional parameter sensitivity, not prospective performance. No production edits.",
    "dates": [str(r.index.min()), str(r.index.max())],
    "snapshot_hashes": {
        k: d["market_snapshot"]["data_hash"] for k, d in records.items()
    },
    "inputs": {
        "raw_annual_means": dict(zip(codes, (r.mean() * 12).tolist())),
        "shrunk_annual_means": dict(zip(codes, (mu * 12).tolist())),
        "annual_vols": dict(zip(codes, np.sqrt(np.diag(cov) * 12).tolist())),
    },
    "comparisons": rows,
    "sensitivity": sensitivity,
    "bootstrap": boot,
    "max_kkt_residual_main": maxkkt,
}
Path("/tmp/target_comparison_results.json").write_text(json.dumps(output, indent=2))
for row in rows:
    print(
        row["case"],
        {
            k: round(v * 100, 4)
            for k, v in row.items()
            if k
            in [
                "annual_model_return",
                "annual_model_vol",
                "bond_weight",
                "old_target_reallocation",
            ]
        },
        flush=True,
    )
print("INPUTS", output["inputs"])
print(
    "SENSITIVITY",
    [
        (x["case"], x.get("shift", x.get("factor")), x["cap"], x["bond_weight"])
        for x in sensitivity
    ],
)
for cap in [1.0, 0.5]:
    b = pd.DataFrame(boot)
    b = b[b.cap == cap]
    print(
        "BOOT",
        cap,
        "infeasible",
        sum(~b.feasible),
        "bond_quantiles",
        b.bond_weight.quantile([0.1, 0.5, 0.9]).to_dict(),
    )
