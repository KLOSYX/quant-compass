"""Independent arithmetic for EXPERT_REVIEW_RESPONSE.md; synthetic scenarios only.

Run from the repository root: python analysis/expert_review_examples.py
Uses no market data or production planner. Amounts and fee schedules below are
review assumptions, not executable quotes or approved household preferences.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from math import ceil, isclose, sqrt


CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def check_wealth(
    opening: Decimal,
    contribution: Decimal,
    assets: list[Decimal],
    cash: Decimal,
    fees: Decimal = Decimal(0),
    receivable: Decimal = Decimal(0),
) -> None:
    assert opening + contribution == sum(assets) + cash + receivable + fees


def buy_only_projection(
    holdings: list[float], targets: list[float], budget: float
) -> list[float]:
    """Solve the example's fee-free squared-distance problem by water filling.

    KKT gives b_i = max(target_i - holding_i - level, 0). No sale,
    no overshoot, and no fee are assumed only in example A.
    """
    gaps = [max(0.0, target - holding) for target, holding in zip(targets, holdings)]
    spend = min(budget, sum(gaps))
    if spend == sum(gaps):
        return gaps
    lower, upper = 0.0, max(gaps)
    for _ in range(100):
        level = (lower + upper) / 2
        if sum(max(gap - level, 0.0) for gap in gaps) > spend:
            lower = level
        else:
            upper = level
    return [round(max(gap - upper, 0.0), 2) for gap in gaps]


def example_a() -> list[dict]:
    holdings = [30000.0, 20000.0, 10000.0]
    policies = {
        "more_safe_assets": ([0.3, 0.3, 0.4], [0, 0, 15000]),
        "middle_example": ([0.4, 0.4, 0.2], [0, 10000, 5000]),
        "more_equity": ([0.6, 0.25, 0.15], [14375, 0, 625]),
    }
    results = []
    for name, (weights, expected_buys) in policies.items():
        targets = [75000 * weight for weight in weights]
        buys = buy_only_projection(holdings, targets, 15000)
        assert buys == expected_buys
        final_assets = [holding + buy for holding, buy in zip(holdings, buys)]
        cash = 20000 - sum(buys)
        assert sum(final_assets) + cash == 80000
        assert cash == 5000
        deficits = [target - asset for target, asset in zip(targets, final_assets)]
        active = [gap for gap, buy in zip(deficits, buys) if buy > 0]
        assert all(isclose(gap, active[0], abs_tol=1e-7) for gap in active)
        assert all(
            gap <= active[0] + 1e-7 for gap, buy in zip(deficits, buys) if buy == 0
        )
        results.append(
            {
                "scenario": name,
                "strategic_weights": weights,
                "targets": targets,
                "buys": buys,
                "assets_after": final_assets,
                "cash_after": cash,
                "account_weights_pct": [
                    value / 80000 * 100 for value in [*final_assets, cash]
                ],
            }
        )
    return results


def example_b() -> dict:
    volatility, months, fraction = 0.04, 36, 0.5
    variance = volatility**2
    return {
        "monthly_mean_standard_error_iid": volatility / sqrt(months),
        "raw_half_kelly_at_monthly_means_0_003_006": [
            fraction * mean / variance for mean in (0, 0.003, 0.006)
        ],
        "ratio_with_0_001_benchmark_subtracted": fraction * (0.003 - 0.001) / variance,
        "expected_95pct_tail_observations_in_36_months": months * 0.05,
    }


def example_c() -> dict:
    # The two permitted dates and all fees are supplied synthetic inputs.
    dates = [date(2026, 9, 21), date(2026, 9, 22)]
    gross_per_order = Decimal(3030)
    net_per_order = money(gross_per_order / Decimal("1.01"))
    fee_per_order = gross_per_order - net_per_order
    assert net_per_order == 3000 and fee_per_order == 30
    gross_buy = gross_per_order * 2 + Decimal(5000)
    fees = fee_per_order * 2
    assets = [Decimal(30000), Decimal(26000), Decimal(15000)]
    cash = Decimal(20000) - gross_buy
    check_wealth(Decimal(70000), Decimal(10000), assets, cash, fees)
    assert cash == 8940 and gross_buy == 11060
    total = sum(assets) + cash
    shocks = [Decimal("0.30"), Decimal("0.08"), Decimal("0.01")]
    actual_loss = sum(value * shock for value, shock in zip(assets, shocks))
    ideal_assets = [Decimal(22500), Decimal(37500), Decimal(15000)]
    ideal_loss = sum(value * shock for value, shock in zip(ideal_assets, shocks))
    assert actual_loss == 11230 and ideal_loss == 9900
    return {
        "permitted_dates_assumed": [day.isoformat() for day in dates],
        "gross_buy": gross_buy,
        "fees": fees,
        "assets_after": assets,
        "cash_after": cash,
        "cash_reserved": 5000,
        "cash_waiting": cash - Decimal(5000),
        "existing_cash_used": gross_buy - Decimal(10000),
        "wealth_after": total,
        "account_weights_pct": [value / total * 100 for value in [*assets, cash]],
        "stress_loss": actual_loss,
        "stress_loss_pct": actual_loss / total * 100,
        "strategic_stress_loss_pct": ideal_loss / Decimal(80000) * 100,
    }


def example_d() -> dict:
    capital, stocks, upper = Decimal(105000), Decimal(65000), Decimal("0.55")
    required_sale = stocks - upper * capital
    assert required_sale == 7250
    months_to_band = ceil((stocks / upper - capital) / Decimal(5000))
    months_to_target = ceil((stocks / Decimal("0.5") - capital) / Decimal(5000))
    assert (months_to_band, months_to_target) == (3, 5)
    check_wealth(
        Decimal(130000),
        Decimal(5000),
        [stocks - required_sale, Decimal(35000 + 5000) + required_sale],
        Decimal(30000),
    )
    high_fee_sale = (required_sale / (1 - upper * Decimal("0.02"))).quantize(
        CENT, rounding=ROUND_CEILING
    )
    high_fee = money(high_fee_sale * Decimal("0.02"))
    assert high_fee_sale == Decimal("7330.64") and high_fee == Decimal("146.61")
    assert (stocks - high_fee_sale) / (capital - high_fee) <= upper
    assert high_fee > Decimal(50)  # The example's owner-approved cost ceiling.
    # Rejected fee-heavy sale: only the new contribution is invested.
    check_wealth(
        Decimal(130000), Decimal(5000), [stocks, Decimal(40000)], Decimal(30000)
    )
    # Slow settlement: the proposed sale is assumed confirmed, but still pending.
    check_wealth(
        Decimal(130000),
        Decimal(5000),
        [stocks - required_sale, Decimal(40000)],
        Decimal(30000),
        receivable=required_sale,
    )
    return {
        "months_to_band_no_market_movement": months_to_band,
        "months_to_exact_target": months_to_target,
        "zero_fee_gross_sale": required_sale,
        "zero_fee_total_bond_buy": Decimal(5000) + required_sale,
        "high_fee_hypothetical_gross_sale": high_fee_sale,
        "high_fee_hypothetical_fee": high_fee,
        "high_fee_recommended_sale": 0,
        "high_fee_recommended_bond_buy": 5000,
        "slow_settlement_pending": required_sale,
        "cash_after_all_three_cases": 30000,
        "sale_if_extra_10000_cash_is_explicitly_reinvestable": stocks
        - upper * Decimal(115000),
    }


def example_e() -> dict:
    budget = Decimal(205)
    gross_orders = [Decimal(100), Decimal(100)]
    assert all(Decimal(10) <= order <= Decimal(100) for order in gross_orders)
    remainder = budget - sum(gross_orders)
    assert remainder == 5
    one_pct_net = [money(order / Decimal("1.01")) for order in gross_orders]
    one_pct_fee = sum(gross_orders) - sum(one_pct_net)
    assert sum(one_pct_net) == Decimal("198.02")
    assert one_pct_fee == Decimal("1.98")
    check_wealth(Decimal(0), budget, [sum(one_pct_net)], remainder, one_pct_fee)
    net_basis_orders = [Decimal(100), Decimal(100)]
    net_basis_fees = [money(order * Decimal("0.01")) for order in net_basis_orders]
    net_basis_gross = [
        order + fee for order, fee in zip(net_basis_orders, net_basis_fees)
    ]
    net_basis_cash = budget - sum(net_basis_gross)
    assert net_basis_gross == [Decimal(101), Decimal(101)] and net_basis_cash == 3
    check_wealth(
        Decimal(0),
        budget,
        [sum(net_basis_orders)],
        net_basis_cash,
        sum(net_basis_fees),
    )
    return {
        "zero_fee_gross_orders": gross_orders,
        "zero_fee_cash_waiting": remainder,
        "one_pct_gross_basis_net_add": sum(one_pct_net),
        "one_pct_gross_basis_fees": one_pct_fee,
        "one_pct_net_basis_gross_orders": net_basis_gross,
        "one_pct_net_basis_cash_waiting": net_basis_cash,
    }


def example_f() -> dict:
    years, hours_per_year, hourly_value = 3, 6, 100
    cost_error_per_year = 100
    annual_hurdle = hours_per_year * hourly_value + cost_error_per_year
    assert annual_hurdle == 700
    return {
        "illustrative_36_month_wealth_hurdle_cny": years * annual_hurdle,
        "annual_cost_hurdle_cny": annual_hurdle,
        "indicative_bps_at_200000_reference_capital": annual_hurdle / 200000 * 10000,
        "not_a_market_performance_result": True,
    }


def main() -> None:
    results = {
        "scope": "synthetic_review_arithmetic_only",
        "A": example_a(),
        "B": example_b(),
        "C": example_c(),
        "D": example_d(),
        "E": example_e(),
        "F": example_f(),
    }
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
