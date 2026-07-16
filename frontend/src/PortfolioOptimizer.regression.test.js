import { sanitizeLegacyHoldings } from './PortfolioOptimizer';
import { translations } from './i18n/translations';

test('removes the retired synthetic RiskFree holding from persisted state', () => {
    expect(sanitizeLegacyHoldings({ '000001': '1200', RiskFree: '300' })).toEqual({
        '000001': '1200'
    });
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
