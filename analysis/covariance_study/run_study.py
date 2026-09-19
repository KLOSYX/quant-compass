"""Predefined offline covariance comparison. Never imported by production."""

import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import ledoit_wolf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from core.decision import ExecutionContext, PortfolioState, StrategySpec, plan_month  # noqa: E402
from core.frontier import _minimum_variance_weights, estimate_covariance  # noqa: E402

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "results"
METHODS = ["fixed_20", "ledoit_wolf"]
WINDOWS = [24, 36, 60, 120]
SEED = 20260919


def covariance(x, method):
    if method in METHODS:
        frame = pd.DataFrame(x)
        c, a = estimate_covariance(frame, method)
        return c.to_numpy(), a
    c = np.cov(x, rowvar=False, ddof=0)
    c = np.atleast_2d(c)
    target = (
        np.diag(np.diag(c))
        if method == "diagonal20_ml"
        else np.eye(c.shape[0]) * np.trace(c) / c.shape[0]
    )
    return 0.8 * c + 0.2 * target, 0.2


def solve(c, mu=None, initial=None):
    n = len(c)
    scale = max(np.trace(c) / n, 1e-14)
    q = c / scale
    start = np.full(n, 1 / n) if initial is None else initial
    constraints = [
        {"type": "eq", "fun": lambda w: w.sum() - 1, "jac": lambda w: np.ones(n)}
    ]
    if mu is not None:
        v = mu / max(np.max(np.abs(mu)), 1e-12)
        floor = (v.min() + v.max()) / 2
        constraints.append(
            {"type": "ineq", "fun": lambda w: w @ v - floor, "jac": lambda w: v}
        )
    answer = minimize(
        lambda w: w @ q @ w,
        start,
        jac=lambda w: 2 * q @ w,
        method="SLSQP",
        bounds=[(0, 1)] * n,
        constraints=constraints,
        options={"ftol": 1e-11, "maxiter": 400},
    )
    feasible = (
        abs(answer.x.sum() - 1) < 1e-7
        and answer.x.min() > -1e-8
        and all(float(con["fun"](answer.x)) >= -1e-7 for con in constraints)
    )
    if not answer.success or not feasible:
        raise RuntimeError(f"QP failed: {answer.message}")
    w = np.maximum(answer.x, 0)
    w /= w.sum()
    return w


def metrics(r):
    r = np.asarray(r, dtype=float)
    wealth = np.r_[1, np.cumprod(1 + r)]
    vol = float(np.std(r, ddof=1) * np.sqrt(12)) if len(r) > 1 else 0
    return {
        "months": len(r),
        "annual_return": float(wealth[-1] ** (12 / len(r)) - 1),
        "volatility": vol,
        "sharpe": float(np.mean(r) * 12 / vol) if vol > 1e-12 else None,
        "max_drawdown": float(np.max(1 - wealth / np.maximum.accumulate(wealth))),
        "terminal_multiple": float(wealth[-1]),
    }


