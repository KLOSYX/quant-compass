"""Shared validation primitives for financial domain values."""

from __future__ import annotations

import math
from collections.abc import Mapping


def finite_number(value, name: str) -> float:
    """Parse a finite float and reject booleans and non-numeric values."""

    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def non_negative_number(value, name: str, *, epsilon: float = 1e-9) -> float:
    parsed = finite_number(value, name)
    if parsed < -epsilon:
        raise ValueError(f"{name} must be non-negative")
    return max(0.0, parsed)


def unit_interval_number(
    value,
    name: str,
    *,
    include_one: bool = True,
) -> float:
    parsed = finite_number(value, name)
    upper_invalid = parsed > 1.0 if include_one else parsed >= 1.0
    if parsed < 0.0 or upper_invalid:
        interval = "[0, 1]" if include_one else "[0, 1)"
        raise ValueError(f"{name} must be within {interval}")
    return parsed


def validate_finite_mapping(
    values: Mapping[str, object] | None,
    name: str,
    *,
    non_negative: bool = False,
) -> dict[str, float]:
    parser = non_negative_number if non_negative else finite_number
    return {
        str(key): parser(value, f"{name} {key}")
        for key, value in (values or {}).items()
    }
