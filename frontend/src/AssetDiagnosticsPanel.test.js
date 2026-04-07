import { render, screen } from '@testing-library/react';
import AssetDiagnosticsPanel from './AssetDiagnosticsPanel';
import { LanguageProvider } from './LanguageContext';

test('renders asset diagnostics explanations', () => {
    render(
        <LanguageProvider>
            <AssetDiagnosticsPanel
                diagnostics={[
                    {
                        code: '016149',
                        name: 'Bond Fund',
                        sample_total_return: 0.1267,
                        sample_cagr: 0.0405,
                        optimizer_expected_return: 0.035,
                        annualized_volatility: 0.012,
                        frontier_points_used: 0,
                        frontier_point_count: 20,
                        max_frontier_weight: 0,
                        status: 'dominated_by_higher_sharpe_assets',
                        sharpe_rank: 3,
                    },
                    {
                        code: 'RiskFree',
                        name: '无风险资产',
                        sample_total_return: 0.06,
                        sample_cagr: 0.02,
                        optimizer_expected_return: 0.02,
                        annualized_volatility: 0,
                        frontier_points_used: 20,
                        frontier_point_count: 20,
                        max_frontier_weight: 1,
                        status: 'risk_free_anchor',
                        sharpe_rank: null,
                    },
                ]}
            />
        </LanguageProvider>
    );

    expect(screen.getByText('资产诊断')).toBeInTheDocument();
    expect(screen.getByText(/单位风险超额收益不占优/)).toBeInTheDocument();
    expect(screen.getByText(/显式无风险锚点/)).toBeInTheDocument();
    expect(screen.getByText('12.67%')).toBeInTheDocument();
});