def universe_data():
    meta = json.loads((DATA / "provenance.json").read_text())
    universes = {
        name: pd.read_csv(DATA / f"{name}.csv", index_col=0, parse_dates=True)
        for name in meta["french"]
    }
    # Missing industries removed based only on the training era, not future returns.
    for name, frame in universes.items():
        eligible = frame.loc[:"1999-12-31"].notna().all()
        universes[name] = frame.loc[:, eligible]
        assert not universes[name].isna().any().any(), name
    included = {
        c: rec for c, rec in meta.get("china", {}).items() if "excluded" not in rec
    }
    groups = {
        "cn_all": list(included),
        "cn_bond": [c for c, r in included.items() if "债券" in r["type"]],
        "cn_equity": [
            c
            for c, r in included.items()
            if "债券" not in r["type"]
            and "QDII" not in r["type"]
            and "海外" not in r["type"]
            and "黄金" not in r["name"]
        ],
        "cn_overseas_gold": [
            c
            for c, r in included.items()
            if "QDII" in r["type"] or "海外" in r["type"] or "黄金" in r["name"]
        ],
    }
    groups["cn_distinct"] = [c for c in included if c not in ["110018", "000217"]]
    for name, codes in groups.items():
        if len(codes) < 2:
            continue
        frame = pd.concat(
            [
                pd.read_csv(DATA / f"cn_{c}.csv", index_col=0, parse_dates=True)
                for c in codes
            ],
            axis=1,
            join="inner",
        ).dropna()
        if len(frame) >= 96:
            universes[name] = frame
    rng = np.random.default_rng(SEED)
    base = universes["us_industry49"]
    for size in [4, 8, 16]:
        for sample in range(5):
            cols = sorted(rng.choice(base.columns, size, replace=False))
            universes[f"us_sub{size}_{sample}"] = base[cols]
    (OUT / "universes.json").write_text(
        json.dumps(
            {
                k: {
                    "assets": list(v.columns),
                    "rows": len(v),
                    "first": str(v.index.min().date()),
                    "last": str(v.index.max().date()),
                }
                for k, v in universes.items()
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return universes


def historical(name, frame, window, objective, method, lag=0):
    x = frame.to_numpy()
    dates = frame.index
    weights = []
    rets = []
    turn = []
    alpha = []
    variance = []
    errors = []
    evaldates = []
    previous = None
    for pos in range(window + lag, len(x)):
        if dates[pos] < pd.Timestamp("2000-01-01"):
            continue
        train = x[pos - window - lag : pos - lag]
        c, a = covariance(train, method)
        raw_mu = train.mean(axis=0)
        mu = (
            0.65 * raw_mu + 0.35 * raw_mu.mean()
            if objective == "mid_return_floor"
            else None
        )
        w = solve(c, mu, previous)
        traded = (
            1.0
            if previous is None
            else np.abs(
                w - previous * (1 + x[pos - 1]) / (1 + previous @ x[pos - 1])
            ).sum()
        )
        r = float(w @ x[pos])
        weights.append(w)
        rets.append(r)
        turn.append(traded)
        alpha.append(a)
        variance.append(float(w @ c @ w))
        errors.append(float((r - w @ raw_mu) ** 2))
        evaldates.append(dates[pos])
        previous = w
    if len(rets) < 24:
        return None
    weights = np.asarray(weights)
    rets = np.asarray(rets)
    turn = np.asarray(turn)
    alpha = np.asarray(alpha)
    variance = np.asarray(variance)
    errors = np.asarray(errors)
    dates = pd.DatetimeIndex(evaldates)
    case = f"{name}__{window}__{objective}__{method}__lag{lag}"
    np.savez_compressed(
        OUT / "traces" / f"{case}.npz",
        weights=weights,
        returns=rets,
        turnover=turn,
        alpha=alpha,
        variance=variance,
        squared_errors=errors,
        dates=dates.strftime("%Y-%m-%d").to_numpy(dtype="U10"),
    )
    rows = []
    periods = {
        "full": np.ones(len(dates), bool),
        "2000_2007": dates.year <= 2007,
        "2008_2015": (dates.year >= 2008) & (dates.year <= 2015),
        "2016_2025": dates.year >= 2016,
    }
    if name.startswith("cn_"):
        midpoint = len(dates) // 2
        periods = {
            "full": np.ones(len(dates), bool),
            "first_half": np.arange(len(dates)) < midpoint,
            "second_half": np.arange(len(dates)) >= midpoint,
        }
    for fee in [0, 0.0015, 0.005]:
        net = (1 - fee * turn) * (1 + rets) - 1
        for period, mask in periods.items():
            if mask.sum() < 12:
                continue
            idx = np.flatnonzero(mask)
            changes = np.abs(np.diff(weights[idx], axis=0)).sum(axis=1) / 2
            rows.append(
                {
                    "universe": name,
                    "window": window,
                    "objective": objective,
                    "method": method,
                    "lag": lag,
                    "fee": fee,
                    "period": period,
                    "assets": frame.shape[1],
                    **metrics(net[mask]),
                    "first": str(dates[mask][0].date()),
                    "last": str(dates[mask][-1].date()),
                    "annual_turnover": float(turn[mask].mean() * 12),
                    "weight_stability": float(1 - changes.mean())
                    if len(changes)
                    else None,
                    "max_weight_mean": float(weights[mask].max(axis=1).mean()),
                    "shrinkage_mean": float(alpha[mask].mean()),
                    "variance_mse": float(
                        np.mean((variance[mask] - errors[mask]) ** 2)
                    ),
                    "variance_qlike": float(
                        np.mean(
                            np.log(np.maximum(variance[mask], 1e-12))
                            + errors[mask] / np.maximum(variance[mask], 1e-12)
                        )
                    ),
                }
            )
    return rows


def bootstrap_comparisons(universes):
    rng = np.random.default_rng(SEED)
    rows = []
    for name in universes:
        if name.startswith("us_sub"):
            continue
        for obj in ["gmv", "mid_return_floor"]:
            paths = [
                OUT / "traces" / f"{name}__36__{obj}__{m}__lag0.npz" for m in METHODS
            ]
            if not all(p.exists() for p in paths):
                continue
            traces = [np.load(p) for p in paths]
            rs = [(1 - 0.0015 * t["turnover"]) * (1 + t["returns"]) - 1 for t in traces]
            n = len(rs[0])
            assert n == len(rs[1])
            starts = rng.integers(0, n, size=(2000, int(np.ceil(n / 12))))
            idx = ((starts[:, :, None] + np.arange(12)) % n).reshape(2000, -1)[:, :n]
            a, b = rs[0][idx], rs[1][idx]
            va = a.std(axis=1, ddof=1) * np.sqrt(12)
            vb = b.std(axis=1, ddof=1) * np.sqrt(12)
            dv = vb - va
            ds = b.mean(axis=1) * 12 / vb - a.mean(axis=1) * 12 / va
            rows.append(
                {
                    "universe": name,
                    "objective": obj,
                    "months": n,
                    "vol_diff_low": float(np.quantile(dv, 0.025)),
                    "vol_diff_high": float(np.quantile(dv, 0.975)),
                    "sharpe_diff_low": float(np.quantile(ds, 0.025)),
                    "sharpe_diff_high": float(np.quantile(ds, 0.975)),
                }
            )
    pd.DataFrame(rows).to_csv(OUT / "bootstrap.csv", index=False)


def implementation_checks():
    rng = np.random.default_rng(SEED)
    checks = []
    for name, x in [
        ("normal", rng.normal(size=(36, 8))),
        (
            "mixed_scale",
            rng.normal(size=(36, 6)) * [0.003, 0.004, 0.01, 0.04, 0.06, 0.1],
        ),
        ("one_asset", rng.normal(size=(36, 1))),
        ("constant", np.ones((36, 4))),
        ("high_dimension", rng.normal(size=(24, 49))),
    ]:
        c, a = covariance(x, "ledoit_wolf")
        ref, ra = ledoit_wolf(x)
        error = float(np.max(np.abs(c - ref)))
        assert error < 1e-10 and abs(a - ra) < 1e-10, (name, error, a, ra)
        assert np.linalg.eigvalsh(c).min() > -1e-10
        checks.append(
            {
                "case": name,
                "max_covariance_error": error,
                "shrinkage_error": abs(a - ra),
            }
        )
    x = rng.normal(size=(36, 4)) * [0.002, 0.005, 0.02, 0.06]
    w, _ = _minimum_variance_weights(pd.DataFrame(x), "fixed_20")
    w2, _ = _minimum_variance_weights(pd.DataFrame(x * 100), "fixed_20")
    c, _ = covariance(x, "fixed_20")
    cw = solve(c)
    cw2 = solve(c * 10000)
    checks.append(
        {
            "case": "solver_unit_invariance",
            "production_weights_decimal": w.to_list(),
            "production_weights_percent": w2.to_list(),
            "scaled_solver_weights": cw.tolist(),
            "scaled_solver_max_unit_error": float(np.abs(cw - cw2).max()),
            "production_variance_over_verified_optimum": float(
                w @ c @ w / (cw @ c @ cw)
            ),
        }
    )
    (OUT / "implementation_checks.json").write_text(json.dumps(checks, indent=2))


def synthetic():
    rng = np.random.default_rng(SEED)
    rows = []
    for kind in [
        "equal_variance",
        "mixed_stock_bond",
        "strong_correlation",
        "duplicate",
        "high_dimension",
        "regime_shift",
    ]:
        p = 49 if kind == "high_dimension" else 6
        vol = (
            np.full(p, 0.04)
            if kind != "mixed_stock_bond"
            else np.array([0.003, 0.005, 0.01, 0.03, 0.05, 0.08])
        )
        corr = np.full(
            (p, p), 0.85 if kind in ["strong_correlation", "duplicate"] else 0.25
        )
        np.fill_diagonal(corr, 1)
        true = np.outer(vol, vol) * corr
        if kind == "duplicate":
            true[-1] = true[0]
            true[:, -1] = true[:, 0]
        for n in [24, 36, 60, 120]:
            for rep in range(100):
                x = rng.multivariate_normal(np.zeros(p), true, size=n)
                future = true.copy()
                if kind == "regime_shift":
                    x[n // 2 :] *= 2
                    future = true * 4
                for method in METHODS:
                    c, a = covariance(x, method)
                    w = solve(c)
                    rows.append(
                        {
                            "kind": kind,
                            "window": n,
                            "replicate": rep,
                            "method": method,
                            "frobenius_relative": float(
                                np.linalg.norm(c - future) / np.linalg.norm(future)
                            ),
                            "true_variance": float(w @ future @ w),
                            "max_weight": float(w.max()),
                            "alpha": a,
                        }
                    )
    pd.DataFrame(rows).to_csv(OUT / "synthetic.csv", index=False)


def execute_account(name, frame, refresh, scenario, method):
    # Total-return proxies: distributions reinvested, no claim about actual dealing dates.
    window = 36
    indices = [
        i
        for i in range(window, len(frame))
        if frame.index[i] >= pd.Timestamp("2000-01-01")
    ]
    if len(indices) < 60:
        return []
    # Predefined entry cohorts avoid attributing the result to one chosen starting target.
    rows = []
    for start in indices[::60]:
        if len(frame) - start < 60:
            continue
        codes = list(frame)
        h = {c: 100000 / len(codes) for c in codes}
        cash = 5000.0
        pending = 0.0
        available = 0.0
        returns = []
        sale_total = fees_total = 0.0
        target = None
        rebalance_months = 0
        monthly = 0.0 if scenario == "zero_contribution" else 1000.0
        fee_multiplier = 2 if scenario == "double_fees" else 1
        for pos in range(start, len(frame)):
            before = sum(h.values()) + cash + pending
            cash += pending
            available += pending
            pending = 0.0
            if target is None or (refresh == "annual" and (pos - start) % 12 == 0):
                c, _ = covariance(frame.iloc[pos - window : pos].to_numpy(), method)
                target = dict(zip(codes, solve(c)))
            result = plan_month(
                columns=codes,
                state=PortfolioState(h, cash),
                strategy=StrategySpec("offline-covariance", target),
                context=ExecutionContext(
                    monthly_budget=monthly,
                    buy_fees={c: 0.0015 * fee_multiplier for c in codes},
                    sell_fees={c: 0.005 * fee_multiplier for c in codes},
                    minimum_cash_reserve=5000.0,
                    available_existing_cash=min(available, max(0.0, cash - 5000)),
                    investment_limits={c: {"monthly_limit": 500.0} for c in codes}
                    if scenario == "capped"
                    else {},
                    rebalance_band=0.02,
                ),
                as_of=frame.index[pos],
            )
            available = max(0.0, available - max(0.0, result.total_gross_buy - monthly))
            h = {
                c: result.funds[c].executable_holding * (1 + float(frame.iloc[pos][c]))
                for c in codes
            }
            cash = result.cash_after
            pending = result.pending_sale_proceeds
            # independent account identity before applying market returns
            ledger_after = (
                sum(f.executable_holding for f in result.funds.values())
                + cash
                + pending
            )
            assert abs(ledger_after + result.transaction_fees - before - monthly) < 1e-5
            assert cash >= 5000.0 - 1e-7
            returns.append((sum(h.values()) + cash + pending) / (before + monthly) - 1)
            sale_total += result.total_gross_sell
            fees_total += result.transaction_fees
            rebalance_months += int(result.total_gross_sell > 0)
        rows.append(
            {
                "universe": name,
                "refresh": refresh,
                "scenario": scenario,
                "method": method,
                "entry": str(frame.index[start].date()),
                **metrics(returns),
                "terminal_wealth": sum(h.values()) + cash + pending,
                "gross_sold": sale_total,
                "fees": fees_total,
                "sale_months": rebalance_months,
            }
        )
    return rows


def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "traces").mkdir(exist_ok=True)
    implementation_checks()
    universes = universe_data()
    rows = []
    start = time.time()
    for name, frame in universes.items():
        subset = name.startswith("us_sub")
        for window in [36] if subset else WINDOWS:
            for obj in ["gmv"] if subset else ["gmv", "mid_return_floor"]:
                methods = METHODS + (
                    ["diagonal20_ml", "identity20_ml"]
                    if not subset and window == 36 and obj == "gmv"
                    else []
                )
                for method in methods:
                    result = historical(name, frame, window, obj, method)
                    if result:
                        rows.extend(result)
        if not subset:
            for method in METHODS:
                result = historical(name, frame, 36, "gmv", method, lag=1)
                if result:
                    rows.extend(result)
        pd.DataFrame(rows).to_csv(OUT / "historical.csv", index=False)
        print("historical", name, len(rows), round(time.time() - start, 1), flush=True)
    bootstrap_comparisons(universes)
    synthetic()
    print("synthetic complete", flush=True)
    accounts = []
    for name, frame in universes.items():
        if name.startswith("us_sub"):
            continue
        for refresh in ["fixed", "annual"]:
            for scenario in ["normal", "zero_contribution", "capped", "double_fees"]:
                for method in METHODS:
                    accounts.extend(
                        execute_account(name, frame, refresh, scenario, method)
                    )
        pd.DataFrame(accounts).to_csv(OUT / "accounts.csv", index=False)
        print(
            "accounts", name, len(accounts), round(time.time() - start, 1), flush=True
        )
    hashes = {
        str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [HERE / "PROTOCOL.md", Path(__file__), *DATA.glob("*.csv")]
    }
    (OUT / "run_manifest.json").write_text(
        json.dumps(
            {
                "seed": SEED,
                "hashes": hashes,
                "elapsed_seconds": time.time() - start,
                "python": sys.version,
                "production_revision": "5b26353",
            },
            indent=2,
        )
    )
    print("DONE", round(time.time() - start, 1), flush=True)


if __name__ == "__main__":
    main()
