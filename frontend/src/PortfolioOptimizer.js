import React, { useState, useEffect, useRef } from 'react';
import { useLanguage } from './LanguageContext';
import ReactECharts from 'echarts-for-react';
import { Plus, X, ArrowRight, Settings, Info, TrendingUp, DollarSign, Wallet, Calendar } from 'lucide-react';

const getISODate = (date) => date.toISOString().split('T')[0];
const formatDD = (obj, key, fallbackKey) => {
    const val = obj?.[key] ?? obj?.[fallbackKey];
    if (val === undefined || val === null) return '--';
    return `${(val * 100).toFixed(2)}%`;
};

// Format money values for better readability (e.g., 1234567 -> "123.46万")
const formatMoney = (value) => {
    if (value === null || value === undefined) return '--';
    const num = Number(value);
    if (isNaN(num)) return '--';

    const absNum = Math.abs(num);
    const sign = num < 0 ? '-' : '';

    if (absNum >= 100000000) {
        // >= 1亿
        return `${sign}${(absNum / 100000000).toFixed(2)}亿`;
    } else if (absNum >= 10000) {
        // >= 1万
        return `${sign}${(absNum / 10000).toFixed(2)}万`;
    } else if (absNum >= 1) {
        return `${sign}${absNum.toFixed(2)}`;
    } else {
        return `${sign}${absNum.toFixed(2)}`;
    }
};

const getAdviceActionMeta = (action, t) => {
    if (action === 'Buy') {
        return { label: t('action_buy'), badgeClass: 'buy' };
    }
    if (action === 'Sell') {
        return { label: t('action_sell'), badgeClass: 'sell' };
    }
    if (action === '存入' || action === 'Deposit') {
        return { label: t('action_deposit'), badgeClass: 'deposit' };
    }
    if (action === '取用' || action === 'Withdraw') {
        return { label: t('action_withdraw'), badgeClass: 'withdraw' };
    }
    return { label: t('action_hold'), badgeClass: 'hold' };
};

const getAllocationSignalLabel = (signal, t) => {
    if (signal === 'undervalued') return t('allocation_upper');
    if (signal === 'overvalued') return t('allocation_lower');
    return t('allocation_neutral');
};

