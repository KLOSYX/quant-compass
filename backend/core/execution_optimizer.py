from dataclasses import asdict, dataclass, field
from typing import Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.frontier import CovarianceMethod, estimate_covariance
from core.limits import allocate_capped_buy_amounts, get_monthly_investment_limits


AllocationMethod = Literal["proportional_gap", "constrained_tracking"]

ALLOCATION_METHODS = frozenset({"proportional_gap", "constrained_tracking"})
OPTIMIZER_EPSILON = 1e-9


@dataclass(frozen=True)
class BuyAllocationDiagnostics:
    method: str
    status: str
    fallback_used: bool
    message: str | None
    tracking_error_before: float | None
    tracking_error_after: float | None
    binding_limits: tuple[str, ...]
    substitute_purchases: Mapping[str, float]
    eligible_substitutes: tuple[str, ...]
    unspent_budget: float
    unspent_reason: str | None
    # Per-fund diagnostics let callers explain partial/zero purchases without
    # collapsing every case into the optimizer-level reason.
    unspent_reasons: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BuyAllocationResult:
    gross_allocations: Mapping[str, float]
    diagnostics: BuyAllocationDiagnostics


def normalize_substitute_for(
    fund_codes: Sequence[str],
    substitute_for: Mapping[str, str] | None,
) -> dict[str, str]:
    codes = list(dict.fromkeys(fund_codes))
    code_set = set(codes)
    supplied = {
        str(substitute).strip(): str(primary).strip()
        for substitute, primary in (substitute_for or {}).items()
        if str(primary or "").strip()
    }
    unknown_substitutes = sorted(set(supplied) - code_set)
    if unknown_substitutes:
        raise ValueError(
            "substitute_for contains substitutes outside the current analysis universe: "
            + ", ".join(unknown_substitutes)
        )
    unknown_primaries = sorted(set(supplied.values()) - code_set)
    if unknown_primaries:
        raise ValueError(
            "substitute_for references primary funds outside the current analysis universe: "
            + ", ".join(unknown_primaries)
        )
    self_references = sorted(
        substitute for substitute, primary in supplied.items() if substitute == primary
    )
    if self_references:
        raise ValueError(
            "a substitute fund cannot reference itself as primary: "
            + ", ".join(self_references)
        )
    chained_primaries = sorted(set(supplied.values()) & set(supplied))
    if chained_primaries:
        raise ValueError(
            "substitute relationships must reference strategic primary funds, not other "
            "substitutes: " + ", ".join(chained_primaries)
        )
    return supplied


def validate_execution_metadata(
    *,
    fund_codes: Sequence[str],
    target_weights: Mapping[str, float],
    substitute_for: Mapping[str, str] | None,
    allocation_method: str,
) -> dict[str, str]:
    if allocation_method not in ALLOCATION_METHODS:
        raise ValueError(
            f"execution_allocation_method must be one of {sorted(ALLOCATION_METHODS)}"
        )
    relationships = normalize_substitute_for(fund_codes, substitute_for)
    invalid_substitute_targets = sorted(
        code
        for code in relationships
        if float(target_weights.get(code, 0.0) or 0.0) > 1e-12
    )
    if invalid_substitute_targets:
        raise ValueError(
            "substitute funds must have zero strategic target weight: "
            + ", ".join(invalid_substitute_targets)
        )
    return relationships


def strategic_fund_codes(
    fund_codes: Sequence[str], substitute_for: Mapping[str, str] | None
) -> list[str]:
    relationships = normalize_substitute_for(fund_codes, substitute_for)
    return [code for code in fund_codes if code not in relationships]


def build_execution_covariance(
    nav_history: pd.DataFrame,
    fund_codes: Sequence[str],
    *,
    estimation_window: int,
    covariance_method: CovarianceMethod = "fixed_20",
) -> pd.DataFrame | None:
    codes = list(dict.fromkeys(fund_codes))
    available = [code for code in codes if code in nav_history.columns]
    if len(available) != len(codes):
        return None
    returns = (
        nav_history[available]
        .astype(float)
        .pct_change()
        .replace([np.inf, -np.inf], np.nan)
        .dropna(how="any")
        .tail(estimation_window)
    )
    if len(returns) < 3:
        return None
    covariance, _ = estimate_covariance(returns, covariance_method)
    return covariance.reindex(index=codes, columns=codes).fillna(0.0)


