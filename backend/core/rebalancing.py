"""Small fee-aware trade problem used by the existing monthly planner."""

import numpy as np
from scipy.optimize import linprog

from core.limits import monthly_investment_limit


def plan_rebalance_sales(
    *,
    holdings,
    targets,
    baseline_values,
    baseline_buys,
    buy_fees,
    sell_fees,
    investment_limits,
    redemption_limits,
    available_budget,
    band,
    timestamp,
    planned_purchase_days,
):
    """First minimize absolute fee-adjusted target deviation, then turnover.

    Direction bounds prevent simultaneous buying/selling. New-money purchases
    cannot be replaced by sales. Sales must fund additional executable purchases;
    no discretionary sale to idle cash is permitted by this problem.
    """
    codes = list(targets)
    capital = sum(targets.values())
    if capital <= 0:
        return {}, {"status": "no_allocatable_capital"}
    deviation = max(
        abs(baseline_values.get(c, 0) - targets[c]) / capital for c in codes
    )
    diagnostics = {"band": band, "max_deviation_after_new_money": deviation}
    if deviation <= band + 1e-9:
        return {}, {**diagnostics, "status": "within_band"}
    n = len(codes)
    weights = np.array([targets[c] / capital for c in codes])
    buy_rate = np.array([float(buy_fees.get(c, 0)) for c in codes])
    sell_rate = np.array([float(sell_fees.get(c, 0)) for c in codes])
    # b / (1+fee) is net purchased value; sell fee is withheld from gross.
    fee_vector = np.r_[buy_rate / (1 + buy_rate), sell_rate, np.zeros(n)]
    rows, rhs, bounds = [], [], []
    buy_bounds, sell_bounds = [], []
    for i, code in enumerate(codes):
        config = investment_limits.get(code)
        limit = monthly_investment_limit(
            config, timestamp, planned_purchase_days=planned_purchase_days
        )
        config = config.model_dump() if hasattr(config, "model_dump") else config or {}
        if config.get("limit_basis") == "net_asset_add":
            limit *= 1 + buy_rate[i]
        gap = targets[code] - baseline_values.get(code, 0)
        # Keep existing contribution purchases feasible, even if their fee makes
        # the resulting tiny gap switch sign.
        # Match execution: an uncapped fund can receive only its target gap,
        # not all proceeds that other (blocked) target funds cannot absorb.
        receiving_capacity = min(
            limit,
            max(0.0, targets[code] - holdings.get(code, 0)) * (1 + buy_rate[i]),
        )
        buy_bounds.append(
            (
                0,
                max(
                    baseline_buys.get(code, 0), receiving_capacity if gap > 1e-7 else 0
                ),
            )
        )
        sell_bounds.append(
            (
                0,
                min(
                    holdings.get(code, 0),
                    redemption_limits.get(code, holdings.get(code, 0)),
                )
                if gap < -1e-7 and baseline_buys.get(code, 0) <= 1e-7
                else 0,
            )
        )
        delta = weights[i] * fee_vector
        delta[i] += 1 / (1 + buy_rate[i])
        delta[n + i] -= 1
        for sign in (1, -1):
            row = sign * delta.copy()
            row[2 * n + i] = -1
            rows.append(row)
            rhs.append(sign * (targets[code] - holdings.get(code, 0)))
    funding = np.r_[np.ones(n), -(1 - sell_rate), np.zeros(n)]
    rows.extend([funding, -funding])
    rhs.extend([available_budget, -sum(baseline_buys.values())])
    bounds = buy_bounds + sell_bounds + [(0, None)] * n
    objective = np.r_[np.zeros(2 * n), np.ones(n)]
    try:
        first = linprog(objective, A_ub=rows, b_ub=rhs, bounds=bounds, method="highs")
        if not first.success:
            return {}, {
                **diagnostics,
                "status": "solver_fallback",
                "solver_message": first.message,
            }
        # Keep the best tracking error to sub-cent precision, then prefer fewer sales.
        second = linprog(
            np.r_[np.zeros(n), np.ones(n), np.zeros(n)],
            A_ub=[*rows, objective],
            b_ub=[*rhs, first.fun + 1e-6],
            bounds=bounds,
            method="highs",
        )
        if second.success:
            # A third LP expresses a true priority, not an arbitrary mixed weight.
            third = linprog(
                np.r_[np.ones(n), np.zeros(2 * n)],
                A_ub=[*rows, objective, np.r_[np.zeros(n), np.ones(n), np.zeros(n)]],
                b_ub=[*rhs, first.fun + 1e-6, second.fun + 1e-6],
                bounds=bounds,
                method="highs",
            )
            if third.success:
                second = third
    except (ValueError, RuntimeError) as exc:
        return {}, {
            **diagnostics,
            "status": "solver_fallback",
            "solver_message": str(exc),
        }
    answer = second if second.success else first
    if np.max(np.asarray(rows) @ answer.x - rhs) > 1e-5:
        return {}, {**diagnostics, "status": "solver_residual_failed"}
    sales = {
        c: float(np.floor((answer.x[n + i] + 1e-6) * 100) / 100)
        for i, c in enumerate(codes)
        if answer.x[n + i] >= 0.01
    }
    return sales, {
        **diagnostics,
        "status": "rebalance_planned"
        if sales
        else "purchase_capacity_or_redemption_limited",
        "continuous_tracking_error": float(first.fun),
        "solver": "highs-linear-tracking-then-turnover",
    }
