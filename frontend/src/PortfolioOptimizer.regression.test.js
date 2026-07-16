import { buildAssetCategoriesPayload, sanitizeLegacyHoldings } from './PortfolioOptimizer';
import { translations } from './i18n/translations';

test('removes the retired synthetic RiskFree holding from persisted state', () => {
    expect(sanitizeLegacyHoldings({ '000001': '1200', RiskFree: '300' })).toEqual({
        '000001': '1200'
    });
});

test('builds explicit categories only for the current analysis universe', () => {
    expect(buildAssetCategoriesPayload(
        ['000001', '000002', '000003'],
        { '000001': 'equity', '000002': 'bond', retired: 'gold' }
    )).toEqual({
        '000001': 'equity',
        '000002': 'bond',
        '000003': 'other'
    });
});

test('distinguishes fund portfolio ratio from equity exposure in copy', () => {
    expect(translations.zh.min_equity_ratio).toContain('基金组合');
    expect(translations.en.min_equity_ratio).toContain('Fund Portfolio');
    expect(translations.zh.target_equity_exposure).toContain('股票权益');
    expect(translations.en.target_equity_exposure).toContain('Equity Exposure');
});

test('labels Kelly DCA backtests without legacy VA terminology', () => {
    const keys = [
        'title_suffix_actual', 'chart_strategy_suffix', 'chart_tooltip_strategy',
        'tooltip_strategy_title', 'strat_ideal_kelly', 'strat_actual_kelly',
        'backtest_note_kelly_theory', 'backtest_note_kelly_actual',
        'kelly_theory', 'kelly_actual'
    ];
    ['zh', 'en'].forEach((language) => {
        keys.forEach((key) => {
            expect(translations[language][key]).toContain('Kelly');
            expect(translations[language][key]).toContain('DCA');
            expect(translations[language][key]).not.toMatch(/\bVA\b/);
        });
    });
});
