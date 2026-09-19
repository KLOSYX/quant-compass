import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import {
    buildAssetCategoriesPayload,
    buildSubstituteForPayload,
    getRecommendationEvidence,
    getRecommendedFrontierPoint,
    sanitizeLegacyHoldings
} from './PortfolioOptimizer';
import PortfolioOptimizer from './PortfolioOptimizer';
import { LanguageProvider } from './LanguageContext';
import { translations } from './i18n/translations';

jest.mock('echarts-for-react', () => ({ onEvents }) => (
    <button
        type="button"
        data-testid="mock-frontier-chart"
        onClick={() => onEvents?.click?.({ dataIndex: 0 })}
    >
        mock chart
    </button>
));

beforeEach(() => {
    localStorage.clear();
    jest.restoreAllMocks();
});

test('preserves legacy holdings for explicit migration rather than silently dropping wealth', () => {
    expect(sanitizeLegacyHoldings({ '000001': '1200', RiskFree: '300' })).toEqual({
        '000001': '1200', RiskFree: '300'
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

test('builds only valid substitute relationships in the current fund universe', () => {
    expect(buildSubstituteForPayload(
        ['A', 'B', 'C'],
        { A: '', B: ' A ', C: 'C', old: 'A', ignored: 'missing' }
    )).toEqual({
        B: 'A'
    });
});

test('resolves the backend recommended frontier point for explicit reset', () => {
    const frontier = [{ risk: 0.1 }, { risk: 0.2 }];
    expect(getRecommendedFrontierPoint({
        efficient_frontier: frontier,
        recommended_point_index: 1
    })).toBe(frontier[1]);
    expect(getRecommendedFrontierPoint({
        efficient_frontier: frontier,
        recommended_point_index: null
    })).toBeNull();
    expect(translations.zh.reset_to_recommended_point).toContain('推荐点');
    expect(translations.en.reset_to_recommended_point).toContain('Recommended Point');
});

test('exposes the evidence attached to the backend recommendation', () => {
    const frontier = [{ risk: 0.1 }, { risk: 0.2, frontier_walk_forward_sharpe: 0.8 }];
    expect(getRecommendationEvidence({
        efficient_frontier: frontier,
        recommended_point_index: 1,
        recommended_point_selection: {
            eligible_count: 3,
            total_count: 20,
            confidence: 'limited'
        }
    })).toEqual({
        point: frontier[1],
        eligibleCount: 3,
        totalCount: 20,
        confidence: 'limited'
    });
});

test('shows an explicit reset button after the backend recommends a frontier point', async () => {
    localStorage.setItem('fundCodes', JSON.stringify(['A']));
    localStorage.setItem('fundNames', JSON.stringify({ A: 'Fund A' }));
    jest.spyOn(global, 'fetch').mockResolvedValue({
        ok: true,
        json: async () => ({
            efficient_frontier: [
                { risk: 0.1, return: 0.05, weights: { A: 1 } },
                {
                    risk: 0.2,
                    return: 0.08,
                    weights: { A: 1 },
                    frontier_walk_forward_sharpe: 0.75,
                    frontier_walk_forward_annualized_return: 0.07,
                    frontier_walk_forward_max_drawdown: 0.12,
                    frontier_walk_forward_weight_stability: 0.82
                }
            ],
            recommended_point_index: 1,
            recommended_point_selection: {
                eligible_count: 4,
                total_count: 20,
                confidence: 'limited'
            },
            fund_names: { A: 'Fund A' },
            asset_categories: { A: 'equity' },
            backtest_period: { start_date: '2023-01-01', end_date: '2026-01-01' },
            warnings: []
        })
    });

    render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);
    fireEvent.click(screen.getByRole('button', { name: translations.zh.analyze_btn }));

    const resetButton = await screen.findByRole('button', {
        name: translations.zh.reset_to_recommended_point
    });
    expect(resetButton).toBeInTheDocument();
    expect(resetButton).toBeDisabled();
    expect(screen.getByTestId('recommendation-evidence')).toHaveTextContent('4/20');
    expect(screen.getByTestId('recommendation-evidence')).toHaveTextContent('0.75');
    expect(screen.getByTestId('recommendation-evidence')).toHaveTextContent(
        translations.zh.recommendation_limited_confidence
    );

    fireEvent.click(screen.getByTestId('mock-frontier-chart'));
    expect(resetButton).toBeEnabled();

    fireEvent.click(resetButton);
    expect(resetButton).toBeDisabled();
});

test('keeps analysis warnings in a compact expandable methodology section', async () => {
    localStorage.setItem('fundCodes', JSON.stringify(['A']));
    localStorage.setItem('fundNames', JSON.stringify({ A: 'Fund A' }));
    jest.spyOn(global, 'fetch').mockResolvedValue({
        ok: true,
        json: async () => ({
            efficient_frontier: [{ risk: 0.1, return: 0.05, weights: { A: 1 } }],
            recommended_point_index: 0,
            recommended_point_selection: { eligible_count: 1, total_count: 1, confidence: 'standard' },
            fund_names: { A: 'Fund A' },
            asset_categories: { A: 'equity' },
            backtest_period: { start_date: '2023-01-01', end_date: '2026-01-01' },
            warnings: ['First methodology note', 'Second methodology note']
        })
    });

    render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);
    fireEvent.click(screen.getByRole('button', { name: translations.zh.analyze_btn }));

    const summary = await screen.findByText(translations.zh.methodology_notes_title);
    const details = summary.closest('details');
    expect(details).not.toHaveAttribute('open');
    expect(details).toHaveTextContent('2 条说明');
    expect(details).toHaveTextContent('First methodology note');
    expect(details).toHaveTextContent('Second methodology note');
});

test('distinguishes fund portfolio ratio from equity exposure in copy', () => {
    expect(translations.zh.suggested_target).toContain('基金');
    expect(translations.en.suggested_target).toContain('Fund');
    expect(translations.zh.target_equity_exposure).toContain('股票权益');
    expect(translations.en.target_equity_exposure).toContain('Equity Exposure');
});

test('explains the executable walk-forward comparison separately from the frontier', () => {
    expect(translations.zh.executable_walk_forward_title).toContain('可执行策略');
    expect(translations.zh.executable_walk_forward_note).toContain('只使用当时可见数据');
    expect(translations.zh.wf_low_evidence).toContain('不足以支持策略切换');
    expect(translations.en.wf_full_strategy).not.toContain('Kelly');
});

test('states that covariance ablation does not auto-switch the production model', () => {
    expect(translations.zh.covariance_ablation_note).toContain('不会自动切换');
    expect(translations.en.covariance_ablation_note).toContain('never switched automatically');
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

test('labels fixed-target backtests without retired strategy terminology', () => {
    const keys = [
        'title_suffix_actual', 'chart_strategy_suffix', 'chart_tooltip_strategy',
        'tooltip_strategy_title', 'strat_ideal_fixed_target', 'strat_actual_fixed_target',
        'backtest_note_fixed_target_theory', 'backtest_note_fixed_target_actual',
        'fixed_target_theory', 'fixed_target_actual'
    ];
    ['zh', 'en'].forEach((language) => {
        keys.forEach((key) => {
            expect(translations[language][key]).not.toContain('Kelly');
            expect(translations[language][key]).toBeTruthy();
            expect(translations[language][key]).not.toMatch(/\bVA\b/);
        });
    });
});

test('partial return data exposes a monthly plan without requesting blocked backtests', async () => {
    localStorage.setItem('fundCodes', JSON.stringify(['006662']));
    localStorage.setItem('fundNames', JSON.stringify({ '006662': 'Fund A' }));
    localStorage.setItem('monthlyInvestment', '1000');
    const fetchMock = jest.spyOn(global, 'fetch').mockImplementation(async (url) => ({
        ok: true,
        json: async () => url === '/api/analyze' ? {
            analysis_status: 'target_only_fallback', fallback_message: '总收益未核验，使用备用配置',
            fallback_target: { weights: { '006662': 1 }, risk: null, return: null },
            efficient_frontier: [], recommended_point_index: null,
            fund_names: { '006662': 'Fund A' }, warnings: [],
            backtest_period: { start_date: '2026-01-01', end_date: '2026-08-31' }
        } : { monthly_budget: 1000, fund_advice: [], fallback_used: true }
    }));
    render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);
    fireEvent.click(screen.getByRole('button', { name: translations.zh.analyze_btn }));
    expect(await screen.findByRole('status')).toHaveTextContent('总收益未核验');
    expect(screen.queryByTestId('mock-frontier-chart')).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: translations.zh.start_analysis_btn }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => url === '/api/current_recommendation')).toBe(true));
    expect(fetchMock.mock.calls.some(([url]) => url === '/api/backtest_strategies')).toBe(false);
});

