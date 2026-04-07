import React from 'react';
import { Activity } from 'lucide-react';
import { useLanguage } from './LanguageContext';

function formatPercent(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '--';
    }
    return `${(Number(value) * 100).toFixed(2)}%`;
}

function getDiagnosisText(item, t) {
    switch (item.status) {
    case 'risk_free_anchor':
        return t('diag_reason_risk_free_anchor');
    case 'selected_on_frontier':
        return `${t('diag_reason_selected_on_frontier')} (${item.frontier_points_used}/${item.frontier_point_count})`;
    case 'below_risk_free':
        return t('diag_reason_below_risk_free');
    case 'dominated_by_higher_sharpe_assets':
        return item.sharpe_rank
            ? `${t('diag_reason_dominated_by_higher_sharpe_assets')} (${t('diag_sharpe_rank')}: #${item.sharpe_rank})`
            : t('diag_reason_dominated_by_higher_sharpe_assets');
    default:
        return t('diag_reason_unused_in_sample');
    }
}

export default function AssetDiagnosticsPanel({ diagnostics }) {
    const { t } = useLanguage();

    if (!diagnostics || diagnostics.length === 0) {
        return null;
    }

    return (
        <div className="dashboard-card mt-6">
            <div className="card-header">
                <h3 className="card-title">
                    <Activity size={20} className="card-icon" />
                    {t('asset_diagnostics_title')}
                </h3>
            </div>
            <p className="text-sm text-slate-300 mb-4">{t('asset_diagnostics_note')}</p>
            <div className="overflow-x-auto">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>{t('header_fund')}</th>
                            <th>{t('diag_col_total_return')}</th>
                            <th>{t('diag_col_cagr')}</th>
                            <th>{t('diag_col_optimizer_return')}</th>
                            <th>{t('diag_col_volatility')}</th>
                            <th>{t('diag_col_frontier_use')}</th>
                            <th>{t('diag_col_max_weight')}</th>
                            <th>{t('diag_col_diagnosis')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {diagnostics.map((item) => (
                            <tr key={`diag-${item.code}`}>
                                <td className="text-sm">
                                    {item.name} <span className="text-slate-500 text-xs">({item.code})</span>
                                </td>
                                <td className="font-mono">{formatPercent(item.sample_total_return)}</td>
                                <td className="font-mono">{formatPercent(item.sample_cagr)}</td>
                                <td className="font-mono text-emerald-400">{formatPercent(item.optimizer_expected_return)}</td>
                                <td className="font-mono">{formatPercent(item.annualized_volatility)}</td>
                                <td className="font-mono">{item.frontier_points_used}/{item.frontier_point_count}</td>
                                <td className="font-mono">{formatPercent(item.max_frontier_weight)}</td>
                                <td className={`text-sm ${item.frontier_points_used === 0 && item.code !== 'RiskFree' ? 'text-amber-300' : 'text-slate-300'}`}>
                                    {getDiagnosisText(item, t)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
