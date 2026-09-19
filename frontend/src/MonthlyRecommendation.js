import React from 'react';
import { Download, TrendingUp } from 'lucide-react';
import { useLanguage } from './LanguageContext';
import { formatPercentValue, getAllocationSignalLabel, getAdviceActionMeta } from './portfolioViewUtils';

export default function MonthlyRecommendation({ recommendationResult, onExport }) {
    const { t } = useLanguage();
    if (!recommendationResult) return null;
    return (
        <section className="workspace-section recommendation-section">
    <div className="recommendation-card">
        <div className="card-header">
            <h3 className="card-title recommendation-title"><TrendingUp size={24} /> {t('recommend_title')}</h3>
            <button className="text-link-btn" onClick={onExport} aria-label={t('export_report')}>
                <Download size={16} /> {t('export_report')}
            </button>
        </div>

        <div className="recommendation-header">
            <div className="recommendation-stat">
                <div className="recommendation-stat-label">{t('market_signal')}</div>
                <div className="recommendation-stat-value" style={{ color: recommendationResult.market_signal === 'undervalued' ? '#34D399' : recommendationResult.market_signal === 'overvalued' ? '#F87171' : '#FBBF24' }}>
                    {recommendationResult.market_signal === 'undervalued' ? t('signal_under') : recommendationResult.market_signal === 'overvalued' ? t('signal_over') : t('signal_neutral')}
                </div>
                {recommendationResult.allocation_signal && (
                    <div className="text-xs text-slate-400 mt-1">
                        {t('allocation_signal')}: {getAllocationSignalLabel(recommendationResult.allocation_signal, t)}
                    </div>
                )}
            </div>
            <div className="recommendation-stat">
                <div className="recommendation-stat-label">{t('suggested_target')}</div>
                <div className="recommendation-stat-value">{((recommendationResult.target_fund_ratio ?? recommendationResult.target_equity_ratio) * 100).toFixed(0)}%</div>
            </div>
            <div className="recommendation-stat">
                <div className="recommendation-stat-label">{t('target_equity_exposure')}</div>
                <div className="recommendation-stat-value">{((recommendationResult.target_equity_exposure ?? 0) * 100).toFixed(0)}%</div>
            </div>
            <div className="recommendation-stat">
                <div className="recommendation-stat-label">{t('target_risk_exposure')}</div>
                <div className="recommendation-stat-value">{((recommendationResult.target_risk_asset_exposure ?? 0) * 100).toFixed(0)}%</div>
            </div>
            <div className="recommendation-stat">
                <div className="recommendation-stat-label">{t('suggested_buy_total')}</div>
                <div className="recommendation-stat-value text-white">¥{recommendationResult.recommended_monthly_investment.toFixed(2)}</div>
            </div>
            <div className="recommendation-stat">
                <div className="recommendation-stat-label">{t('monthly_budget_label')}</div>
                <div className="recommendation-stat-value text-slate-400">¥{recommendationResult.monthly_budget}</div>
            </div>
        </div>

        <div className={`recommendation-diagnostic ${recommendationResult.decision_readiness !== 'manual_review_required' || !recommendationResult.target_reached ? 'diagnostic-warning' : ''}`}>
            <div className="diagnostic-title">{t('decision_readiness')}</div>
            <div className="diagnostic-copy">
                {t(`decision_${recommendationResult.decision_readiness || 'research_only'}`)}
            </div>
            <div className="diagnostic-copy">
                {t('base_non_riskfree_ratio')}: {((recommendationResult.base_non_riskfree_fund_ratio || 0) * 100).toFixed(1)}%
                {' · '}{t('final_non_riskfree_ratio')}: {((recommendationResult.final_non_riskfree_fund_ratio || 0) * 100).toFixed(1)}%
            </div>
            <div className="diagnostic-copy">
                {t('safe_sleeve_ratio')}: {((recommendationResult.base_safe_sleeve_ratio || 0) * 100).toFixed(1)}%
                {' · '}{t('residual_cash_ratio')}: {((recommendationResult.residual_cash_ratio || 0) * 100).toFixed(1)}%
                {' · '}{t('actual_risk_ratio')}: {((recommendationResult.actual_risk_ratio || 0) * 100).toFixed(1)}%
            </div>
            {!recommendationResult.target_reached && (
                <div className="diagnostic-copy">
                    {t('target_not_reached')} · {t('cash_reserve_shortfall')}: ¥{Number(recommendationResult.cash_reserve_shortfall || 0).toFixed(2)}
                </div>
            )}
        </div>

        {(recommendationResult.optimizer_info?.cvar_preference_exceeded || recommendationResult.optimizer_info?.drawdown_preference_exceeded) && (
            <div className="recommendation-diagnostic diagnostic-warning">
                <div className="diagnostic-copy">{t('risk_preference_exceeded_warning')}</div>
            </div>
        )}
        {recommendationResult.optimizer_info?.cvar_confidence_status && (
            <div className={`recommendation-diagnostic ${recommendationResult.optimizer_info.cvar_warning_only ? 'diagnostic-warning' : ''}`}>
                <div className="diagnostic-title">{t('risk_diagnostics')}</div>
                <div className="diagnostic-copy">
                    {t('risk_data_source')}: {t(`risk_source_${recommendationResult.optimizer_info.cvar_data_source}`)}
                    {' · '}{t('risk_horizon')}: {recommendationResult.optimizer_info.risk_horizon_days} {t('trading_days')}
                    {' · '}{t('risk_observations')}: {recommendationResult.optimizer_info.cvar_return_observations}
                    {' / '}{t('risk_effective_observations')}: {recommendationResult.optimizer_info.cvar_effective_return_observations}
                    {' · '}{t('risk_tail_samples')}: {recommendationResult.optimizer_info.cvar_effective_tail_count}
                </div>
                {recommendationResult.optimizer_info.cvar_warning_only && (
                    <div className="diagnostic-copy">{t('cvar_low_confidence_warning')}</div>
                )}
            </div>
        )}
        {recommendationResult.rebalancing && (
            <div className="recommendation-diagnostic">
                <div className="diagnostic-title">{t('rebalance_status')}</div>
                <div className="diagnostic-copy">{t(`rebalance_${recommendationResult.rebalancing.status}`)}</div>
                {Object.entries(recommendationResult.rebalancing.conditional_buys_after_settlement || {}).map(([code, amount]) => (
                    <div className="diagnostic-copy" key={code}>{recommendationResult.fund_names?.[code] || code}: ¥{Number(amount).toFixed(2)} · {t('conditional_buy')}</div>
                ))}
            </div>
        )}
        {recommendationResult.projected_account_risk && (
            <div className="recommendation-diagnostic">
                <div className="diagnostic-title">{t('projected_account_risk')}</div>
                <div className="diagnostic-copy">{t('projected_account_risk_help')}</div>
                <div className="diagnostic-copy">CVaR: {formatPercentValue(recommendationResult.projected_account_risk.cvar_loss)} · {t('max_drawdown')}: {formatPercentValue(recommendationResult.projected_account_risk.max_drawdown)}</div>
                {recommendationResult.projected_account_risk.status !== 'adequate' && <div className="diagnostic-copy">{t('cvar_low_confidence_warning')}</div>}
            </div>
        )}
        {recommendationResult.execution_allocation && (
            <div className={`recommendation-diagnostic ${recommendationResult.execution_allocation.fallback_used ? 'diagnostic-warning' : ''}`}>
                <div className="diagnostic-title">{t('execution_allocation_diagnostics')}</div>
                <div className="diagnostic-copy">
                    {t('purchase_schedule_conditional').replace('{days}', recommendationResult.execution_allocation.planned_purchase_days ?? 21).replace('{calendarDays}', recommendationResult.execution_allocation.planning_period_days ?? 30)} {recommendationResult.execution_allocation.purchase_dates?.join(', ')}
                    <br />
                    {t('execution_status')}: {recommendationResult.execution_allocation.status}
                    {' · '}{t('execution_unspent')}: ¥{Number(recommendationResult.execution_allocation.unspent_budget || 0).toFixed(2)}
                </div>
                {recommendationResult.execution_allocation.unspent_reason_label && (
                    <div className="diagnostic-copy">
                        {t('execution_unspent_reason')}: {recommendationResult.execution_allocation.unspent_reason_label}
                    </div>
                )}
                {recommendationResult.execution_allocation.tracking_error_before !== null && recommendationResult.execution_allocation.tracking_error_before !== undefined && (
                    <div className="diagnostic-copy">
                        {t('tracking_error')}: {formatPercentValue(recommendationResult.execution_allocation.tracking_error_before)}
                        {' → '}{formatPercentValue(recommendationResult.execution_allocation.tracking_error_after)}
                    </div>
                )}
                {Object.keys(recommendationResult.execution_allocation.substitute_purchases || {}).length > 0 && (
                    <div className="diagnostic-copy">{t('substitute_purchase_note')}</div>
                )}
            </div>
        )}
        {recommendationResult.fund_advice && (
            <table className="recommendation-table">
                <thead>
                    <tr>
                        <th>{t('table_fund')}</th>
                        <th>{t('table_action')}</th>
                        <th>{t('table_current')}</th>
                        <th>{t('table_amount')}</th>
                        <th>{t('table_gap')}</th>
                        <th>{t('table_target')}</th>
                        <th>{t('table_ideal_target')}</th>
                        <th>{t('table_reason')}</th>
                    </tr>
                </thead>
                <tbody>
                    {recommendationResult.fund_advice.map(advice => {
                        const actionMeta = getAdviceActionMeta(advice.action, t);
                        const currentHolding = advice.current_holding ?? 0;
                        const afterTrade = advice.executable_holding ?? advice.target_holding;
                        const executableGap = afterTrade === undefined || afterTrade === null ? null : afterTrade - currentHolding;
                        const idealTarget = advice.ideal_holding ?? advice.target_holding;
                        return (
                            <tr key={advice.code}>
                                <td>{advice.name}</td>
                                <td>
                                    <span className={`action-badge ${actionMeta.badgeClass}`}>
                                        {actionMeta.label}
                                    </span>
                                </td>
                                <td className="font-mono">¥{advice.current_holding?.toFixed(2) ?? '--'}</td>
                                <td className="font-mono">¥{advice.amount.toFixed(2)}</td>
                                <td className={`font-mono ${executableGap > 0 ? 'text-emerald-400' : executableGap < 0 ? 'text-amber-400' : ''}`}>{executableGap === undefined || executableGap === null ? '--' : `¥${executableGap.toFixed(2)}`}</td>
                                <td className="font-mono">¥{afterTrade?.toFixed(2) ?? '--'}</td>
                                <td className="font-mono text-slate-400">¥{idealTarget?.toFixed(2) ?? '--'}</td>
                                <td className="text-slate-500 text-xs">{advice.reason}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        )}
    </div>
</section>
    );
}
