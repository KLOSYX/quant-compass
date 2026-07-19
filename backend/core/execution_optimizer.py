from dataclasses import asdict, dataclass
from typing import Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.frontier import CovarianceMethod, estimate_covariance
from core.limits import allocate_capped_buy_amounts, get_monthly_investment_limits


AllocationMethod = Literal["proportional_gap", "constrained_tracking"]
FundRole = Literal["strategic", "substitute"]

ALLOCATION_METHODS = frozenset({"proportional_gap", "constrained_tracking"})
FUND_ROLES = frozenset({"strategic", "substitute"})
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

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BuyAllocationResult:
    gross_allocations: Mapping[str, float]
    diagnostics: BuyAllocationDiagnostics


def normalize_fund_roles(
    fund_codes: Sequence[str],
    fund_roles: Mapping[str, str] | None,
) -> dict[str, FundRole]:
    codes = list(dict.fromkeys(fund_codes))
    code_set = set(codes)
    supplied = fund_roles or {}
    unknown = sorted(set(supplied) - code_set)
    if unknown:
        raise ValueError(
            "fund_roles contain assets outside the current analysis universe: "
            + ", ".join(unknown)
        )

    normalized: dict[str, FundRole] = {}
    for code in codes:
        role = str(supplied.get(code, "strategic")).strip()
        if role not in FUND_ROLES:
            raise ValueError(f"invalid fund role for {code}: {role!r}")
        normalized[code] = role  # type: ignore[assignment]
    return normalized


def normalize_substitution_groups(
    fund_codes: Sequence[str],
    substitution_groups: Mapping[str, str] | None,
) -> dict[str, str]:
    codes = list(dict.fromkeys(fund_codes))
    code_set = set(codes)
    supplied = substitution_groups or {}
    unknown = sorted(set(supplied) - code_set)
    if unknown:
        raise ValueError(
            "substitution_groups contain assets outside the current analysis universe: "
            + ", ".join(unknown)
        )
    return {
        code: str(supplied.get(code, "") or "").strip()
        for code in codes
    }


def normalize_proxy_penalties(
    fund_codes: Sequence[str],
    proxy_penalties: Mapping[str, float] | None,
) -> dict[str, float]:
    codes = list(dict.fromkeys(fund_codes))
    code_set = set(codes)
    supplied = proxy_penalties or {}
    unknown = sorted(set(supplied) - code_set)
    if unknown:
        raise ValueError(
            "proxy_penalties contain assets outside the current analysis universe: "
            + ", ".join(unknown)
        )
    normalized = {}
    for code in codes:
        value = float(supplied.get(code, 0.0) or 0.0)
        if value < 0:
            raise ValueError(f"proxy penalty for {code} must be non-negative")
        normalized[code] = value
    return normalized


def validate_execution_metadata(
    *,
    fund_codes: Sequence[str],
    target_weights: Mapping[str, float],
    fund_roles: Mapping[str, str] | None,
    substitution_groups: Mapping[str, str] | None,
    allocation_method: str,
) -> tuple[dict[str, FundRole], dict[str, str]]:
    if allocation_method not in ALLOCATION_METHODS:
        raise ValueError(
            f"execution_allocation_method must be one of {sorted(ALLOCATION_METHODS)}"
        )
    roles = normalize_fund_roles(fund_codes, fund_roles)
    groups = normalize_substitution_groups(fund_codes, substitution_groups)
    invalid_substitute_targets = sorted(
        code
        for code, role in roles.items()
        if role == "substitute" and float(target_weights.get(code, 0.0) or 0.0) > 1e-12
    )
    if invalid_substitute_targets:
        raise ValueError(
            "substitute funds must have zero strategic target weight: "
            + ", ".join(invalid_substitute_targets)
        )
    missing_groups = sorted(
        code for code, role in roles.items() if role == "substitute" and not groups[code]
    )
    if missing_groups:
        raise ValueError(
            "substitute funds require a substitution_group: "
            + ", ".join(missing_groups)
        )
    strategic_groups = {
        groups[code]
        for code, role in roles.items()
        if role == "strategic" and groups[code]
    }
    orphan_substitutes = sorted(
        code
        for code, role in roles.items()
        if role == "substitute" and groups[code] not in strategic_groups
    )
    if orphan_substitutes:
        raise ValueError(
            "substitute funds require a strategic fund in the same substitution_group: "
            + ", ".join(orphan_substitutes)
        )
    return roles, groups


def strategic_fund_codes(
    fund_codes: Sequence[str], fund_roles: Mapping[str, str] | None
) -> list[str]:
    roles = normalize_fund_roles(fund_codes, fund_roles)
    return [code for code in fund_codes if roles[code] == "strategic"]


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
            unspent_reason="no_positive_target_capacity" if unspent > 1e-9 else None,
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
    fund_roles: Mapping[str, str] | None = None,
    substitution_groups: Mapping[str, str] | None = None,
    proxy_penalties: Mapping[str, float] | None = None,
    planned_purchase_days: int | None = None,
) -> BuyAllocationResult:
    codes = list(dict.fromkeys(fund_codes))
    roles, groups = validate_execution_metadata(
        fund_codes=codes,
        target_weights=target_weights,
        fund_roles=fund_roles,
        substitution_groups=substitution_groups,
        allocation_method=allocation_method,
    )
    penalties = normalize_proxy_penalties(codes, proxy_penalties)
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
    binding_groups: set[str] = set()
    for group in {value for value in groups.values() if value}:
        strategic = [
            code
            for code in codes
            if roles[code] == "strategic"
            and groups[code] == group
            and gaps[code] > OPTIMIZER_EPSILON
        ]
        if not strategic:
            continue
        gross_gap = sum(gaps[code] * (1.0 + fees[code]) for code in strategic)
        capacity = sum(
            min(gaps[code] * (1.0 + fees[code]), limits[code])
            for code in strategic
        )
        if capacity + OPTIMIZER_EPSILON < min(gross_gap, max_cash_to_spend):
            binding_groups.add(group)

    eligible_substitutes = tuple(
        code
        for code in codes
        if roles[code] == "substitute" and groups[code] in binding_groups
    )
    eligible = {
        code
        for code in codes
        if (
            roles[code] == "strategic"
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
    covariance_values = eigenvectors @ np.diag(np.maximum(eigenvalues, 0.0)) @ eigenvectors.T
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
        if roles[code] == "strategic":
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

    def post_trade(gross_buys: np.ndarray) -> np.ndarray:
        return current + gross_buys / fee_factors

    def objective(gross_buys: np.ndarray) -> float:
        holdings = post_trade(gross_buys)
        deviation = (holdings - targets) / wealth_scale
        tracking_variance = float(deviation @ optimization_covariance @ deviation)
        proxy_cost = sum(
            penalties[code] * gross_buys[idx] / wealth_scale
            for idx, code in enumerate(codes)
            if roles[code] == "substitute"
        )
        return tracking_variance + proxy_cost

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

    optimized = {
        code: max(0.0, float(result.x[idx])) for idx, code in enumerate(codes)
    }
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
    elif not eligible_substitutes and binding_groups:
        unspent_reason = "no_eligible_substitute"
    elif aggregate_target_gap <= OPTIMIZER_EPSILON:
        unspent_reason = "aggregate_target_reached"
    else:
        unspent_reason = "optimizer_preferred_cash"

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
        ),
    )