test('keeps a confirmed target across reanalysis and reload and sends pending execution inputs', async () => {
    const target = { weights: { A: 0.7, B: 0.3 }, risk: 0.1, return: 0.05 };
    localStorage.setItem('fundCodes', JSON.stringify(['A', 'B']));
    localStorage.setItem('fundNames', JSON.stringify({ A: 'Fund A', B: 'Fund B' }));
    localStorage.setItem('confirmedTarget', JSON.stringify(target));
    localStorage.setItem('monthlyInvestment', '1000');
    localStorage.setItem('pendingProceeds', '500');
    localStorage.setItem('pendingSells', JSON.stringify({ A: '200' }));
    const fetchMock = jest.spyOn(global, 'fetch').mockImplementation(async (url) => ({
        ok: true,
        json: async () => url === '/api/analyze' ? {
            analysis_status: 'target_only_fallback', fallback_message: 'Target only',
            fallback_target: { weights: { A: 0.5, B: 0.5 }, risk: null, return: null },
            efficient_frontier: [], recommended_point_index: null,
            fund_names: { A: 'Fund A', B: 'Fund B' }, warnings: [],
            backtest_period: { start_date: '2026-01-01', end_date: '2026-08-31' }
        } : { monthly_budget: 1000, recommended_monthly_investment: 1000, fund_advice: [] }
    }));
    const first = render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);
    for (let i = 0; i < 2; i++) {
        fireEvent.click(screen.getByRole('button', { name: translations.zh.analyze_btn }));
        await waitFor(() => expect(screen.getByRole('button', { name: translations.zh.analyze_btn })).toBeEnabled());
        fireEvent.click(await screen.findByRole('button', { name: translations.zh.start_analysis_btn }));
        await waitFor(() => expect(fetchMock.mock.calls.filter(([url]) => url === '/api/current_recommendation')).toHaveLength(i + 1));
    }
    first.unmount();
    render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);
    fireEvent.click(screen.getByRole('button', { name: translations.zh.analyze_btn }));
    await screen.findByRole('status');
    const analysisRequests = fetchMock.mock.calls.filter(([url]) => url === '/api/analyze');
    expect(analysisRequests).toHaveLength(3);
    analysisRequests.forEach(([, options]) => expect(JSON.parse(options.body).target_weights).toEqual(target.weights));
    fetchMock.mock.calls.filter(([url]) => url === '/api/current_recommendation').forEach(([, options]) => {
        const body = JSON.parse(options.body);
        expect(body.weights).toEqual(target.weights);
        expect(body.pending_sale_proceeds).toBe(500);
        expect(body.pending_sell_amounts).toEqual({ A: 200 });
        expect(body.strategy_mode).toBe('fixed_weight');
    });
});

