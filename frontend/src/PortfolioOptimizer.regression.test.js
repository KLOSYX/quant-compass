import { render, screen, waitFor } from '@testing-library/react';
import { buildAssetCategoriesPayload, sanitizeLegacyHoldings } from './PortfolioOptimizer';
import PortfolioOptimizer from './PortfolioOptimizer';
import { LanguageProvider } from './LanguageContext';
import { translations } from './i18n/translations';

beforeEach(() => {
    localStorage.clear();
    jest.restoreAllMocks();
});

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

test('explains the executable walk-forward comparison separately from the frontier', () => {
    expect(translations.zh.executable_walk_forward_title).toContain('可执行策略');
    expect(translations.zh.executable_walk_forward_note).toContain('只使用当时可见数据');
    expect(translations.en.wf_full_strategy).toContain('Kelly');
});

test('shows a cached fund name before portfolio analysis', async () => {
    localStorage.setItem('fundCodes', JSON.stringify(['016149']));
    localStorage.setItem('fundNames', JSON.stringify({ '016149': '招商安泰债券A' }));

    render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);

    expect(await screen.findByText('招商安泰债券A')).toBeInTheDocument();
    expect(screen.getByText('016149')).toBeInTheDocument();
});

test('resolves and persists missing fund names before analysis', async () => {
    localStorage.setItem('fundCodes', JSON.stringify(['270023']));
    jest.spyOn(global, 'fetch').mockResolvedValue({
        ok: true,
        json: async () => ({ fund_names: { '270023': '广发全球精选股票' } })
    });

    render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);

    expect(await screen.findByText('广发全球精选股票')).toBeInTheDocument();
    await waitFor(() => {
        expect(JSON.parse(localStorage.getItem('fundNames'))).toEqual({
            '270023': '广发全球精选股票'
        });
    });
    expect(global.fetch).toHaveBeenCalledWith('/api/fund_names', expect.objectContaining({ method: 'POST' }));
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
