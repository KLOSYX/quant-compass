import React from 'react';
import { render, screen } from '@testing-library/react';
import MonthlyRecommendation from './MonthlyRecommendation';
import { LanguageProvider } from './LanguageContext';

test('keeps pending proceeds and available cash as separate recommendation rows', () => {
    const result = {
        monthly_budget: 100, recommended_monthly_investment: 0,
        decision_readiness: 'research_only', cash_reserve_shortfall: 400,
        fund_advice: [
            { code: 'Cash', name: 'Available cash', action: 'Hold', amount: 100, executable_holding: 100 },
            { code: 'PendingCash', name: 'Pending proceeds', action: 'Hold', amount: 400, executable_holding: 400 }
        ]
    };
    render(<LanguageProvider><MonthlyRecommendation recommendationResult={result} onExport={() => {}} /></LanguageProvider>);
    expect(screen.getByText('Available cash')).toBeInTheDocument();
    expect(screen.getByText('Pending proceeds')).toBeInTheDocument();
});

test('retains the monthly plan while showing a risk preference warning', () => {
    const result = {
        monthly_budget: 100, recommended_monthly_investment: 100,
        optimizer_info: { drawdown_preference_exceeded: true },
        fund_advice: [{ code: 'A', name: 'Planned fund', action: 'Buy', amount: 100 }]
    };
    render(<LanguageProvider><MonthlyRecommendation recommendationResult={result} onExport={() => {}} /></LanguageProvider>);
    expect(screen.getByText(/模型估计风险仍超过所设偏好|Estimated risk still exceeds/)).toBeInTheDocument();
    expect(screen.getByText('Planned fund')).toBeInTheDocument();
});


test('labels standard-month allocations as staged totals', () => {
    const result = { monthly_budget: 3000, recommended_monthly_investment: 3000, fund_advice: [], execution_allocation: {
        planning_period_days: 30, planned_purchase_days: 21, status: 'ok', unspent_budget: 0
    }};
    render(<LanguageProvider><MonthlyRecommendation recommendationResult={result} onExport={() => {}} /></LanguageProvider>);
    expect(screen.getByText(/规划周期为 30 个自然日，按 21 个可交易日估算/)).toBeInTheDocument();
    expect(screen.getByText(/这是分批投入总额/)).toBeInTheDocument();
});
