"""Immutable domain contracts shared by data, strategy, and execution layers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal, Mapping

import numpy as np
import pandas as pd

from core.validation import finite_number, non_negative_number


ReturnQualityStatus = Literal["verified", "reconciled", "partial", "unsupported"]
AvailabilityQuality = Literal["observed", "inferred"]
DistributionPolicy = Literal["cash", "reinvest"]
CorporateActionKind = Literal["cash_distribution", "split"]
LimitBasis = Literal["gross_cash_out", "net_asset_add"]


def _canonicalize(value: Any) -> Any:
    if is_dataclass(value):
        return _canonicalize(asdict(value))
    if hasattr(value, "model_dump"):
        return _canonicalize(value.model_dump(mode="python"))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _canonicalize(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (set, frozenset)):
        return sorted((_canonicalize(item) for item in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, pd.Series):
        return {
            "index": [_canonicalize(item) for item in value.index],
            "values": [_canonicalize(item) for item in value.tolist()],
            "name": value.name,
        }
    if isinstance(value, pd.DataFrame):
        return {
            "index": [_canonicalize(item) for item in value.index],
            "columns": [str(item) for item in value.columns],
            "values": [
                [_canonicalize(item) for item in row] for row in value.to_numpy()
            ],
        }
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return _canonicalize(value.item())
    if isinstance(value, float):
        return finite_number(value, "canonical value")
    return value


def canonical_hash(value: Any) -> str:
    payload = json.dumps(
        _canonicalize(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CorporateAction:
    asset_code: str
    kind: CorporateActionKind
    record_date: pd.Timestamp | None = None
    ex_date: pd.Timestamp | None = None
    payment_date: pd.Timestamp | None = None
    confirmation_date: pd.Timestamp | None = None
    cash_per_share: float = 0.0
    split_ratio: float = 1.0
    source: str = ""
    observed_at: pd.Timestamp | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"cash_distribution", "split"}:
            raise ValueError(f"unsupported corporate action kind: {self.kind}")
        object.__setattr__(
            self,
            "cash_per_share",
            non_negative_number(self.cash_per_share, "cash_per_share"),
        )
        split_ratio = finite_number(self.split_ratio, "split_ratio")
        if split_ratio <= 0:
            raise ValueError("split_ratio must be positive")
        object.__setattr__(self, "split_ratio", split_ratio)
        for field_name in (
            "record_date",
            "ex_date",
            "payment_date",
            "confirmation_date",
            "observed_at",
        ):
            raw = getattr(self, field_name)
            if raw is not None:
                object.__setattr__(self, field_name, pd.Timestamp(raw))


def _validated_series(values: pd.Series, name: str) -> pd.Series:
    if not isinstance(values, pd.Series):
        raise ValueError(f"{name} must be a pandas Series")
    parsed = values.astype(float).replace([np.inf, -np.inf], np.nan)
    if parsed.empty or parsed.isna().any():
        raise ValueError(f"{name} must contain only finite observations")
    if (parsed <= 0).any():
        raise ValueError(f"{name} must be strictly positive")
    parsed.index = pd.to_datetime(parsed.index)
    if parsed.index.has_duplicates:
        raise ValueError(f"{name} index must not contain duplicates")
    return parsed.sort_index()


@dataclass(frozen=True)
class AssetPriceSeries:
    asset_code: str
    values: pd.Series
    source: str
    observed_at: pd.Timestamp
    availability_quality: AvailabilityQuality = "observed"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "values", _validated_series(self.values, "price series")
        )
        object.__setattr__(self, "observed_at", pd.Timestamp(self.observed_at))
        if self.availability_quality not in {"observed", "inferred"}:
            raise ValueError("invalid availability_quality")


@dataclass(frozen=True)
class AssetTotalReturnSeries:
    asset_code: str
    values: pd.Series
    source: str
    return_policy: str = "reinvested_total_return"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "values", _validated_series(self.values, "total return series")
        )


@dataclass(frozen=True)
class ReturnQuality:
    status: ReturnQualityStatus
    corporate_action_coverage_complete: bool
    reconciliation_error: float | None = None
    reconciliation_tolerance: float | None = None
    availability_quality: AvailabilityQuality = "inferred"
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in {"verified", "reconciled", "partial", "unsupported"}:
            raise ValueError("invalid return quality status")
        for name in ("reconciliation_error", "reconciliation_tolerance"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, non_negative_number(value, name))
        if self.availability_quality not in {"observed", "inferred"}:
            raise ValueError("invalid availability_quality")


@dataclass(frozen=True)
class MarketSnapshot:
    price_series: Mapping[str, AssetPriceSeries]
    total_return_series: Mapping[str, AssetTotalReturnSeries]
    corporate_actions: tuple[CorporateAction, ...]
    return_quality: Mapping[str, ReturnQuality]
    fetched_at: pd.Timestamp
    available_at: Mapping[str, pd.Timestamp]
    source: str
    data_hash: str = field(default="")

    def __post_init__(self) -> None:
        object.__setattr__(self, "fetched_at", pd.Timestamp(self.fetched_at))
        normalized_available = {
            str(code): pd.Timestamp(timestamp)
            for code, timestamp in self.available_at.items()
        }
        object.__setattr__(self, "available_at", normalized_available)
        codes = set(self.price_series)
        if codes != set(self.total_return_series) or codes != set(self.return_quality):
            raise ValueError(
                "market snapshot series and quality must cover identical assets"
            )
        if codes != set(normalized_available):
            raise ValueError("available_at must cover every market snapshot asset")
        if not self.data_hash:
            hash_input = {
                "prices": self.price_series,
                "total_returns": self.total_return_series,
                "actions": self.corporate_actions,
                "quality": self.return_quality,
                "fetched_at": self.fetched_at,
                "available_at": normalized_available,
                "source": self.source,
            }
            object.__setattr__(self, "data_hash", canonical_hash(hash_input))


@dataclass(frozen=True)
class FundDealingTimeline:
    asset_code: str
    valuation_date: pd.Timestamp
    available_at: pd.Timestamp
    decision_at: pd.Timestamp
    order_cutoff_at: pd.Timestamp
    dealing_date: pd.Timestamp
    confirmation_date: pd.Timestamp
    availability_quality: AvailabilityQuality = "inferred"

    def __post_init__(self) -> None:
        for name in (
            "valuation_date",
            "available_at",
            "decision_at",
            "order_cutoff_at",
            "dealing_date",
            "confirmation_date",
        ):
            object.__setattr__(self, name, pd.Timestamp(getattr(self, name)))
        if self.available_at > self.decision_at:
            raise ValueError("available_at must not be after decision_at")
        if self.decision_at >= self.dealing_date:
            raise ValueError("decision_at must be before dealing_date")
        if self.dealing_date > self.confirmation_date:
            raise ValueError("dealing_date must not be after confirmation_date")
        if self.availability_quality not in {"observed", "inferred"}:
            raise ValueError("invalid availability_quality")


@dataclass(frozen=True)
class ExecutionAmount:
    gross_cash_out: float
    net_asset_add: float
    buy_fee: float

    def __post_init__(self) -> None:
        gross = non_negative_number(self.gross_cash_out, "gross_cash_out")
        net = non_negative_number(self.net_asset_add, "net_asset_add")
        fee = non_negative_number(self.buy_fee, "buy_fee")
        tolerance = max(1e-8, gross * 1e-10)
        if abs(gross - net - fee) > tolerance:
            raise ValueError("gross_cash_out must equal net_asset_add plus buy_fee")
        object.__setattr__(self, "gross_cash_out", gross)
        object.__setattr__(self, "net_asset_add", net)
        object.__setattr__(self, "buy_fee", fee)


@dataclass(frozen=True)
class SubstitutionGroup:
    group_id: str
    primary_fund: str
    substitutes: tuple[str, ...]
    target_amount: float | None = None

    def __post_init__(self) -> None:
        if not self.group_id or not self.primary_fund:
            raise ValueError("substitution group id and primary fund are required")
        if not self.substitutes:
            raise ValueError("substitution group must contain a substitute")
        if self.primary_fund in self.substitutes:
            raise ValueError("primary fund cannot substitute for itself")
        if len(set(self.substitutes)) != len(self.substitutes):
            raise ValueError("substitution group contains duplicate substitutes")
        if self.target_amount is not None:
            object.__setattr__(
                self,
                "target_amount",
                non_negative_number(self.target_amount, "substitution target_amount"),
            )