def _tracking_error(
    holdings: np.ndarray,
    targets: np.ndarray,
    covariance: np.ndarray,
    wealth_scale: float,
) -> float:
    deviation = (holdings - targets) / wealth_scale
    value = float(deviation @ covariance @ deviation)
    return float(np.sqrt(max(value, 0.0)))


def _fallback_result(
    *,
    fund_codes,
    fund_gaps,
    target_weights,
    buy_fees,
    max_cash_to_spend,
    investment_limits,
    timestamp,
    planned_purchase_days,
    method,
    status,
    message,
) -> BuyAllocationResult:
    allocations = allocate_capped_buy_amounts(
        fund_codes,
        fund_gaps,
        target_weights,
        buy_fees,
        max_cash_to_spend,
        investment_limits,
        timestamp,
        planned_purchase_days=planned_purchase_days,
    )
    unspent = max(0.0, float(max_cash_to_spend) - sum(allocations.values()))
    limits = get_monthly_investment_limits(
        fund_codes,
        investment_limits,
        timestamp,
        planned_purchase_days=planned_purchase_days,
    )
    unspent_reasons: dict[str, str] = {}
    for code in fund_codes:
        gap = max(0.0, float(fund_gaps.get(code, 0.0)))
        gross_gap = gap * (1.0 + max(0.0, float(buy_fees.get(code, 0.0))))
        allocated = float(allocations.get(code, 0.0))
        if gross_gap <= OPTIMIZER_EPSILON or allocated + OPTIMIZER_EPSILON >= gross_gap:
            continue
        limit = float(limits.get(code, np.inf))
        if limit <= OPTIMIZER_EPSILON:
            unspent_reasons[code] = "monthly_limit_zero"
        elif np.isfinite(limit) and allocated + OPTIMIZER_EPSILON >= limit:
            unspent_reasons[code] = "monthly_limit_reached"
        elif unspent <= OPTIMIZER_EPSILON:
            unspent_reasons[code] = "monthly_budget_exhausted"
        else:
            unspent_reasons[code] = "allocation_fallback"
    limited_reasons = {"monthly_limit_zero", "monthly_limit_reached"}
    if unspent <= OPTIMIZER_EPSILON:
        fallback_unspent_reason = None
    elif unspent_reasons and set(unspent_reasons.values()) <= limited_reasons:
        fallback_unspent_reason = "investment_limits_blocked"
    else:
        fallback_unspent_reason = "no_positive_target_capacity"
    return BuyAllocationResult(
        gross_allocations=allocations,
        diagnostics=BuyAllocationDiagnostics(
            method=method,
            status=status,
            fallback_used=method != "proportional_gap",
            message=message,
            tracking_error_before=None,
            tracking_error_after=None,
            binding_limits=(),
            substitute_purchases={},
            eligible_substitutes=(),
            unspent_budget=unspent,
            unspent_reason=fallback_unspent_reason,
            unspent_reasons=unspent_reasons,
        ),
    )