test('advanced settings show cash first and add fund exceptions only when needed', async () => {
    localStorage.setItem('fundCodes', JSON.stringify(['A', 'B', 'C']));
    localStorage.setItem('fundNames', JSON.stringify({ A: 'Fund A', B: 'Fund B', C: 'Fund C' }));
    localStorage.setItem('redemptionLimits', JSON.stringify({ A: '0' }));
    jest.spyOn(global, 'fetch').mockResolvedValue({ ok: true, json: async () => ({
        analysis_status: 'target_only_fallback', fallback_message: 'Target only',
        fallback_target: { weights: { A: 0.5, B: 0.3, C: 0.2 }, risk: null, return: null },
        efficient_frontier: [], recommended_point_index: null,
        fund_names: { A: 'Fund A', B: 'Fund B', C: 'Fund C' }, warnings: [],
        backtest_period: { start_date: '2026-01-01', end_date: '2026-08-31' }
    }) });
    const { container } = render(<LanguageProvider><PortfolioOptimizer /></LanguageProvider>);
    fireEvent.click(screen.getByRole('button', { name: translations.zh.analyze_btn }));
    fireEvent.click(await screen.findByRole('button', { name: translations.zh.expand_advanced }));
    expect(screen.getByLabelText(translations.zh.minimum_cash_reserve)).toBeVisible();
    expect(screen.getByLabelText(translations.zh.available_cash)).toBeVisible();
    const details = screen.getByText(translations.zh.settings_transactions).closest('details');
    expect(details).not.toHaveAttribute('open');
    expect(details).toHaveTextContent(translations.zh.settings_has_values);
    expect(container.querySelector('#redeem-A')).toHaveValue(0);
    expect(container.querySelector('#pending-B')).toBeNull();
    expect(container.querySelector('#pending-C')).toBeNull();
    fireEvent.click(details.querySelector('summary'));
    fireEvent.change(screen.getByLabelText(translations.zh.settings_add_fund), { target: { value: 'B' } });
    fireEvent.change(container.querySelector('#pending-B'), { target: { value: '200' } });
    fireEvent.change(container.querySelector('#redeem-B'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: translations.zh.collapse_advanced }));
    fireEvent.click(screen.getByRole('button', { name: translations.zh.expand_advanced }));
    expect(container.querySelector('#pending-B')).toHaveValue(200);
    expect(container.querySelector('#redeem-B')).toHaveValue(0);
    const reopened = screen.getByText(translations.zh.settings_transactions).closest('details');
    fireEvent.click(reopened.querySelector('summary'));
    fireEvent.click(screen.getByRole('button', { name: `${translations.zh.settings_remove} Fund B` }));
    expect(container.querySelector('#pending-B')).toBeNull();
    expect(JSON.parse(localStorage.getItem('pendingSells')).B).toBe('');
    expect(JSON.parse(localStorage.getItem('redemptionLimits')).A).toBe('0');
});
