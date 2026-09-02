"""Event-driven account unit ledger used by portfolio simulations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import pandas as pd

from core.domain import CorporateAction, DistributionPolicy
from core.validation import finite_number, non_negative_number


@dataclass
class AccountUnitLedger:
    shares: dict[str, float]
    cash: float
    total_units: float
    receivables: dict[str, float] = field(default_factory=dict)
    unit_nav_history: dict[pd.Timestamp, float] = field(default_factory=dict)
    external_cash_flows: list[tuple[pd.Timestamp, float]] = field(default_factory=list)

    @classmethod
    def initialize(
        cls,
        shares: Mapping[str, float],
        cash: float,
        prices: Mapping[str, float],
    ) -> "AccountUnitLedger":
        normalized_shares = {
            str(code): non_negative_number(value, f"shares {code}")
            for code, value in shares.items()
        }
        normalized_cash = non_negative_number(cash, "cash")
        wealth = (
            sum(
                normalized_shares[code] * finite_number(prices[code], f"price {code}")
                for code in normalized_shares
            )
            + normalized_cash
        )
        return cls(
            shares=normalized_shares,
            cash=normalized_cash,
            total_units=wealth,
        )

    def wealth(self, prices: Mapping[str, float]) -> float:
        missing_prices = sorted(
            code
            for code, shares in self.shares.items()
            if shares > 1e-12 and code not in prices
        )
        if missing_prices:
            raise ValueError(
                "prices do not cover held assets: " + ", ".join(missing_prices)
            )
        return (
            sum(
                self.shares.get(code, 0.0) * finite_number(price, f"price {code}")
                for code, price in prices.items()
            )
            + self.cash
            + sum(self.receivables.values())
        )

    def unit_nav(self, prices: Mapping[str, float]) -> float:
        return self.wealth(prices) / self.total_units if self.total_units > 0 else 1.0

    def contribute(
        self, timestamp, amount: float, prices: Mapping[str, float]
    ) -> float:
        contribution = non_negative_number(amount, "external contribution")
        nav_before = self.unit_nav(prices)
        if contribution > 0:
            self.total_units += contribution / nav_before
            self.cash += contribution
            self.external_cash_flows.append((pd.Timestamp(timestamp), -contribution))
        return nav_before

    def withdraw(self, timestamp, amount: float, prices: Mapping[str, float]) -> float:
        withdrawal = non_negative_number(amount, "external withdrawal")
        if withdrawal > self.cash + 1e-9:
            raise ValueError("external withdrawal exceeds available cash")
        nav_before = self.unit_nav(prices)
        units_to_cancel = withdrawal / nav_before if nav_before > 0 else 0.0
        if units_to_cancel > self.total_units + 1e-9:
            raise ValueError("external withdrawal exceeds account units")
        self.cash -= withdrawal
        self.total_units -= units_to_cancel
        self.external_cash_flows.append((pd.Timestamp(timestamp), withdrawal))
        return nav_before

    def accrue_distribution(
        self,
        action: CorporateAction,
        *,
        eligible_shares: float | None = None,
    ) -> float:
        if action.kind != "cash_distribution":
            raise ValueError("only cash distributions can create receivables")
        shares = (
            self.shares.get(action.asset_code, 0.0)
            if eligible_shares is None
            else non_negative_number(eligible_shares, "eligible shares")
        )
        amount = shares * action.cash_per_share
        self.receivables[action.asset_code] = (
            self.receivables.get(action.asset_code, 0.0) + amount
        )
        return amount

    def settle_distribution(
        self,
        action: CorporateAction,
        *,
        policy: DistributionPolicy,
        confirmation_price: float | None = None,
    ) -> float:
        amount = self.receivables.pop(action.asset_code, 0.0)
        if policy == "cash":
            self.cash += amount
        elif policy == "reinvest":
            price = finite_number(confirmation_price, "confirmation price")
            if price <= 0:
                raise ValueError("confirmation price must be positive")
            self.shares[action.asset_code] = (
                self.shares.get(action.asset_code, 0.0) + amount / price
            )
        else:
            raise ValueError("distribution policy must be cash or reinvest")
        return amount

    def apply_split(self, action: CorporateAction) -> None:
        if action.kind != "split":
            raise ValueError("only split actions can adjust shares")
        self.shares[action.asset_code] = (
            self.shares.get(action.asset_code, 0.0) * action.split_ratio
        )

    def charge_fee(self, amount: float) -> None:
        fee = non_negative_number(amount, "fee")
        if fee > self.cash + 1e-9:
            raise ValueError("fee exceeds available cash")
        self.cash -= fee

    def record(self, timestamp, prices: Mapping[str, float]) -> float:
        nav = self.unit_nav(prices)
        self.unit_nav_history[pd.Timestamp(timestamp)] = nav
        return nav
