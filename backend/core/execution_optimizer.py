from dataclasses import asdict, dataclass, field
from typing import Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from core.frontier import CovarianceMethod, estimate_covariance
from core.limits import get_monthly_investment_limits
from core.validation import finite_number, non_negative_number, validate_finite_mapping


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


@dataclass(frozen=True)
class FeasibleExecutionEnvelope:
    safe_gross_allocations: Mapping[str, float]
    upper_gross_allocations: Mapping[str, float]
    aggregate_net_gap: float
    max_gross_cash_out: float
    group_net_caps: Mapping[str, float]
    group_members: Mapping[str, tuple[str, ...]]
    monthly_gross_limits: Mapping[str, float]
    net_gaps: Mapping[str, float]


@dataclass(frozen=True)
class ConstraintValidationResult:
    feasible: bool
    residuals: Mapping[str, float]


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


def _limit_config_dict(config) -> dict:
    if config is None:
        return {}
    if hasattr(config, "model_dump"):
        return config.model_dump()
    if hasattr(config, "dict"):
        return config.dict()
    return dict(config)


def _gross_limits(
    fund_codes,
    investment_limits,
    buy_fees,
    timestamp,
    planned_purchase_days,
) -> dict[str, float]:
    nominal_limits = get_monthly_investment_limits(
        fund_codes,
        investment_limits,
        timestamp,
        planned_purchase_days=planned_purchase_days,
    )
    result = {}
    for code in fund_codes:
        config = _limit_config_dict((investment_limits or {}).get(code))
        basis = config.get("limit_basis", "gross_cash_out")
        if basis not in {"gross_cash_out", "net_asset_add"}:
            raise ValueError(
                f"investment limit basis for {code} must be gross_cash_out or net_asset_add"
            )
        nominal = float(nominal_limits.get(code, np.inf))
        if np.isnan(nominal) or nominal < 0:
            raise ValueError(f"limit {code} must be a non-negative number")
        result[code] = (
            nominal * (1.0 + buy_fees[code]) if basis == "net_asset_add" else nominal
        )
    return result


def _water_fill(
    *,
    codes: Sequence[str],
    remaining_net_gaps: Mapping[str, float],
    net_capacities: Mapping[str, float],
    fees: Mapping[str, float],
    max_gross_cash: float,
    aggregate_net_cap: float,
) -> dict[str, float]:
    """Deterministically allocate gross cash while all hard caps remain explicit."""

    allocations = {code: 0.0 for code in codes}
    net_allocated = {code: 0.0 for code in codes}
    remaining_cash = max(0.0, float(max_gross_cash))
    remaining_aggregate = max(0.0, float(aggregate_net_cap))
    active = {
        code
        for code in codes
        if remaining_net_gaps.get(code, 0.0) > OPTIMIZER_EPSILON
        and net_capacities.get(code, 0.0) > OPTIMIZER_EPSILON
    }

    while (
        active
        and remaining_cash > OPTIMIZER_EPSILON
        and remaining_aggregate > OPTIMIZER_EPSILON
    ):
        net_demands = {
            code: min(
                max(0.0, remaining_net_gaps.get(code, 0.0) - net_allocated[code]),
                max(0.0, net_capacities.get(code, 0.0) - net_allocated[code]),
            )
            for code in active
        }
        active = {code for code in active if net_demands[code] > OPTIMIZER_EPSILON}
        if not active:
            break
        weighting_gaps = {
            code: max(0.0, remaining_net_gaps.get(code, 0.0) - net_allocated[code])
            for code in active
        }
        total_weighting_gap = sum(weighting_gaps.values())
        desired_net_total = min(total_weighting_gap, remaining_aggregate)
        desired_net = {
            code: desired_net_total * weighting_gaps[code] / total_weighting_gap
            for code in active
        }
        desired_gross = sum(desired_net[code] * (1.0 + fees[code]) for code in active)
        cash_scale = (
            min(1.0, remaining_cash / desired_gross) if desired_gross > 0 else 0.0
        )
        spent = 0.0
        added_net = 0.0
        saturated = set()
        for code in sorted(active):
            add_net = min(net_demands[code], desired_net[code] * cash_scale)
            if add_net <= OPTIMIZER_EPSILON:
                continue
            add_gross = add_net * (1.0 + fees[code])
            allocations[code] += add_gross
            net_allocated[code] += add_net
            spent += add_gross
            added_net += add_net
            if net_demands[code] - add_net <= OPTIMIZER_EPSILON:
                saturated.add(code)
        remaining_cash = max(0.0, remaining_cash - spent)
        remaining_aggregate = max(0.0, remaining_aggregate - added_net)
        active -= saturated
        if spent <= OPTIMIZER_EPSILON:
            break
    return allocations


