import numpy as np

_UNSPENT_REASON_LABELS = {
    "monthly_limit_zero": "申购限额为0，本月无法买入",
    "monthly_limit_reached": "已达到本月申购限额",
    "monthly_budget_exhausted": "月度 DCA 预算已分配完",
    "investment_limits_blocked": "所有正向目标缺口均受申购限额约束",
    "purchase_amount_constraints": "按申购起点和金额步长调整，零头留在现金，下次投入时重新计算",
    "no_eligible_substitute": "主基金限购且没有可用替代基金",
    "aggregate_target_reached": "组合目标缺口已填满",
    "optimizer_preferred_cash": "跟踪优化器选择保留现金",
    "allocation_fallback": "执行优化器不可用，已按比例和限额分配",
    "allocation_priority": "预算已优先分配给其他低配基金",
    "no_positive_target_capacity": "没有可继续买入的正向目标缺口",
}


def _unspent_reason_label(reason: str | None) -> str | None:
    if not reason:
        return None
    return _UNSPENT_REASON_LABELS.get(reason, reason)


def build_fund_advice(
    execution,
    unique_fund_codes,
    fund_names,
    fund_monthly_limits,
    allocation_unspent_reasons,
    allocation_diagnostics,
):
    """Present the execution result without recalculating trades or targets."""
    fund_advice = []
    # 2. Determine Actions
    for code in unique_fund_codes:
        fund_execution = execution.funds[code]
        current_val = fund_execution.current_holding
        gap_val = fund_execution.gap
        ideal_target_val = fund_execution.target_holding
        buy_fee = fund_execution.buy_fee_rate
        gross_gap = max(0.0, gap_val) * (1 + buy_fee)
        monthly_buy_limit = fund_monthly_limits.get(code, float("inf"))
        allocation_state = fund_execution.allocation_state

        # Reset action and amount for each fund to prevent leakage
        # Using a more robust if-elif-else structure
        action = fund_execution.action
        amount = (
            fund_execution.gross_sell if action == "Sell" else fund_execution.gross_buy
        )
        reason = "持有"
        unspent_reason = allocation_unspent_reasons.get(code)
        gross_gap_remaining = max(0.0, gross_gap - fund_execution.gross_buy)
        if gap_val > 1e-9 and gross_gap_remaining > 1e-7 and not unspent_reason:
            if monthly_buy_limit <= 1e-9:
                unspent_reason = "monthly_limit_zero"
            elif (
                np.isfinite(monthly_buy_limit)
                and fund_execution.gross_buy + 1e-7 >= monthly_buy_limit
            ):
                unspent_reason = "monthly_limit_reached"
            elif allocation_diagnostics.get("unspent_budget", 0.0) <= 1e-7:
                unspent_reason = "monthly_budget_exhausted"
            elif allocation_diagnostics.get("unspent_reason"):
                unspent_reason = str(allocation_diagnostics.get("unspent_reason"))
            else:
                unspent_reason = "allocation_priority"
        unspent_reason_label = _unspent_reason_label(unspent_reason)

        if allocation_state == "REBALANCE":
            reason = f"新增资金后仍超配，卖出修复目标；预计费用 {fund_execution.gross_sell - fund_execution.net_sell_proceeds:.2f} 元，净款到账后再核验买入"
        elif allocation_state == "RESERVE_REPAIR":
            reason = f"卖出部分持仓补足现金储备，按配置费率估算费用 {fund_execution.gross_sell - fund_execution.net_sell_proceeds:.2f} 元；到账前不计入可用现金"
        elif allocation_state == "EXIT":
            if amount > 1e-9:
                reason = "资产已被显式标记为退出，建议卖出"
            else:
                reason = "资产已标记退出，但当前没有可卖持仓"
        elif allocation_state == "ACTIVE_SUBSTITUTE":
            reason = (
                "主基金申购额度成为约束，作为其明确指定的替代基金买入以降低目标跟踪误差"
            )
        elif gap_val < 0:
            if allocation_state == "NO_NEW_BUY":
                reason = "目标权重为数值零，暂停新增买入但不自动卖出"
            else:
                reason = "偏差在容忍范围内或交易条件不足；新增资金优先投向低配资产"
        elif gap_val > 0:
            limit_applied = bool(
                np.isfinite(monthly_buy_limit)
                and gross_gap > monthly_buy_limit + 1e-9
                and fund_execution.gross_buy + 1e-9 >= monthly_buy_limit
            )
            if fund_execution.gross_buy > 0:
                if limit_applied:
                    reason = "受申购限额约束，先买入可执行额度"
                else:
                    reason = f"按 DCA 预算向低配资产投入 (Gap: {gap_val:.1f})"
            else:
                action = "Hold"
                reason = unspent_reason_label or "当前没有可执行买入额度"

            # Preserve the useful action text while making a partial buy's
            # remaining gap explicit instead of silently implying that it
            # was merely being observed.
            if fund_execution.gross_buy > 0 and unspent_reason_label:
                reason = f"{reason}；未完全执行：{unspent_reason_label}"
        else:
            action = "Hold"
            reason = "仓位已达标"

        if gap_val <= 0:
            limit_applied = False

        # target_val is already calculated as the ideal target (current + gap)

        fund_advice.append(
            {
                "code": code,
                "name": fund_names.get(code, code),
                "current_holding": current_val,
                "target_holding": ideal_target_val,
                "executable_holding": fund_execution.executable_holding,
                "ideal_holding": ideal_target_val,
                "gap": gap_val,
                "action": action,
                "amount": amount,
                "monthly_buy_limit": (
                    monthly_buy_limit if np.isfinite(monthly_buy_limit) else None
                ),
                "limit_applied": limit_applied,
                "allocation_state": allocation_state,
                "execution_role": fund_execution.execution_role,
                "buy_source": fund_execution.buy_source,
                "buy_fee": fund_execution.gross_buy
                - fund_execution.gross_buy / (1 + fund_execution.buy_fee_rate),
                "sell_fee": fund_execution.gross_sell
                - fund_execution.net_sell_proceeds,
                "net_sale_proceeds": fund_execution.net_sell_proceeds,
                "unspent_reason": unspent_reason,
                "unspent_reason_label": unspent_reason_label,
                "reason": reason,
            }
        )

    return fund_advice
