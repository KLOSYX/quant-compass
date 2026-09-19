import { createPortfolioReportHtml } from './exportPortfolioReport';

const options = {
    language: 'zh',
    generatedAt: new Date('2026-07-12'),
    selectedPoint: { return: 0.12, risk: 0.2, weights: { A: 0.6, RiskFree: 0.4 } },
    fundNames: { A: '<指数基金>', RiskFree: '货币基金' },
    initialHoldings: { A: 10000 },
    currentCash: 2000,
    recommendationResult: {
        monthly_budget: 1000,
        recommended_monthly_investment: 800,
        target_fund_ratio: 0.6,
        target_equity_exposure: 0.5,
        target_risk_asset_exposure: 0.6,
        current_category_values: { equity: 10000 },
        current_category_exposures: { equity: 0.8333 },
        target_category_values: { equity: 6500, cash_equivalent: 4500 },
        target_category_exposures: { equity: 0.5909, cash_equivalent: 0.4091 },
        fund_advice: [{
            code: 'A', name: '<指数基金>', action: 'Buy', amount: 800,
            executable_holding: 10800, ideal_holding: 12000, reason: '<低估>'
        }]
    }
};

test('creates self-contained escaped report with category exposures', () => {
    const html = createPortfolioReportHtml(options);
    expect(html).toContain('<!doctype html>');
    expect(html).toContain('目前的投资资金分配');
    expect(html).toContain('最终定投计划');
    expect(html).toContain('收益统计口径');
    expect(html).not.toContain('<svg');
    expect(html).toContain('&lt;指数基金&gt;');
    expect(html).not.toContain('<指数基金>');
    expect(html).toContain('闲置现金');
    expect(html).toContain('理论目标');
    expect(html).toContain('建议目标基金组合仓位');
    expect(html).toContain('目标股票权益暴露');
    expect(html).toContain('资产类别暴露');
    expect(html).toContain('现金等价物');
    expect(html).toContain('60.00%');
    expect(html).toContain('50.00%');
});

test('does not extrapolate historical returns into future wealth', () => {
    const html = createPortfolioReportHtml({ ...options, language: 'en' });
    expect(html).toContain('Historical returns are not forecasts');
    expect(html).not.toContain('<svg');
});


test('exports settlement conditions and does not present unavailable risk as zero', () => {
    const html = createPortfolioReportHtml({ ...options,
        selectedPoint: { ...options.selectedPoint, return: null, risk: null },
        recommendationResult: { ...options.recommendationResult, rebalancing: { conditional_buys_after_settlement: { A: 2000 } } }
    });
    expect(html).toContain('到账后条件买入');
    expect(html).toContain('不计入本次可用现金');
    expect(html).toContain('历史收益不是未来收益预测');
    expect(html).not.toContain('<svg');
    expect(html).toContain('— / —');
});