def build_feasible_execution_envelope(
    *,
    fund_codes: Sequence[str],
    post_sell_holdings: Mapping[str, float],
    target_holdings: Mapping[str, float],
    target_weights: Mapping[str, float],
    buy_fees: Mapping[str, float] | None,
    max_cash_to_spend: float,
    investment_limits: Mapping[str, object] | None,
    timestamp,
    substitute_for: Mapping[str, str] | None = None,
    planned_purchase_days: int | None = None,
) -> FeasibleExecutionEnvelope:
    codes = list(dict.fromkeys(fund_codes))
    relationships = normalize_substitute_for(codes, substitute_for)
    current = validate_finite_mapping(
        {code: post_sell_holdings.get(code, 0.0) for code in codes},
        "post-sell holding",
        non_negative=True,
    )
    targets = validate_finite_mapping(
        {code: target_holdings.get(code, 0.0) for code in codes},
        "target holding",
        non_negative=True,
    )
    weights = validate_finite_mapping(
        {code: target_weights.get(code, 0.0) for code in codes},
        "target weight",
        non_negative=True,
    )
    fees = validate_finite_mapping(
        {code: (buy_fees or {}).get(code, 0.0) for code in codes},
        "buy fee",
        non_negative=True,
    )
    for code, fee in fees.items():
        if fee >= 1.0:
            raise ValueError(f"buy fee {code} must be less than 1")
    max_cash = non_negative_number(max_cash_to_spend, "max_cash_to_spend")
    gross_limits = _gross_limits(
        codes,
        investment_limits,
        fees,
        timestamp,
        planned_purchase_days,
    )
    aggregate_net_gap = max(0.0, sum(targets.values()) - sum(current.values()))
    strategic = [code for code in codes if code not in relationships]
    primary_gaps = {
        code: max(0.0, targets[code] - current[code])
        if weights[code] > OPTIMIZER_EPSILON
        else 0.0
        for code in strategic
    }
    primary_net_capacities = {
        code: min(primary_gaps[code], gross_limits[code] / (1.0 + fees[code]))
        for code in strategic
    }
    safe_primary = _water_fill(
        codes=strategic,
        remaining_net_gaps=primary_gaps,
        net_capacities=primary_net_capacities,
        fees=fees,
        max_gross_cash=max_cash,
        aggregate_net_cap=aggregate_net_gap,
    )
    # The deterministic baseline intentionally buys strategic funds only.  A
    # substitute is an explicitly qualified QP option and is not activated
    # when covariance evidence is unavailable or the solver falls back.
    safe = {code: safe_primary.get(code, 0.0) for code in codes}
    primary_net_added = {code: safe[code] / (1.0 + fees[code]) for code in strategic}
    remaining_aggregate = max(
        0.0,
        aggregate_net_gap - sum(primary_net_added.values()),
    )

    group_members: dict[str, tuple[str, ...]] = {}
    group_caps: dict[str, float] = {}
    substitutes_by_primary: dict[str, list[str]] = {}
    for substitute, primary in relationships.items():
        substitutes_by_primary.setdefault(primary, []).append(substitute)
    for primary in sorted(substitutes_by_primary):
        substitutes = tuple(sorted(substitutes_by_primary[primary]))
        group_members[primary] = substitutes
        main_gap = max(0.0, targets[primary] - current[primary])
        main_limit_capacity = min(
            main_gap,
            gross_limits[primary] / (1.0 + fees[primary]),
        )
        blocked_by_main_limit = max(0.0, main_gap - main_limit_capacity)
        existing_group_value = current[primary] + sum(
            current[code] for code in substitutes
        )
        group_remaining_after_main = max(
            0.0,
            targets[primary]
            - existing_group_value
            - primary_net_added.get(primary, 0.0),
        )
        cap = min(
            blocked_by_main_limit, group_remaining_after_main, remaining_aggregate
        )
        group_caps[primary] = cap

    upper = {code: 0.0 for code in codes}
    for code in strategic:
        upper[code] = min(
            gross_limits[code],
            primary_gaps[code] * (1.0 + fees[code]),
            max_cash,
        )
    for primary, substitutes in group_members.items():
        for code in substitutes:
            upper[code] = min(
                gross_limits[code],
                group_caps[primary] * (1.0 + fees[code]),
                max_cash,
            )

    envelope = FeasibleExecutionEnvelope(
        safe_gross_allocations=safe,
        upper_gross_allocations=upper,
        aggregate_net_gap=aggregate_net_gap,
        max_gross_cash_out=max_cash,
        group_net_caps=group_caps,
        group_members=group_members,
        monthly_gross_limits=gross_limits,
        net_gaps={code: max(0.0, targets[code] - current[code]) for code in codes},
    )
    validation = validate_execution_constraints(
        gross_allocations=safe,
        buy_fees=fees,
        envelope=envelope,
    )
    if not validation.feasible:
        raise RuntimeError(
            "safe execution envelope produced an infeasible baseline: "
            + str(validation.residuals)
        )
    return envelope