def allocate_buy_amounts(
    *,
    fund_codes: Sequence[str],
    post_sell_holdings: Mapping[str, float],
    target_holdings: Mapping[str, float],
    target_weights: Mapping[str, float],
    buy_fees: Mapping[str, float] | None,
    max_cash_to_spend: float,
    investment_limits: Mapping[str, object] | None,
    timestamp,
    allocation_method: AllocationMethod = "proportional_gap",
    covariance: pd.DataFrame | None = None,
    substitute_for: Mapping[str, str] | None = None,
    planned_purchase_days: int | None = None,
) -> BuyAllocationResult:
    codes = list(dict.fromkeys(fund_codes))
    relationships = validate_execution_metadata(
        fund_codes=codes,
        target_weights=target_weights,
        substitute_for=substitute_for,
        allocation_method=allocation_method,
    )
    gaps = {
        code: float(target_holdings.get(code, 0.0))
        - float(post_sell_holdings.get(code, 0.0))
        for code in codes
    }
    fees = {code: max(0.0, float((buy_fees or {}).get(code, 0.0))) for code in codes}

    if allocation_method == "proportional_gap":
        return _fallback_result(
            fund_codes=codes,
            fund_gaps=gaps,
            target_weights=target_weights,
            buy_fees=fees,
            max_cash_to_spend=max_cash_to_spend,
            investment_limits=investment_limits,
            timestamp=timestamp,
            planned_purchase_days=planned_purchase_days,
            method=allocation_method,
            status="ok",
            message=None,
        )

    if covariance is None:
        return _fallback_result(
            fund_codes=codes,
            fund_gaps=gaps,
            target_weights=target_weights,
            buy_fees=fees,
            max_cash_to_spend=max_cash_to_spend,
            investment_limits=investment_limits,
            timestamp=timestamp,
            planned_purchase_days=planned_purchase_days,
            method=allocation_method,
            status="fallback_missing_covariance",
            message="insufficient overlapping history for constrained tracking",
        )

    covariance = covariance.reindex(index=codes, columns=codes)
    if covariance.isna().any().any():
        return _fallback_result(
            fund_codes=codes,
            fund_gaps=gaps,
            target_weights=target_weights,
            buy_fees=fees,
            max_cash_to_spend=max_cash_to_spend,
            investment_limits=investment_limits,
            timestamp=timestamp,
            planned_purchase_days=planned_purchase_days,
            method=allocation_method,
            status="fallback_invalid_covariance",
            message="execution covariance does not cover the complete execution universe",
        )

    limits = get_monthly_investment_limits(
        codes,
        investment_limits,
        timestamp,
        planned_purchase_days=planned_purchase_days,
    )
    binding_primaries: set[str] = set()
    for primary in set(relationships.values()):
        if gaps[primary] <= OPTIMIZER_EPSILON:
            continue
        gross_gap = gaps[primary] * (1.0 + fees[primary])
        capacity = min(gross_gap, limits[primary])
        if capacity + OPTIMIZER_EPSILON < min(gross_gap, max_cash_to_spend):
            binding_primaries.add(primary)

    eligible_substitutes = tuple(
        code for code in codes if relationships.get(code) in binding_primaries
    )
    eligible = {
        code
        for code in codes
        if (
            code not in relationships
            and gaps[code] > OPTIMIZER_EPSILON
            and float(target_weights.get(code, 0.0)) > OPTIMIZER_EPSILON
        )
        or code in eligible_substitutes
    }
    if not eligible or max_cash_to_spend <= OPTIMIZER_EPSILON:
        return _fallback_result(
            fund_codes=codes,
            fund_gaps=gaps,
            target_weights=target_weights,
            buy_fees=fees,
            max_cash_to_spend=max_cash_to_spend,
            investment_limits=investment_limits,
            timestamp=timestamp,
            planned_purchase_days=planned_purchase_days,
            method=allocation_method,
            status="ok",
            message=None,
        )

    current = np.array([float(post_sell_holdings.get(code, 0.0)) for code in codes])
    targets = np.array([float(target_holdings.get(code, 0.0)) for code in codes])
    covariance_values = covariance.to_numpy(dtype=float)
    covariance_values = (covariance_values + covariance_values.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(covariance_values)
    covariance_values = (
        eigenvectors @ np.diag(np.maximum(eigenvalues, 0.0)) @ eigenvectors.T
    )
    covariance_scale = max(float(np.max(np.diag(covariance_values))), 1e-12)
    optimization_covariance = covariance_values / covariance_scale
    wealth_scale = max(float(targets.sum()), float(current.sum()), 1.0)
    aggregate_target_gap = max(0.0, float(targets.sum() - current.sum()))
    bounds = []
    for code in codes:
        if code not in eligible:
            bounds.append((0.0, 0.0))
            continue
        upper = min(float(limits[code]), float(max_cash_to_spend))
        if code not in relationships:
            upper = min(upper, max(0.0, gaps[code]) * (1.0 + fees[code]))
        bounds.append((0.0, upper))

    legacy = allocate_capped_buy_amounts(
        codes,
        gaps,
        target_weights,
        fees,
        max_cash_to_spend,
        investment_limits,
        timestamp,
        planned_purchase_days=planned_purchase_days,
    )
    initial = np.array([legacy.get(code, 0.0) for code in codes], dtype=float)
    fee_factors = np.array([1.0 + fees[code] for code in codes], dtype=float)
    # Positive per-fund gaps can coexist with an overweight sleeve.  In that
    # case the raw proportional baseline may exceed the aggregate target gap;
    # scale it down before using it as a lower bound so the solver remains
    # feasible and never crosses the total target.
    legacy_net_total = float(np.sum(initial / fee_factors))
    if (
        aggregate_target_gap > OPTIMIZER_EPSILON
        and legacy_net_total > aggregate_target_gap + OPTIMIZER_EPSILON
    ):
        initial *= aggregate_target_gap / legacy_net_total

    def post_trade(gross_buys: np.ndarray) -> np.ndarray:
        return current + gross_buys / fee_factors

    def objective(gross_buys: np.ndarray) -> float:
        holdings = post_trade(gross_buys)
        deviation = (holdings - targets) / wealth_scale
        return float(deviation @ optimization_covariance @ deviation)

    # The proportional allocator is a safe, cap-aware lower-bound plan.  A
    # tracking-error optimizer may prefer holding cash when a purchase has a
    # small marginal effect on the objective; that is not an acceptable reason
    # to undo an otherwise feasible DCA contribution.  Keep at least the
    # baseline amount while retaining the budget, aggregate-gap, per-fund gap,
    # and investment-limit upper bounds below.
    legacy_total = float(np.sum(initial))
    constraints = [
        {
            "type": "ineq",
            "fun": lambda buys: float(max_cash_to_spend - np.sum(buys)),
        },
        {
            "type": "ineq",
            "fun": lambda buys: float(
                aggregate_target_gap - np.sum(buys / fee_factors)
            ),
        },
    ]
    if legacy_total > OPTIMIZER_EPSILON:
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda buys: float(np.sum(buys) - legacy_total),
            }
        )
    result = minimize(
        objective,
        initial,
        method="SLSQP",
        bounds=bounds,
        constraints=constraints,
        options={"ftol": 1e-12, "maxiter": 500},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        return _fallback_result(
            fund_codes=codes,
            fund_gaps=gaps,
            target_weights=target_weights,
            buy_fees=fees,
            max_cash_to_spend=max_cash_to_spend,
            investment_limits=investment_limits,
            timestamp=timestamp,
            planned_purchase_days=planned_purchase_days,
            method=allocation_method,
            status="fallback_solver_failure",
            message=str(result.message),
        )

    optimized = {code: max(0.0, float(result.x[idx])) for idx, code in enumerate(codes)}
    post = post_trade(result.x)
    binding_limits = tuple(
        code
        for idx, code in enumerate(codes)
        if np.isfinite(limits[code])
        and optimized[code] > OPTIMIZER_EPSILON
        and abs(optimized[code] - limits[code]) <= 1e-6
    )
    substitute_purchases = {
        code: optimized[code]
        for code in eligible_substitutes
        if optimized[code] > OPTIMIZER_EPSILON
    }
    unspent = max(0.0, float(max_cash_to_spend) - sum(optimized.values()))
    if unspent <= OPTIMIZER_EPSILON:
        unspent_reason = None
    elif not eligible_substitutes and binding_primaries:
        unspent_reason = "no_eligible_substitute"
    elif aggregate_target_gap <= OPTIMIZER_EPSILON:
        unspent_reason = "aggregate_target_reached"
    else:
        unspent_reason = "optimizer_preferred_cash"

    unspent_reasons: dict[str, str] = {}
    for code in codes:
        gap = max(0.0, gaps[code])
        gross_gap = gap * (1.0 + fees[code])
        allocated = optimized[code]
        if gross_gap <= OPTIMIZER_EPSILON or allocated + OPTIMIZER_EPSILON >= gross_gap:
            continue
        limit = float(limits[code])
        if limit <= OPTIMIZER_EPSILON:
            unspent_reasons[code] = "monthly_limit_zero"
        elif np.isfinite(limit) and allocated + OPTIMIZER_EPSILON >= limit:
            unspent_reasons[code] = "monthly_limit_reached"
        elif unspent <= OPTIMIZER_EPSILON:
            unspent_reasons[code] = "monthly_budget_exhausted"
        elif unspent_reason == "no_eligible_substitute":
            unspent_reasons[code] = "no_eligible_substitute"
        elif unspent_reason == "aggregate_target_reached":
            unspent_reasons[code] = "aggregate_target_reached"
        elif unspent_reason == "optimizer_preferred_cash":
            unspent_reasons[code] = "optimizer_preferred_cash"
        else:
            unspent_reasons[code] = "allocation_priority"

    return BuyAllocationResult(
        gross_allocations=optimized,
        diagnostics=BuyAllocationDiagnostics(
            method=allocation_method,
            status="ok",
            fallback_used=False,
            message=None,
            tracking_error_before=_tracking_error(
                current, targets, covariance_values, wealth_scale
            ),
            tracking_error_after=_tracking_error(
                post, targets, covariance_values, wealth_scale
            ),
            binding_limits=binding_limits,
            substitute_purchases=substitute_purchases,
            eligible_substitutes=eligible_substitutes,
            unspent_budget=unspent,
            unspent_reason=unspent_reason,
            unspent_reasons=unspent_reasons,
        ),
    )