const getStoredNumber = (key, fallback) => {
    const raw = localStorage.getItem(key);
    if (raw === null || raw === undefined || raw === '') return fallback;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const getStoredPercentWithLegacyRatioSupport = (key, fallback) => {
    const raw = localStorage.getItem(key);
    if (raw === null || raw === undefined || raw === '') return fallback;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return fallback;
    // Legacy versions persisted ratios (0~1). Current UI expects percentages (0~100).
    if (parsed > 0 && parsed <= 1) {
        const migrated = parsed * 100;
        localStorage.setItem(key, String(migrated));
        return migrated;
    }
    return parsed;
};

function PortfolioOptimizer() {
    const { t } = useLanguage();
    const [fundCodes, setFundCodes] = useState([]);
    const [fundFees, setFundFees] = useState({});
    const [fundBuyFees, setFundBuyFees] = useState({});
    const [fundSellFees, setFundSellFees] = useState({});
    const [currentInput, setCurrentInput] = useState('');
    const [hasRiskFree, setHasRiskFree] = useState(false);
    const [riskFreeRate, setRiskFreeRate] = useState('2.0');
    const [startDate, setStartDate] = useState(() => {
        const saved = localStorage.getItem('startDate');
        if (saved) return saved;
        const d = new Date();
        d.setFullYear(d.getFullYear() - 3);
        return getISODate(d);
    });
    const [endDate, setEndDate] = useState(() => localStorage.getItem('endDate') || getISODate(new Date()));
    const [analysisResult, setAnalysisResult] = useState(null);
    const [selectedPoint, setSelectedPoint] = useState(null);
    const [monthlyInvestment, setMonthlyInvestment] = useState(() => localStorage.getItem('monthlyInvestment') || '');
    const [initialHoldings, setInitialHoldings] = useState(() => JSON.parse(localStorage.getItem('initialHoldings') || '{}'));
    const [currentCash, setCurrentCash] = useState(() => localStorage.getItem('currentCash') || '');

    // Advanced Strategy Parameters
    const [showAdvancedParams, setShowAdvancedParams] = useState(false);
    const [strategyMode, setStrategyMode] = useState(() => localStorage.getItem('strategyMode') || 'optimized_kelly');
    const [kellyFraction, setKellyFraction] = useState(() => getStoredPercentWithLegacyRatioSupport('kellyFraction', 50)); // pct
    const [estimationWindow, setEstimationWindow] = useState(() => getStoredNumber('estimationWindow', 36));
    const [minimumCashReserve, setMinimumCashReserve] = useState(() => getStoredNumber('minimumCashReserve', 0));
    const [enableCvarConstraint, setEnableCvarConstraint] = useState(() => {
        const saved = localStorage.getItem('enableCvarConstraint');
        return saved === null ? true : JSON.parse(saved);
    });
    const [cvarConfidence, setCvarConfidence] = useState(() => getStoredPercentWithLegacyRatioSupport('cvarConfidence', 95)); // pct
    const [cvarLimit, setCvarLimit] = useState(() => getStoredPercentWithLegacyRatioSupport('cvarLimit', 8)); // pct
    const [enableDrawdownConstraint, setEnableDrawdownConstraint] = useState(() => {
        const saved = localStorage.getItem('enableDrawdownConstraint');
        return saved === null ? true : JSON.parse(saved);
    });
    const [maxDrawdownLimit, setMaxDrawdownLimit] = useState(() => getStoredPercentWithLegacyRatioSupport('maxDrawdownLimit', 20)); // pct
    const [maxBuyMultiplier, setMaxBuyMultiplier] = useState(() => getStoredNumber('maxBuyMultiplier', 3.0));
    const [sellThreshold, setSellThreshold] = useState(() => getStoredPercentWithLegacyRatioSupport('sellThreshold', 5.0)); // pct
    const [minWeight, setMinWeight] = useState(() => getStoredPercentWithLegacyRatioSupport('minWeight', 30)); // pct
    const [maxWeight, setMaxWeight] = useState(() => getStoredPercentWithLegacyRatioSupport('maxWeight', 80)); // pct
    const [maWindow, setMaWindow] = useState(() => getStoredNumber('maWindow', 12));
    const [strategyResult, setStrategyResult] = useState(null);
    const [recommendationResult, setRecommendationResult] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState({ analysis: false, strategy: false, recommendation: false });
    const [budgetError, setBudgetError] = useState('');
    const [showBacktestNote, setShowBacktestNote] = useState(false);
    const [backtestNotePinned, setBacktestNotePinned] = useState(false);
    const backtestNoteRef = useRef(null);

    useEffect(() => {
        const savedFundCodes = localStorage.getItem('fundCodes');
        const savedFundFees = localStorage.getItem('fundFees');
        const savedFundBuyFees = localStorage.getItem('fundBuyFees');
        const savedFundSellFees = localStorage.getItem('fundSellFees');
        const savedHasRiskFree = localStorage.getItem('hasRiskFree');
        const savedRiskFreeRate = localStorage.getItem('riskFreeRate');
        if (savedFundCodes) setFundCodes(JSON.parse(savedFundCodes));
        if (savedFundFees) setFundFees(JSON.parse(savedFundFees));
        if (savedFundBuyFees) setFundBuyFees(JSON.parse(savedFundBuyFees));
        if (savedFundSellFees) setFundSellFees(JSON.parse(savedFundSellFees));
        if (savedHasRiskFree) setHasRiskFree(JSON.parse(savedHasRiskFree));
        if (savedRiskFreeRate) setRiskFreeRate(savedRiskFreeRate);
    }, []);

    useEffect(() => {
        const handleClickOutside = (event) => {
            if (!backtestNoteRef.current || backtestNoteRef.current.contains(event.target)) return;
            setBacktestNotePinned(false);
            setShowBacktestNote(false);
        };
        document.addEventListener('mousedown', handleClickOutside);
        document.addEventListener('touchstart', handleClickOutside);
        return () => {
            document.removeEventListener('mousedown', handleClickOutside);
            document.removeEventListener('touchstart', handleClickOutside);
        };
    }, []);

    const handleAddFundCode = () => {
        if (currentInput && !fundCodes.includes(currentInput)) {
            const newFundCodes = [...fundCodes, currentInput.trim()];
            const newFundFees = { ...fundFees, [currentInput.trim()]: '' };
            setFundCodes(newFundCodes);
            setFundFees(newFundFees);
            localStorage.setItem('fundCodes', JSON.stringify(newFundCodes));
            localStorage.setItem('fundFees', JSON.stringify(newFundFees));
            setCurrentInput('');
        }
    };

    const handleRemoveAsset = (codeToRemove) => {
        if (codeToRemove === 'RiskFree') {
            setHasRiskFree(false);
            localStorage.setItem('hasRiskFree', JSON.stringify(false));
        } else {
            const newFundCodes = fundCodes.filter(code => code !== codeToRemove);
            const newFundFees = { ...fundFees };
            delete newFundFees[codeToRemove];
            setFundCodes(newFundCodes);
            setFundFees(newFundFees);
            localStorage.setItem('fundCodes', JSON.stringify(newFundCodes));
            localStorage.setItem('fundFees', JSON.stringify(newFundFees));
        }
    };

    const handleAddRiskFree = () => {
        setHasRiskFree(true);
        localStorage.setItem('hasRiskFree', JSON.stringify(true));
    };

    const handleFeeChange = (code, fee) => {
        const newFundFees = { ...fundFees, [code]: fee };
        setFundFees(newFundFees);
        localStorage.setItem('fundFees', JSON.stringify(newFundFees));
    };

    const handleBuyFeeChange = (code, fee) => {
        const newFees = { ...fundBuyFees, [code]: fee };
        setFundBuyFees(newFees);
        localStorage.setItem('fundBuyFees', JSON.stringify(newFees));
    };

    const handleSellFeeChange = (code, fee) => {
        const newFees = { ...fundSellFees, [code]: fee };
        setFundSellFees(newFees);
        localStorage.setItem('fundSellFees', JSON.stringify(newFees));
    };

    const handleRiskFreeRateChange = (rate) => {
        setRiskFreeRate(rate);
        localStorage.setItem('riskFreeRate', rate);
    }

    const handleHoldingChange = (code, value) => {
        const newHoldings = { ...initialHoldings, [code]: value };
        setInitialHoldings(newHoldings);
        localStorage.setItem('initialHoldings', JSON.stringify(newHoldings));
    };

    const setDateRange = (years) => {
        const end = new Date();
        const start = new Date();
        start.setFullYear(start.getFullYear() - years);
        const startStr = getISODate(start);
        const endStr = getISODate(end);
        setStartDate(startStr);
        setEndDate(endStr);
        localStorage.setItem('startDate', startStr);
        localStorage.setItem('endDate', endStr);
    };

    const handleAnalysisSubmit = async (e) => {
        e.preventDefault();
        setLoading({ ...loading, analysis: true });
        setError(null);
        setAnalysisResult(null);
        setSelectedPoint(null);
        setStrategyResult(null);
        setMonthlyInvestment('');


        try {
            const feesAsFloats = Object.entries(fundFees).reduce((acc, [code, fee]) => {
                const parsedFee = parseFloat(fee);
                acc[code] = isNaN(parsedFee) ? 0 : parsedFee / 100;
                return acc;
            }, {});
            const parsedKellyFraction = parseFloat(kellyFraction);
            const parsedEstimationWindow = parseInt(estimationWindow, 10);
            const parsedMinimumCashReserve = parseFloat(minimumCashReserve);
            const parsedCvarConfidence = parseFloat(cvarConfidence);
            const parsedCvarLimit = parseFloat(cvarLimit);
            const parsedMaxDrawdownLimit = parseFloat(maxDrawdownLimit);
            const parsedMaxBuyMultiplier = parseFloat(maxBuyMultiplier);
            const parsedSellThreshold = parseFloat(sellThreshold);
            const parsedMinWeight = parseFloat(minWeight);
            const parsedMaxWeight = parseFloat(maxWeight);
            const parsedMaWindow = parseInt(maWindow, 10);

            const payload = {
                fund_codes: fundCodes,
                fund_fees: feesAsFloats,
                start_date: startDate,
                end_date: endDate,
                risk_free_rate: hasRiskFree ? (parseFloat(riskFreeRate) || 0) / 100 : null,
                strategy_mode: strategyMode,
                kelly_fraction: (Number.isNaN(parsedKellyFraction) ? 50 : parsedKellyFraction) / 100,
                estimation_window: Number.isNaN(parsedEstimationWindow) ? 36 : parsedEstimationWindow,
                minimum_cash_reserve: Number.isNaN(parsedMinimumCashReserve) ? 0 : parsedMinimumCashReserve,
                enable_cvar_constraint: enableCvarConstraint,
                cvar_confidence: (Number.isNaN(parsedCvarConfidence) ? 95 : parsedCvarConfidence) / 100,
                cvar_limit: (Number.isNaN(parsedCvarLimit) ? 8 : parsedCvarLimit) / 100,
                enable_drawdown_constraint: enableDrawdownConstraint,
                max_drawdown_limit: (Number.isNaN(parsedMaxDrawdownLimit) ? 20 : parsedMaxDrawdownLimit) / 100,
                max_buy_multiplier: Number.isNaN(parsedMaxBuyMultiplier) ? 3.0 : parsedMaxBuyMultiplier,
                sell_threshold: (Number.isNaN(parsedSellThreshold) ? 5.0 : parsedSellThreshold) / 100,
                min_weight: (Number.isNaN(parsedMinWeight) ? 30 : parsedMinWeight) / 100,
                max_weight: (Number.isNaN(parsedMaxWeight) ? 80 : parsedMaxWeight) / 100,
                ma_window: Number.isNaN(parsedMaWindow) ? 12 : parsedMaWindow,
            };

            const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error((await response.json()).detail);
            setAnalysisResult(await response.json());
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading({ ...loading, analysis: false });
        }
    };

    const runBacktests = async (weights) => {
        setLoading((prev) => ({ ...prev, strategy: true }));
        setError(null);
        setStrategyResult(null);

        try {
            const feesAsFloats = Object.entries(fundFees).reduce((acc, [code, fee]) => {
                const parsedFee = parseFloat(fee);
                acc[code] = isNaN(parsedFee) ? 0 : parsedFee / 100;
                return acc;
            }, {});

            // Calculate total capital
            const totalHoldingsValue = Object.values(initialHoldings).reduce((sum, val) => sum + (parseFloat(val) || 0), 0);
            const totalCash = parseFloat(currentCash) || 0;
            const totalCapital = totalHoldingsValue + totalCash;

            // IDEAL: Perfect allocation based on target weights
            const idealHoldings = {};
            if (totalCapital > 0) {
                Object.entries(weights).forEach(([code, weight]) => {
                    idealHoldings[code] = totalCapital * weight;
                });
            }

            // ACTUAL: User's real holdings + cash as RiskFree
            const actualHoldings = Object.entries(initialHoldings).reduce((acc, [code, val]) => {
                const v = parseFloat(val);
                if (v > 0) acc[code] = v;
                return acc;
            }, {});
            if (totalCash > 0) actualHoldings['RiskFree'] = (actualHoldings['RiskFree'] || 0) + totalCash;

            const basePayload = {
                fund_codes: fundCodes,
                weights,
                fund_fees: feesAsFloats,
                start_date: analysisResult.backtest_period.start_date,
                end_date: analysisResult.backtest_period.end_date,
                monthly_investment: parseFloat(monthlyInvestment),
                risk_free_rate: hasRiskFree ? (parseFloat(riskFreeRate) || 0) / 100 : null,
                max_buy_multiplier: parseFloat(maxBuyMultiplier),
                sell_threshold: parseFloat(sellThreshold) / 100,
                min_weight: parseFloat(minWeight) / 100,
                max_weight: parseFloat(maxWeight) / 100,
                strategy_mode: strategyMode,
                kelly_fraction: (() => {
                    const value = parseFloat(kellyFraction);
                    return (Number.isNaN(value) ? 50 : value) / 100;
                })(),
                estimation_window: (() => {
                    const value = parseInt(estimationWindow, 10);
                    return Number.isNaN(value) ? 36 : value;
                })(),
                minimum_cash_reserve: (() => {
                    const value = parseFloat(minimumCashReserve);
                    return Number.isNaN(value) ? 0 : value;
                })(),
                enable_cvar_constraint: enableCvarConstraint,
                cvar_confidence: (() => {
                    const value = parseFloat(cvarConfidence);
                    return (Number.isNaN(value) ? 95 : value) / 100;
                })(),
                cvar_limit: (() => {
                    const value = parseFloat(cvarLimit);
                    return (Number.isNaN(value) ? 8 : value) / 100;
                })(),
                enable_drawdown_constraint: enableDrawdownConstraint,
                max_drawdown_limit: (() => {
                    const value = parseFloat(maxDrawdownLimit);
                    return (Number.isNaN(value) ? 20 : value) / 100;
                })(),
                buy_fee: Object.entries(fundBuyFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                sell_fee: Object.entries(fundSellFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                ma_window: parseInt(maWindow)
            };

            // Run BOTH backtests in parallel
            const [idealRes, actualRes] = await Promise.all([
                fetch('/api/backtest_strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...basePayload, initial_holdings: idealHoldings }) }),
                fetch('/api/backtest_strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...basePayload, initial_holdings: actualHoldings }) })
            ]);

            const idealData = await idealRes.json();
            const actualData = await actualRes.json();

            // Store both results - keep backward compatible structure
            setStrategyResult({
                ...idealData,
                ideal_kelly_dca: idealData.kelly_dca,
                actual_kelly_dca: actualData.kelly_dca
            });
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading((prev) => ({ ...prev, strategy: false }));
        }
    };

    const getRecommendation = async () => {
        setLoading((prev) => ({ ...prev, recommendation: true }));
        try {
            const holdingsAsFloats = Object.entries(initialHoldings).reduce((acc, [code, val]) => {
                const parsed = parseFloat(val);
                if (!isNaN(parsed) && parsed > 0) {
                    acc[code] = parsed;
                }
                return acc;
            }, {});

            const feesAsFloats = Object.entries(fundFees).reduce((acc, [code, fee]) => {
                const parsedFee = parseFloat(fee);
                acc[code] = isNaN(parsedFee) ? 0 : parsedFee / 100;
                return acc;
            }, {});

            const payload = {
                fund_codes: fundCodes,
                fund_fees: feesAsFloats,
                weights: selectedPoint.weights,
                current_holdings: holdingsAsFloats,
                current_cash: parseFloat(currentCash) || 0,
                monthly_budget: parseFloat(monthlyInvestment) || 0,
                risk_free_rate: hasRiskFree ? (parseFloat(riskFreeRate) || 0) / 100 : 0,
                max_buy_multiplier: parseFloat(maxBuyMultiplier),
                sell_threshold: parseFloat(sellThreshold) / 100,
                min_weight: parseFloat(minWeight) / 100,
                max_weight: parseFloat(maxWeight) / 100,
                strategy_mode: strategyMode,
                kelly_fraction: (() => {
                    const value = parseFloat(kellyFraction);
                    return (Number.isNaN(value) ? 50 : value) / 100;
                })(),
                estimation_window: (() => {
                    const value = parseInt(estimationWindow, 10);
                    return Number.isNaN(value) ? 36 : value;
                })(),
                minimum_cash_reserve: (() => {
                    const value = parseFloat(minimumCashReserve);
                    return Number.isNaN(value) ? 0 : value;
                })(),
                enable_cvar_constraint: enableCvarConstraint,
                cvar_confidence: (() => {
                    const value = parseFloat(cvarConfidence);
                    return (Number.isNaN(value) ? 95 : value) / 100;
                })(),
                cvar_limit: (() => {
                    const value = parseFloat(cvarLimit);
                    return (Number.isNaN(value) ? 8 : value) / 100;
                })(),
                enable_drawdown_constraint: enableDrawdownConstraint,
                max_drawdown_limit: (() => {
                    const value = parseFloat(maxDrawdownLimit);
                    return (Number.isNaN(value) ? 20 : value) / 100;
                })(),
                buy_fee: Object.entries(fundBuyFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                sell_fee: Object.entries(fundSellFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                ma_window: parseInt(maWindow)
            };

            const response = await fetch('/api/current_recommendation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error((await response.json()).detail);
            setRecommendationResult(await response.json());
        } catch (err) {
            console.error("Failed to get recommendation", err);
        } finally {
            setLoading((prev) => ({ ...prev, recommendation: false }));
        }
    };

    const handleStrategySubmit = async () => {
        if (!selectedPoint) return;
        if (!monthlyInvestment) {
            setBudgetError(t('monthly_budget_required'));
            return;
        }
        setBudgetError('');
        setStrategyResult(null);
        setRecommendationResult(null);
        await Promise.all([runBacktests(selectedPoint.weights), getRecommendation()]);
    };

    const toggleBacktestNote = () => {
        if (backtestNotePinned) {
            setBacktestNotePinned(false);
            setShowBacktestNote(false);
            return;
        }
        setBacktestNotePinned(true);
        setShowBacktestNote(true);
    };


    const onChartClick = (params) => {
        const [chartRisk, chartReturn, weights] = params.data;
        setSelectedPoint({ risk: chartRisk, return: chartReturn, weights });
        setStrategyResult(null);

        // Auto-tune parameters based on risk/return profile
        // Find relative position in the frontier
        if (analysisResult && analysisResult.efficient_frontier) {
            const frontier = analysisResult.efficient_frontier;
            const risks = frontier.map(p => p.risk);
            const minRisk = Math.min(...risks);
            const maxRisk = Math.max(...risks);

            let newMinWeight = 30;
            let newMaxWeight = 80;

            if (maxRisk > minRisk) {
                const riskLevel = (chartRisk - minRisk) / (maxRisk - minRisk); // 0 to 1
                newMinWeight = 40 + (riskLevel * 50); // 40 -> 90
                newMaxWeight = 80 + (riskLevel * 20); // 80 -> 100
            }

            // Round to nearest 5
            newMinWeight = Math.round(newMinWeight / 5) * 5;
            newMaxWeight = Math.round(newMaxWeight / 5) * 5;

            setMinWeight(newMinWeight);
            setMaxWeight(newMaxWeight);
            localStorage.setItem('minWeight', newMinWeight);
            localStorage.setItem('maxWeight', newMaxWeight);
        }
    };

    const getFrontierOptions = () => {
        if (!analysisResult) return {};

        const frontierData = analysisResult.efficient_frontier.map(p => [
            p.risk,
            p.return,
            p.weights
        ]);
        const xName = t('theoretical_vol');
        const yName = t('expected_return');
        const titleSuffix = t('title_suffix_theory');

        return {
            backgroundColor: 'transparent',
            textStyle: { color: '#F8FAFC' },
            title: { text: `${t('efficient_frontier')} ${titleSuffix}`, left: 'center', textStyle: { fontSize: 16, color: '#F8FAFC' } },
            tooltip: {
                formatter: (p) => {
                    const risk = (p.data[0] * 100).toFixed(2);
                    const ret = (p.data[1] * 100).toFixed(2);
                    return `<b>${t('tooltip_theory_title')}</b><br/>${t('tooltip_expected_return')}: ${ret}%<br/>${t('tooltip_expected_risk')}: ${risk}%`;
                }
            },
            xAxis: {
                type: 'value',
                name: xName,
                axisLabel: { formatter: (v) => `${(v * 100).toFixed(2)}%`, color: '#94A3B8' },
                splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
                min: 'dataMin'
            },
            yAxis: {
                type: 'value',
                name: yName,
                axisLabel: { formatter: (v) => `${(v * 100).toFixed(1)}%`, color: '#94A3B8' },
                splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } },
                min: 'dataMin'
            },
            series: [{ type: 'scatter', data: frontierData, symbolSize: 10, itemStyle: { color: '#3B82F6' } }]
        };
    };

    const getStrategyChartOptions = (strategyType) => {
        if (!strategyResult || !strategyResult[strategyType]) return {};
        const attributionData = strategyResult[strategyType].attribution;
        if (!attributionData) return {};

        const dates = Object.keys(attributionData).sort();
        if (dates.length === 0) return {};

        const firstDateData = attributionData[dates[0]];
        if (!firstDateData) return {};

        const assetCodes = Object.keys(firstDateData);

        const getAssetName = (code) => {
            if (analysisResult && analysisResult.fund_names && analysisResult.fund_names[code]) {
                return analysisResult.fund_names[code];
            }
            return code;
        };

        const series = assetCodes.map(code => ({
            name: getAssetName(code),
            type: 'line',
            stack: 'Total',
            areaStyle: { opacity: 0.3 },
            emphasis: { focus: 'series' },
            data: dates.map(date => attributionData[date][code])
        }));

        const titleMap = {
            'lump_sum': t('strat_lump_sum'),
            'dca': t('strat_dca'),
            'ideal_kelly_dca': t('strat_ideal_kelly'),
            'actual_kelly_dca': t('strat_actual_kelly')
        };
        return {
            backgroundColor: 'transparent',
            textStyle: { color: '#F8FAFC' },
            title: { text: titleMap[strategyType] || 'Strategy Attribution', left: 'center', textStyle: { color: '#F8FAFC' } },
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'cross', label: { backgroundColor: '#6a7985' } },
                formatter: (params) => {
                    if (!params || params.length === 0) return '';
                    let result = `<div style="font-weight:bold;margin-bottom:8px;">${params[0].axisValue}</div>`;
                    params.forEach(item => {
                        const value = formatMoney(item.value);
                        result += `<div style="display:flex;justify-content:space-between;gap:16px;"><span>${item.marker} ${item.seriesName}</span><span style="font-weight:bold;">¥${value}</span></div>`;
                    });
                    return result;
                }
            },
            legend: { data: assetCodes.map(code => getAssetName(code)), top: 30, type: 'scroll', textStyle: { color: '#94A3B8' } },
            grid: { top: 70, left: '3%', right: '4%', bottom: '3%', containLabel: true },
            xAxis: { type: 'category', boundaryGap: false, data: dates, axisLabel: { color: '#94A3B8' } },
            yAxis: {
                type: 'value',
                axisLabel: {
                    formatter: (value) => `¥${formatMoney(value)}`,
                    color: '#94A3B8'
                },
                splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
            },
            series: series
        };
    };



    return (
        <div className="portfolio-optimizer">
            <form onSubmit={handleAnalysisSubmit} className="main-form">
                <div className="app-main">
                    <div className="half-width p-0">
                        <div className="dashboard-card h-full">
                            <div className="card-header">
                                <h3 className="card-title"><Wallet size={20} className="card-icon" /> {t('asset_config_title')}</h3>
                            </div>

                            <div className="form-group">
                                <label className="form-label" htmlFor="fundCodeInput">{t('add_fund_label')}</label>
                                <div className="flex gap-2">
                                    <input
                                        type="text"
                                        id="fundCodeInput"
                                        className="form-input"
                                        value={currentInput}
                                        onChange={(e) => setCurrentInput(e.target.value)}
                                        onKeyPress={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddFundCode(); } }}
                                        placeholder={t('input_placeholder')}
                                        style={{ flex: 1 }}
                                    />
                                    <button type="button" className="btn btn-primary" onClick={handleAddFundCode}>
                                        <Plus size={16} /> {t('add_btn')}
                                    </button>
                                </div>
                            </div>

                            <div className="form-group">
                                <button type="button" className="btn btn-secondary w-full" onClick={handleAddRiskFree} disabled={hasRiskFree}>
                                    {t('add_risk_free_btn')}
                                </button>
                            </div>

                            {(fundCodes.length > 0 || hasRiskFree) && <div className="border-t border-glass my-4"></div>}

                            {hasRiskFree && (
                                <div className="risk-free-row">
                                    <span className="risk-free-label">{t('risk_free_asset')}</span>
                                    <span className="text-xs text-slate-400">{t('annual_return')}</span>
                                    <input type="number" className="form-input risk-free-input" value={riskFreeRate} onChange={(e) => handleRiskFreeRateChange(e.target.value)} placeholder="%" />
                                    <button type="button" className="icon-btn" onClick={() => handleRemoveAsset('RiskFree')}><X size={16} /></button>
                                </div>
                            )}

                            <div className="space-y-3 max-h-[400px] overflow-y-auto pr-2">
                                {fundCodes.length > 0 && (
                                    <>
                                        <>
                                            <div className="asset-list-header">
                                                <div>{t('header_fund')}</div>
                                                <div>{t('header_buy')}</div>
                                                <div>{t('header_sell')}</div>
                                                <div>{t('header_manage')}</div>
                                                <div></div>
                                            </div>
                                            {fundCodes.map(code => (
                                                <div key={code} className="asset-item-row">
                                                    <span className="asset-name" title={analysisResult?.fund_names[code] || code}>{analysisResult?.fund_names[code] || code}</span>
                                                    <input type="number" step="0.01" className="asset-input-small" value={fundBuyFees[code] || ''} onChange={(e) => handleBuyFeeChange(code, e.target.value)} placeholder="0.15" />
                                                    <input type="number" step="0.01" className="asset-input-small" value={fundSellFees[code] || ''} onChange={(e) => handleSellFeeChange(code, e.target.value)} placeholder="0.5" />
                                                    <input type="number" step="0.01" className="asset-input-small" value={fundFees[code] || ''} onChange={(e) => handleFeeChange(code, e.target.value)} placeholder="0.6" />
                                                    <button type="button" className="icon-btn" onClick={() => handleRemoveAsset(code)}><X size={16} /></button>
                                                </div>
                                            ))}
                                        </>
                                    </>
                                )}
                            </div>
                        </div>
                    </div>

                    <div className="half-width p-0">
                        <div className="dashboard-card mb-6">
                            <div className="card-header">
                                <h3 className="card-title"><Calendar size={20} className="card-icon" /> {t('backtest_title')}</h3>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="form-group">
                                    <label className="form-label">{t('start_date')}</label>
                                    <input type="date" className="form-input" value={startDate} onChange={(e) => { setStartDate(e.target.value); localStorage.setItem('startDate', e.target.value); }} />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">{t('end_date')}</label>
                                    <input type="date" className="form-input" value={endDate} onChange={(e) => { setEndDate(e.target.value); localStorage.setItem('endDate', e.target.value); }} />
                                </div>
                            </div>
                            <div className="flex gap-2 mt-4">
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(1)}>{t('last_1_year')}</button>
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(3)}>{t('last_3_years')}</button>
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(5)}>{t('last_5_years')}</button>
                            </div>
                        </div>
                        <button type="submit" className="btn btn-primary w-full py-4 text-lg shadow-lg-glow" disabled={loading.analysis || (fundCodes.length === 0 && !hasRiskFree)}>
                            {loading.analysis ? t('analyzing') : t('analyze_btn')} <ArrowRight size={20} />
                        </button>
                    </div>
                </div>
            </form>

            {error && <div role="alert" className="m-8 p-4 bg-red-500/10 border border-red-500/20 text-red-400 rounded-lg flex items-center gap-2"><div className="w-2 h-2 rounded-full bg-red-500"></div>{error}</div>}

            {analysisResult && (
                <div className="app-main pt-0">
                    <div className="full-width">
                        <div className="dashboard-card">
                            <div className="card-header justify-between">
                                <h3 className="card-title"><TrendingUp size={20} className="card-icon" /> {t('step_2_title')}</h3>
                            </div>

                            {analysisResult.warnings?.length > 0 && (
                                <div className="mb-4 p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                                    {analysisResult.warnings.map((w, i) => <p key={i} className="text-yellow-500 text-sm flex items-center gap-2"><Info size={14} /> {w}</p>)}
                                </div>
                            )}

                            <ReactECharts option={getFrontierOptions()} style={{ height: 400 }} onEvents={{ 'click': onChartClick }} />
                            <p className="text-center text-slate-400 text-sm mt-4">{t('chart_hint')}</p>
                        </div>
                    </div>

                    {selectedPoint && (
                        <div className="full-width">
                            <div className="dashboard-card">
                                <div className="card-header">
                                    <h3 className="card-title"><Settings size={20} className="card-icon" /> {t('strategy_title')}</h3>
                                </div>
                                <div className="grid grid-cols-12 gap-8">
                                    <div className="col-span-4">
                                        <div className="p-4 bg-slate-900/50 rounded-lg mb-6">
                                            <h4 className="text-slate-400 text-sm uppercase mb-4">{t('selected_metrics')}</h4>
                                            <div className="flex justify-between mb-2">
                                                <span>{t('expected_return')}</span>
                                                <span className="text-emerald-400 font-mono font-bold">{(selectedPoint.return * 100).toFixed(2)}%</span>
                                            </div>
                                            <div className="flex justify-between">
                                                <span>{t('expected_risk')}</span>
                                                <span className="text-amber-400 font-mono font-bold">{(selectedPoint.risk * 100).toFixed(2)}%</span>
                                            </div>
                                        </div>

                                        <table className="data-table">
                                            <thead><tr><th>{t('header_fund')}</th><th>{t('target_weight')}</th></tr></thead>
                                            <tbody>
                                                {Object.entries(selectedPoint.weights).map(([code, weight]) => (
                                                    <tr key={code}>
                                                        <td className="text-sm">{analysisResult.fund_names[code]} <span className="text-slate-500 text-xs">({code})</span></td>
                                                        <td className="font-mono text-emerald-400">{(weight * 100).toFixed(2)}%</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                    </div>

                                    <div className="col-span-8">
                                        <h5 className="text-lg font-medium mb-4 flex items-center gap-2"><DollarSign size={18} className="text-sky-400" /> {t('current_holdings_monthly')}</h5>

                                        <div className="grid grid-cols-2 gap-6 mb-6">
                                            <div>
                                                <label className="form-label">{t('current_cash')}</label>
                                                <input type="number" className="form-input" value={currentCash} onChange={(e) => { setCurrentCash(e.target.value); localStorage.setItem('currentCash', e.target.value); }} placeholder="0" />
                                            </div>
                                            <div>
                                                <label className="form-label">{t('monthly_budget')}</label>
                                                <input type="number" className="form-input" value={monthlyInvestment} onChange={(e) => { setMonthlyInvestment(e.target.value); localStorage.setItem('monthlyInvestment', e.target.value); }} placeholder={t('placeholder_money')} />
                                            </div>
                                        </div>

                                        <div className="mb-6">
                                            <table className="data-table">
                                                <thead><tr><th>{t('current_holding_val')}</th><th>{t('input_amount')}</th></tr></thead>
                                                <tbody>
                                                    {Object.entries(selectedPoint.weights).map(([code, weight]) => (
                                                        <tr key={code}>
                                                            <td>{analysisResult.fund_names[code]}</td>
                                                            <td><input type="number" className="form-input py-1 text-sm w-32" value={initialHoldings[code] || ''} onChange={(e) => handleHoldingChange(code, e.target.value)} placeholder="0" /></td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>

                                        <button className="text-link-btn mb-4" onClick={() => setShowAdvancedParams(!showAdvancedParams)}>
                                            <Settings size={14} />
                                            {showAdvancedParams ? t('collapse_advanced') : t('expand_advanced')}
                                        </button>

                                        {showAdvancedParams && (
                                            <div className="p-4 bg-slate-900/50 rounded-lg border border-slate-700/50 mb-6 grid grid-cols-2 gap-4">
                                                <div className="form-group col-span-2">
                                                    <label className="form-label text-xs">{t('strategy_mode')}</label>
                                                    <select
                                                        className="form-input text-sm"
                                                        value={strategyMode}
                                                        onChange={(e) => {
                                                            setStrategyMode(e.target.value);
                                                            localStorage.setItem('strategyMode', e.target.value);
                                                        }}
                                                    >
                                                        <option value="optimized_kelly">{t('mode_optimized_kelly')}</option>
                                                        <option value="legacy_linear">{t('mode_legacy_linear')}</option>
                                                    </select>
                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('strategy_mode_help')}</p>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label text-xs">{t('max_buy_mult')}</label>
                                                    <input className="form-input text-sm" type="number" step="0.1" value={maxBuyMultiplier} onChange={(e) => { setMaxBuyMultiplier(e.target.value); localStorage.setItem('maxBuyMultiplier', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('max_buy_mult_help')}</p>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label text-xs">{t('sell_threshold')}</label>
                                                    <input className="form-input text-sm" type="number" step="0.5" value={sellThreshold} onChange={(e) => { setSellThreshold(e.target.value); localStorage.setItem('sellThreshold', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('sell_threshold_help')}</p>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label text-xs">{t('min_equity_ratio')}</label>
                                                    <input className="form-input text-sm" type="number" step="5" value={minWeight} onChange={(e) => { setMinWeight(e.target.value); localStorage.setItem('minWeight', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('min_equity_ratio_help')}</p>
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label text-xs">{t('max_equity_ratio')}</label>
                                                    <input className="form-input text-sm" type="number" step="5" value={maxWeight} onChange={(e) => { setMaxWeight(e.target.value); localStorage.setItem('maxWeight', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('max_equity_ratio_help')}</p>
                                                </div>
                                                {strategyMode === 'optimized_kelly' ? (
                                                    <>
                                                        <div className="form-group">
                                                            <label className="form-label text-xs">{t('kelly_fraction')}</label>
                                                            <input className="form-input text-sm" type="number" step="5" min="1" max="100" value={kellyFraction} onChange={(e) => { setKellyFraction(e.target.value); localStorage.setItem('kellyFraction', e.target.value); }} />
                                                            <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('kelly_fraction_help')}</p>
                                                        </div>
                                                        <div className="form-group">
                                                            <label className="form-label text-xs">{t('estimation_window')}</label>
                                                            <input className="form-input text-sm" type="number" step="1" min="6" value={estimationWindow} onChange={(e) => { setEstimationWindow(e.target.value); localStorage.setItem('estimationWindow', e.target.value); }} />
                                                            <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('estimation_window_help')}</p>
                                                        </div>
                                                        <div className="form-group col-span-2">
                                                            <label className="form-label text-xs">{t('minimum_cash_reserve')}</label>
                                                            <input className="form-input text-sm" type="number" step="100" min="0" value={minimumCashReserve} onChange={(e) => { setMinimumCashReserve(e.target.value); localStorage.setItem('minimumCashReserve', e.target.value); }} />
                                                            <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('minimum_cash_reserve_help')}</p>
                                                        </div>
                                                        <div className="form-group col-span-2">
                                                            <label className="form-label text-xs">{t('constraint_priority_note')}</label>
                                                        </div>
                                                        <div className="form-group col-span-2">
                                                            <label className="form-label text-xs flex items-center gap-2">
                                                                <input
                                                                    type="checkbox"
                                                                    checked={enableCvarConstraint}
                                                                    onChange={(e) => {
                                                                        setEnableCvarConstraint(e.target.checked);
                                                                        localStorage.setItem('enableCvarConstraint', JSON.stringify(e.target.checked));
                                                                    }}
                                                                />
                                                                {t('enable_cvar_constraint')}
                                                            </label>
                                                            <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('enable_cvar_constraint_help')}</p>
                                                        </div>
                                                        {enableCvarConstraint && (
                                                            <>
                                                                <div className="form-group">
                                                                    <label className="form-label text-xs">{t('cvar_confidence')}</label>
                                                                    <input className="form-input text-sm" type="number" step="1" min="51" max="99.8" value={cvarConfidence} onChange={(e) => { setCvarConfidence(e.target.value); localStorage.setItem('cvarConfidence', e.target.value); }} />
                                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('cvar_confidence_help')}</p>
                                                                </div>
                                                                <div className="form-group">
                                                                    <label className="form-label text-xs">{t('cvar_limit')}</label>
                                                                    <input className="form-input text-sm" type="number" step="0.5" min="0.1" max="99" value={cvarLimit} onChange={(e) => { setCvarLimit(e.target.value); localStorage.setItem('cvarLimit', e.target.value); }} />
                                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('cvar_limit_help')}</p>
                                                                </div>
                                                            </>
                                                        )}
                                                        <div className="form-group col-span-2">
                                                            <label className="form-label text-xs flex items-center gap-2">
                                                                <input
                                                                    type="checkbox"
                                                                    checked={enableDrawdownConstraint}
                                                                    onChange={(e) => {
                                                                        setEnableDrawdownConstraint(e.target.checked);
                                                                        localStorage.setItem('enableDrawdownConstraint', JSON.stringify(e.target.checked));
                                                                    }}
                                                                />
                                                                {t('enable_drawdown_constraint')}
                                                            </label>
                                                            <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('enable_drawdown_constraint_help')}</p>
                                                        </div>
                                                        {enableDrawdownConstraint && (
                                                            <div className="form-group col-span-2">
                                                                <label className="form-label text-xs">{t('max_drawdown_limit')}</label>
                                                                <input className="form-input text-sm" type="number" step="1" min="1" max="99" value={maxDrawdownLimit} onChange={(e) => { setMaxDrawdownLimit(e.target.value); localStorage.setItem('maxDrawdownLimit', e.target.value); }} />
                                                                <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('max_drawdown_limit_help')}</p>
                                                            </div>
                                                        )}
                                                    </>
                                                ) : (
                                                    <div className="form-group col-span-2">
                                                        <label className="form-label text-xs">{t('ma_window')}</label>
                                                        <input className="form-input text-sm" type="number" step="1" value={maWindow} onChange={(e) => { setMaWindow(e.target.value); localStorage.setItem('maWindow', e.target.value); }} />
                                                        <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('ma_window_help')}</p>
                                                    </div>
                                                )}
                                            </div>
                                        )}

                                        <button className="btn btn-primary w-full" onClick={handleStrategySubmit} disabled={loading.strategy || !selectedPoint}>
                                            {loading.strategy ? t('analyzing') : t('start_analysis_btn')}
                                        </button>
                                        {budgetError && (
                                            <div className="mt-2 p-2 bg-red-500/10 border border-red-500/30 rounded text-red-400 text-sm flex items-center gap-2">
                                                <span className="w-2 h-2 rounded-full bg-red-500"></span>
                                                {budgetError}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {strategyResult && (
                        <div className="full-width">
                            <div className="dashboard-card mb-6">
                                <div className="card-header">
                                    <h3 className="card-title">{t('backtest_compare')}</h3>
                                    <div
                                        className="backtest-note-tooltip"
                                        ref={backtestNoteRef}
                                        onMouseEnter={() => setShowBacktestNote(true)}
                                        onMouseLeave={() => {
                                            if (!backtestNotePinned) setShowBacktestNote(false);
                                        }}
                                    >
                                        <button
                                            type="button"
                                            className={`backtest-note-trigger ${showBacktestNote ? 'active' : ''}`}
                                            onClick={toggleBacktestNote}
                                            aria-label={t('backtest_note_toggle')}
                                            aria-expanded={showBacktestNote}
                                            title={t('backtest_note_title')}
                                        >
                                            <Info size={14} />
                                        </button>
                                        <div className={`backtest-note-popover ${showBacktestNote ? 'show' : ''}`} role="note">
                                            <div className="backtest-note-popover-title">{t('backtest_note_title')}</div>
                                            <div className="backtest-note-line">1. {t('backtest_note_lump_sum')}</div>
                                            <div className="backtest-note-line">2. {t('backtest_note_dca')}</div>
                                            <div className="backtest-note-line">3. {t('backtest_note_kelly_theory')}</div>
                                            {strategyResult.actual_kelly_dca && (
                                                <div className="backtest-note-line">4. {t('backtest_note_kelly_actual')}</div>
                                            )}
                                        </div>
                                    </div>
                                </div>
                                <div className="stat-grid">
                                    <div className="stat-item">
                                        <div className="stat-label">{t('lump_sum_annual')}</div>
                                        <div className="stat-value">{(strategyResult.lump_sum.annualized_return * 100).toFixed(2)}%</div>
                                        <div className="text-xs text-slate-500 mt-1">{t('max_drawdown')}: {formatDD(strategyResult.lump_sum, 'max_drawdown_value', 'max_drawdown')}</div>
                                    </div>
                                    <div className="stat-item">
                                        <div className="stat-label">{t('dca_annual')}</div>
                                        <div className="stat-value">{(strategyResult.dca.annualized_return * 100).toFixed(2)}%</div>
                                        <div className="text-xs text-slate-500 mt-1">{t('max_drawdown')}: {formatDD(strategyResult.dca, 'max_drawdown_value', 'max_drawdown')}</div>
                                    </div>
                                    <div className="stat-item border-l-4 border-emerald-500 bg-emerald-900/10">
                                        <div className="stat-label text-emerald-400">{t('kelly_theory')}</div>
                                        <div className="stat-value text-emerald-400">{((strategyResult.ideal_kelly_dca || strategyResult.kelly_dca).annualized_return * 100).toFixed(2)}%</div>
                                        <div className="text-xs text-emerald-600 mt-1">{t('max_drawdown')}: {formatDD(strategyResult.ideal_kelly_dca || strategyResult.kelly_dca, 'max_drawdown_value', 'max_drawdown')}</div>
                                    </div>
                                    {strategyResult.actual_kelly_dca && (
                                        <div className="stat-item border-l-4 border-amber-500 bg-amber-900/10">
                                            <div className="stat-label text-amber-400">{t('kelly_actual')}</div>
                                            <div className="stat-value text-amber-400">{(strategyResult.actual_kelly_dca.annualized_return * 100).toFixed(2)}%</div>
                                            <div className="text-xs text-amber-600 mt-1">{t('max_drawdown')}: {formatDD(strategyResult.actual_kelly_dca, 'max_drawdown_value', 'max_drawdown')}</div>
                                        </div>
                                    )}
                                </div>

                                <div className="grid grid-cols-2 gap-4 mt-6">
                                    <div><ReactECharts option={getStrategyChartOptions('ideal_kelly_dca')} style={{ height: 300 }} /></div>
                                    {strategyResult.actual_kelly_dca && (
                                        <div><ReactECharts option={getStrategyChartOptions('actual_kelly_dca')} style={{ height: 300 }} /></div>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}

                    {recommendationResult && (
                        <div className="full-width">
                            <div className="recommendation-card">
                                <div className="card-header">
                                    <h3 className="card-title text-xl text-emerald-400"><TrendingUp size={24} /> {t('recommend_title')}</h3>
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
                                        <div className="recommendation-stat-value">{(recommendationResult.target_equity_ratio * 100).toFixed(0)}%</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">{t('suggested_monthly')}</div>
                                        <div className="recommendation-stat-value text-white">¥{recommendationResult.recommended_monthly_investment.toFixed(2)}</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">{t('monthly_budget_label')}</div>
                                        <div className="recommendation-stat-value text-slate-400">¥{recommendationResult.monthly_budget}</div>
                                    </div>
                                </div>

                                {recommendationResult.fund_advice && (
                                    <table className="recommendation-table">
                                        <thead>
                                            <tr>
                                                <th>{t('table_fund')}</th>
                                                <th>{t('table_action')}</th>
                                                <th>{t('table_amount')}</th>
                                                <th>{t('table_target')}</th>
                                                <th>{t('table_reason')}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {recommendationResult.fund_advice.map(advice => {
                                                const actionMeta = getAdviceActionMeta(advice.action, t);
                                                return (
                                                <tr key={advice.code}>
                                                    <td>{advice.name}</td>
                                                    <td>
                                                        <span className={`action-badge ${actionMeta.badgeClass}`}>
                                                            {actionMeta.label}
                                                        </span>
                                                    </td>
                                                    <td className="font-mono">¥{advice.amount.toFixed(2)}</td>
                                                    <td className="font-mono">¥{advice.target_holding?.toFixed(2) ?? '--'}</td>
                                                    <td className="text-slate-500 text-xs">{advice.reason}</td>
                                                </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export default PortfolioOptimizer;
