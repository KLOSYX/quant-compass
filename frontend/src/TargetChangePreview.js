import React, { useEffect, useRef } from 'react';
import { useLanguage } from './LanguageContext';

export function sameTargetWeights(left, right) {
    if (!left?.weights || !right?.weights) return false;
    const codes = new Set([...Object.keys(left.weights), ...Object.keys(right.weights)]);
    return [...codes].every(code => Math.abs((left.weights[code] || 0) - (right.weights[code] || 0)) < 1e-8);
}

export default function TargetChangePreview({ current, candidate, fundNames, busy, onConfirm, onCancel }) {
    const { t } = useLanguage();
    const heading = useRef(null);
    useEffect(() => { heading.current?.focus(); }, [candidate]);
    const rows = [...new Set([...Object.keys(current?.weights || {}), ...Object.keys(candidate.weights)])].map(code => ({
        code, before: current?.weights?.[code] || 0, after: candidate.weights[code] || 0
    })).sort((a, b) => Math.abs(b.after - b.before) - Math.abs(a.after - a.before));
    const maxChange = Math.max(0, ...rows.map(row => Math.abs(row.after - row.before))) * 100;
    return (
        <section className="target-change-preview" aria-labelledby="target-preview-title">
            <h4 id="target-preview-title" ref={heading} tabIndex={-1}>{t('target_preview_title')}</h4>
            <p>{t(current ? 'target_preview_explanation' : 'target_initial_explanation')}</p>
            {current && <p className="target-change-summary">{t('target_max_change').replace('{value}', maxChange.toFixed(2))}</p>}
            <div className="target-change-table-wrap">
                <table>
                    <thead><tr><th>{t('target_asset')}</th><th>{t('target_current')}</th><th>{t('target_candidate')}</th><th>{t('target_difference')}</th></tr></thead>
                    <tbody>{rows.map(({ code, before, after }) => (
                        <tr key={code}><th scope="row">{fundNames[code] || code}<small>{code}</small></th>
                            <td>{current ? `${(before * 100).toFixed(2)}%` : '—'}</td><td>{(after * 100).toFixed(2)}%</td>
                            <td>{current ? `${after > before ? '+' : ''}${((after - before) * 100).toFixed(2)}` : '—'}</td>
                        </tr>
                    ))}</tbody>
                </table>
            </div>
            <p>{t('target_switch_warning')}</p>
            <div className="inline-actions">
                <button type="button" className="btn btn-primary" disabled={busy} onClick={onConfirm}>{t('target_confirm_switch')}</button>
                <button type="button" className="text-link-btn" onClick={onCancel}>{t('target_cancel_preview')}</button>
            </div>
        </section>
    );
}