def validate_execution_constraints(
    *,
    gross_allocations: Mapping[str, float],
    buy_fees: Mapping[str, float],
    envelope: FeasibleExecutionEnvelope,
    tolerance: float = 1e-6,
) -> ConstraintValidationResult:
    allocations = {
        code: finite_number(gross_allocations.get(code, 0.0), f"allocation {code}")
        for code in envelope.upper_gross_allocations
    }
    fees = {
        code: finite_number(buy_fees.get(code, 0.0), f"buy fee {code}")
        for code in allocations
    }
    residuals: dict[str, float] = {}
    residuals["non_negative"] = max(
        [max(0.0, -amount) for amount in allocations.values()] or [0.0]
    )
    residuals["gross_budget"] = max(
        0.0, sum(allocations.values()) - envelope.max_gross_cash_out
    )
    net_total = sum(allocations[code] / (1.0 + fees[code]) for code in allocations)
    residuals["aggregate_net_gap"] = max(0.0, net_total - envelope.aggregate_net_gap)
    residuals["per_fund_upper"] = max(
        [
            max(0.0, allocations[code] - envelope.upper_gross_allocations[code])
            for code in allocations
        ]
        or [0.0]
    )
    residuals["monthly_limit"] = max(
        [
            max(0.0, allocations[code] - envelope.monthly_gross_limits[code])
            for code in allocations
        ]
        or [0.0]
    )
    group_violation = 0.0
    for primary, members in envelope.group_members.items():
        group_net = sum(allocations[code] / (1.0 + fees[code]) for code in members)
        group_violation = max(
            group_violation,
            max(0.0, group_net - envelope.group_net_caps.get(primary, 0.0)),
        )
    residuals["substitution_group"] = group_violation
    return ConstraintValidationResult(
        feasible=all(value <= tolerance for value in residuals.values()),
        residuals=residuals,
    )


def _fallback_result(
    *,
    fund_codes,
    envelope,
    buy_fees,
    method,
    status,
    message,
) -> BuyAllocationResult:
    allocations = dict(envelope.safe_gross_allocations)
    unspent = max(0.0, envelope.max_gross_cash_out - sum(allocations.values()))
    relationships = {
        substitute: primary
        for primary, substitutes in envelope.group_members.items()
        for substitute in substitutes
    }
    substitute_purchases = {
        code: allocations[code]
        for code in relationships
        if allocations.get(code, 0.0) > OPTIMIZER_EPSILON
    }
    if unspent <= OPTIMIZER_EPSILON:
        unspent_reason = None
    elif envelope.aggregate_net_gap <= OPTIMIZER_EPSILON:
        unspent_reason = "aggregate_target_reached"
    elif any(
        np.isfinite(limit) and allocations.get(code, 0.0) + OPTIMIZER_EPSILON >= limit
        for code, limit in envelope.monthly_gross_limits.items()
    ):
        unspent_reason = "investment_limits_blocked"
    else:
        unspent_reason = "no_positive_target_capacity"
    unspent_reasons = {}
    for code, gap in envelope.net_gaps.items():
        allocated_net = allocations.get(code, 0.0) / (1.0 + buy_fees[code])
        if gap <= OPTIMIZER_EPSILON or allocated_net + OPTIMIZER_EPSILON >= gap:
            continue
        limit = envelope.monthly_gross_limits[code]
        if limit <= OPTIMIZER_EPSILON:
            unspent_reasons[code] = "monthly_limit_zero"
        elif (
            np.isfinite(limit)
            and allocations.get(code, 0.0) + OPTIMIZER_EPSILON >= limit
        ):
            unspent_reasons[code] = "monthly_limit_reached"
        elif unspent <= OPTIMIZER_EPSILON:
            unspent_reasons[code] = "monthly_budget_exhausted"
        else:
            unspent_reasons[code] = "allocation_fallback"
    return BuyAllocationResult(
        gross_allocations=allocations,
        diagnostics=BuyAllocationDiagnostics(
            method=method,
            status=status,
            fallback_used=method != "proportional_gap",
            message=message,
            tracking_error_before=None,
            tracking_error_after=None,
            binding_limits=tuple(
                code
                for code, limit in envelope.monthly_gross_limits.items()
                if np.isfinite(limit)
                and allocations.get(code, 0.0) > OPTIMIZER_EPSILON
                and abs(allocations[code] - limit) <= 1e-6
            ),
            substitute_purchases=substitute_purchases,
            eligible_substitutes=tuple(sorted(relationships)),
            unspent_budget=unspent,
            unspent_reason=unspent_reason,
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
    fees = validate_finite_mapping(
        {code: (buy_fees or {}).get(code, 0.0) for code in codes},
        "buy fee",
        non_negative=True,
    )
    envelope = build_feasible_execution_envelope(
        fund_codes=codes,
        post_sell_holdings=post_sell_holdings,
        target_holdings=target_holdings,
        target_weights=target_weights,
        buy_fees=fees,
        max_cash_to_spend=max_cash_to_spend,
        investment_limits=investment_limits,
        timestamp=timestamp,
        substitute_for=relationships,
        planned_purchase_days=planned_purchase_days,
    )
    if allocation_method == "proportional_gap":
        return _fallback_result(
            fund_codes=codes,
            envelope=envelope,
            buy_fees=fees,
            method=allocation_method,
            status="ok",
            message=None,
        )
    if covariance is None:
        return _fallback_result(
            fund_codes=codes,
            envelope=envelope,
            buy_fees=fees,
            method=allocation_method,
            status="fallback_missing_covariance",
            message="insufficient overlapping history for constrained tracking",
        )
    covariance = covariance.reindex(index=codes, columns=codes)
    if covariance.isna().any().any() or not np.isfinite(covariance.to_numpy()).all():
        return _fallback_result(
            fund_codes=codes,
            envelope=envelope,
            buy_fees=fees,
            method=allocation_method,
            status="fallback_invalid_covariance",
            message="execution covariance does not cover the complete execution universe",
        )

    current = np.array(
        [
            non_negative_number(post_sell_holdings.get(code, 0.0), f"holding {code}")
            for code in codes
        ]
    )
    targets = np.array(
        [
            non_negative_number(target_holdings.get(code, 0.0), f"target {code}")
            for code in codes
        ]
    )
    fee_factors = np.array([1.0 + fees[code] for code in codes])
    covariance_values = covariance.to_numpy(dtype=float)
    covariance_values = (covariance_values + covariance_values.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(covariance_values)
    covariance_values = (
        eigenvectors @ np.diag(np.maximum(eigenvalues, 0.0)) @ eigenvectors.T
    )
    covariance_scale = max(float(np.max(np.diag(covariance_values))), 1e-12)
    optimization_covariance = covariance_values / covariance_scale
    wealth_scale = max(float(targets.sum()), float(current.sum()), 1.0)

    def post_trade(gross_buys: np.ndarray) -> np.ndarray:
        return current + gross_buys / fee_factors

    def objective(gross_buys: np.ndarray) -> float:
        deviation = (post_trade(gross_buys) - targets) / wealth_scale
        return float(deviation @ optimization_covariance @ deviation)

    initial = np.array(
        [envelope.safe_gross_allocations.get(code, 0.0) for code in codes], dtype=float
    )
    bounds = [(0.0, envelope.upper_gross_allocations[code]) for code in codes]
    constraints = [
        {
            "type": "ineq",
            "fun": lambda buys: float(envelope.max_gross_cash_out - np.sum(buys)),
        },
        {
            "type": "ineq",
            "fun": lambda buys: float(
                envelope.aggregate_net_gap - np.sum(buys / fee_factors)
            ),
        },
    ]
    safe_total = float(np.sum(initial))
    if safe_total > OPTIMIZER_EPSILON:
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda buys: float(np.sum(buys) - safe_total),
            }
        )
    for primary, members in envelope.group_members.items():
        indices = tuple(codes.index(code) for code in members)
        cap = envelope.group_net_caps.get(primary, 0.0)
        constraints.append(
            {
                "type": "ineq",
                "fun": lambda buys, member_indices=indices, group_cap=cap: float(
                    group_cap
                    - sum(buys[index] / fee_factors[index] for index in member_indices)
                ),
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
            envelope=envelope,
            buy_fees=fees,
            method=allocation_method,
            status="fallback_solver_failure",
            message=str(result.message),
        )
    optimized = {code: max(0.0, float(result.x[idx])) for idx, code in enumerate(codes)}
    validation = validate_execution_constraints(
        gross_allocations=optimized,
        buy_fees=fees,
        envelope=envelope,
    )
    if not validation.feasible:
        return _fallback_result(
            fund_codes=codes,
            envelope=envelope,
            buy_fees=fees,
            method=allocation_method,
            status="fallback_constraint_residual",
            message=str(validation.residuals),
        )
    post = post_trade(result.x)
    unspent = max(0.0, envelope.max_gross_cash_out - sum(optimized.values()))
    substitute_purchases = {
        code: optimized[code]
        for code in relationships
        if optimized[code] > OPTIMIZER_EPSILON
    }
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
            binding_limits=tuple(
                code
                for code in codes
                if np.isfinite(envelope.monthly_gross_limits[code])
                and optimized[code] > OPTIMIZER_EPSILON
                and abs(optimized[code] - envelope.monthly_gross_limits[code]) <= 1e-6
            ),
            substitute_purchases=substitute_purchases,
            eligible_substitutes=tuple(sorted(relationships)),
            unspent_budget=unspent,
            unspent_reason=(
                None if unspent <= OPTIMIZER_EPSILON else "optimizer_preferred_cash"
            ),
            unspent_reasons={},
        ),
    )
