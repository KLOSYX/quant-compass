import React, { useState, useEffect, useRef } from 'react';
import { useLanguage } from './LanguageContext';
import ReactECharts from 'echarts-for-react';
import { Plus, X, ArrowRight, Settings, Info, TrendingUp, DollarSign, Wallet, Calendar, Download, RotateCcw, ChevronDown } from 'lucide-react';
import AssetDiagnosticsPanel from './AssetDiagnosticsPanel';
import { downloadPortfolioReport } from './exportPortfolioReport';

const getISODate = (date) => date.toISOString().split('T')[0];
const formatDD = (obj, key, fallbackKey) => {
    const val = obj?.[key] ?? obj?.[fallbackKey];
    if (val === undefined || val === null) return '--';
    return `${(val * 100).toFixed(2)}%`;
};
const formatPercentValue = (value, digits = 2) => (
    value === undefined || value === null || !Number.isFinite(Number(value))
        ? '--'
        : `${(Number(value) * 100).toFixed(digits)}%`
);
const formatRatio = (value, digits = 2) => (
    value === undefined || value === null || !Number.isFinite(Number(value))
        ? '--'
        : Number(value).toFixed(digits)
);

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

export const sanitizeLegacyHoldings = (holdings = {}) => Object.fromEntries(
    Object.entries(holdings).filter(([code]) => code !== 'RiskFree')
);
export const ASSET_CATEGORY_OPTIONS = ['equity', 'bond', 'commodity', 'gold', 'money_market', 'cash_equivalent', 'other'];
export const buildAssetCategoriesPayload = (fundCodes, categories = {}) => Object.fromEntries(
    fundCodes.map(code => [code, ASSET_CATEGORY_OPTIONS.includes(categories[code]) ? categories[code] : 'other'])
);
export const buildSubstituteForPayload = (fundCodes, relationships = {}) => {
    const currentCodes = new Set(fundCodes);
    return Object.fromEntries(
        fundCodes
            .map(code => [code, String(relationships[code] || '').trim()])
            .filter(([code, primary]) => primary && primary !== code && currentCodes.has(primary))
    );
};
export const getRecommendedFrontierPoint = (analysis) => {
    const index = analysis?.recommended_point_index;
    if (!Number.isInteger(index) || index < 0) return null;
    return analysis?.efficient_frontier?.[index] || null;
};
export const getRecommendationEvidence = (analysis) => {
    const point = getRecommendedFrontierPoint(analysis);
    const selection = analysis?.recommended_point_selection;
    if (!point || !selection) return null;
    return {
        point,
        eligibleCount: selection.eligible_count ?? 0,
        totalCount: selection.total_count ?? analysis?.efficient_frontier?.length ?? 0,
        confidence: selection.confidence || 'none'
    };
};
function PortfolioOptimizer() {
    const { t, language } = useLanguage();
    const [fundCodes, setFundCodes] = useState([]);
    const [fundFees, setFundFees] = useState({});
    const [fundBuyFees, setFundBuyFees] = useState({});
    const [fundSellFees, setFundSellFees] = useState({});
    const [fundInvestmentLimits, setFundInvestmentLimits] = useState({});
    const [fundSubstituteFor, setFundSubstituteFor] = useState(() => {
        try {
            return JSON.parse(localStorage.getItem('fundSubstituteFor') || '{}');
        } catch (_) {
            return {};
        }
    });
    const [fundNames, setFundNames] = useState(() => {
        try {
            return JSON.parse(localStorage.getItem('fundNames') || '{}');
        } catch (_) {
            return {};
        }
    });
    const [fundAssetCategories, setFundAssetCategories] = useState(() => {
        try {
            return JSON.parse(localStorage.getItem('fundAssetCategories') || '{}');
        } catch (_) {
            return {};
        }
    });
    const [currentInput, setCurrentInput] = useState('');
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
    const [initialHoldings, setInitialHoldings] = useState(() => {
        let stored = {};
        try {
            stored = JSON.parse(localStorage.getItem('initialHoldings') || '{}');
        } catch (_) {
            stored = {};
        }
        const sanitized = sanitizeLegacyHoldings(stored);
        if (Object.prototype.hasOwnProperty.call(stored, 'RiskFree')) {
            localStorage.setItem('initialHoldings', JSON.stringify(sanitized));
        }
        return sanitized;
    });
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
    const [riskHorizonDays, setRiskHorizonDays] = useState(() => getStoredNumber('riskHorizonDays', 21));
    const [enableDrawdownConstraint, setEnableDrawdownConstraint] = useState(() => {
        const saved = localStorage.getItem('enableDrawdownConstraint');
        return saved === null ? true : JSON.parse(saved);
    });
    const [maxDrawdownLimit, setMaxDrawdownLimit] = useState(() => getStoredPercentWithLegacyRatioSupport('maxDrawdownLimit', 20)); // pct
    const [minWeight, setMinWeight] = useState(() => getStoredPercentWithLegacyRatioSupport('minWeight', 30)); // pct
    const [maxWeight, setMaxWeight] = useState(() => getStoredPercentWithLegacyRatioSupport('maxWeight', 80)); // pct
    const [maWindow, setMaWindow] = useState(() => getStoredNumber('maWindow', 12));
    const [plannedPurchaseDays, setPlannedPurchaseDays] = useState(
        () => getStoredNumber('plannedPurchaseDays', 1)
    );
    const [strategyResult, setStrategyResult] = useState(null);
    const [recommendationResult, setRecommendationResult] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState({ analysis: false, strategy: false, recommendation: false });
    const [budgetError, setBudgetError] = useState('');
    const [showPortfolioDetails, setShowPortfolioDetails] = useState(false);
    const [showBacktestNote, setShowBacktestNote] = useState(false);
    const [backtestNotePinned, setBacktestNotePinned] = useState(false);
    const backtestNoteRef = useRef(null);

    const clearAnalysisOutputs = () => {
        setAnalysisResult(null);
        setSelectedPoint(null);
        setStrategyResult(null);
        setRecommendationResult(null);
        setShowPortfolioDetails(false);
    };

    const handleExport = () => downloadPortfolioReport({
        language, selectedPoint, fundNames, initialHoldings, currentCash, recommendationResult
    });

    useEffect(() => {
        const savedFundCodes = localStorage.getItem('fundCodes');
        const savedFundFees = localStorage.getItem('fundFees');
        const savedFundBuyFees = localStorage.getItem('fundBuyFees');
        const savedFundSellFees = localStorage.getItem('fundSellFees');
        const savedFundInvestmentLimits = localStorage.getItem('fundInvestmentLimits');
        if (savedFundCodes) setFundCodes(JSON.parse(savedFundCodes));
        if (savedFundFees) setFundFees(JSON.parse(savedFundFees));
        if (savedFundBuyFees) setFundBuyFees(JSON.parse(savedFundBuyFees));
        if (savedFundSellFees) setFundSellFees(JSON.parse(savedFundSellFees));
        if (savedFundInvestmentLimits) setFundInvestmentLimits(JSON.parse(savedFundInvestmentLimits));
    }, []);

    useEffect(() => {
        const missingCodes = fundCodes.filter(code => !fundNames[code]);
        if (missingCodes.length === 0) return undefined;
        let cancelled = false;
        fetch('/api/fund_names', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ fund_codes: missingCodes })
        })
            .then(async response => {
                if (!response.ok) throw new Error((await response.json()).detail);
                return response.json();
            })
            .then(result => {
                if (cancelled) return;
                setFundNames(previous => {
                    const merged = { ...previous, ...(result.fund_names || {}) };
                    localStorage.setItem('fundNames', JSON.stringify(merged));
                    return merged;
                });
            })
            .catch(() => {
                // Name lookup is helpful metadata and must not block portfolio setup.
            });
        return () => { cancelled = true; };
    }, [fundCodes, fundNames]);

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
            const newFundInvestmentLimits = { ...fundInvestmentLimits, [currentInput.trim()]: { daily_limit: '', monthly_limit: '' } };
            const newFundAssetCategories = { ...fundAssetCategories, [currentInput.trim()]: 'other' };
            clearAnalysisOutputs();
            setFundCodes(newFundCodes);
            setFundFees(newFundFees);
            setFundInvestmentLimits(newFundInvestmentLimits);
            setFundAssetCategories(newFundAssetCategories);
            localStorage.setItem('fundCodes', JSON.stringify(newFundCodes));
            localStorage.setItem('fundFees', JSON.stringify(newFundFees));
            localStorage.setItem('fundInvestmentLimits', JSON.stringify(newFundInvestmentLimits));
            localStorage.setItem('fundAssetCategories', JSON.stringify(newFundAssetCategories));
            setCurrentInput('');
        }
    };

    const handleRemoveAsset = (codeToRemove) => {
        clearAnalysisOutputs();
        const newFundCodes = fundCodes.filter(code => code !== codeToRemove);
        const newFundFees = { ...fundFees };
        const newFundInvestmentLimits = { ...fundInvestmentLimits };
        const newFundAssetCategories = { ...fundAssetCategories };
        const newSubstituteFor = Object.fromEntries(
            Object.entries(fundSubstituteFor).filter(
                ([substitute, primary]) => substitute !== codeToRemove && primary !== codeToRemove
            )
        );
        delete newFundFees[codeToRemove];
        delete newFundInvestmentLimits[codeToRemove];
        delete newFundAssetCategories[codeToRemove];
        setFundCodes(newFundCodes);
        setFundFees(newFundFees);
        setFundInvestmentLimits(newFundInvestmentLimits);
        setFundAssetCategories(newFundAssetCategories);
        setFundSubstituteFor(newSubstituteFor);
        localStorage.setItem('fundCodes', JSON.stringify(newFundCodes));
        localStorage.setItem('fundFees', JSON.stringify(newFundFees));
        localStorage.setItem('fundInvestmentLimits', JSON.stringify(newFundInvestmentLimits));
        localStorage.setItem('fundAssetCategories', JSON.stringify(newFundAssetCategories));
        localStorage.setItem('fundSubstituteFor', JSON.stringify(newSubstituteFor));
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

    const handleInvestmentLimitChange = (code, field, value) => {
        const newLimits = {
            ...fundInvestmentLimits,
            [code]: {
                ...(fundInvestmentLimits[code] || {}),
                [field]: value
            }
        };
        setFundInvestmentLimits(newLimits);
        localStorage.setItem('fundInvestmentLimits', JSON.stringify(newLimits));
    };

    const handleAssetCategoryChange = (code, category) => {
        const newCategories = { ...fundAssetCategories, [code]: category };
        clearAnalysisOutputs();
        setFundAssetCategories(newCategories);
        localStorage.setItem('fundAssetCategories', JSON.stringify(newCategories));
    };

    const handleSubstituteForChange = (code, primary) => {
        const newRelationships = { ...fundSubstituteFor };
        if (primary) {
            newRelationships[code] = primary;
        } else {
            delete newRelationships[code];
        }
        clearAnalysisOutputs();
        setFundSubstituteFor(newRelationships);
        localStorage.setItem('fundSubstituteFor', JSON.stringify(newRelationships));
    };

    const buildFundInvestmentLimitsPayload = () => {
        return Object.entries(fundInvestmentLimits).reduce((acc, [code, limits]) => {
            const dailyLimit = parseFloat(limits?.daily_limit);
            const monthlyLimit = parseFloat(limits?.monthly_limit);
            const parsed = {};
            if (!Number.isNaN(dailyLimit) && dailyLimit >= 0) parsed.daily_limit = dailyLimit;
            if (!Number.isNaN(monthlyLimit) && monthlyLimit >= 0) parsed.monthly_limit = monthlyLimit;
            if (Object.keys(parsed).length > 0) acc[code] = parsed;
            return acc;
        }, {});
    };

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
        clearAnalysisOutputs();
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
            const parsedRiskHorizonDays = parseInt(riskHorizonDays, 10);
            const parsedMaxDrawdownLimit = parseFloat(maxDrawdownLimit);
            const parsedMinWeight = parseFloat(minWeight);
            const parsedMaxWeight = parseFloat(maxWeight);
            const parsedMaWindow = parseInt(maWindow, 10);

            const payload = {
                fund_codes: fundCodes,
                fund_fees: feesAsFloats,
                asset_categories: buildAssetCategoriesPayload(fundCodes, fundAssetCategories),
                substitute_for: buildSubstituteForPayload(fundCodes, fundSubstituteFor),
                planned_purchase_days: Number(plannedPurchaseDays) || 1,
                start_date: startDate,
                end_date: endDate,
                strategy_mode: strategyMode,
                kelly_fraction: (Number.isNaN(parsedKellyFraction) ? 50 : parsedKellyFraction) / 100,
                estimation_window: Number.isNaN(parsedEstimationWindow) ? 36 : parsedEstimationWindow,
                minimum_cash_reserve: Number.isNaN(parsedMinimumCashReserve) ? 0 : parsedMinimumCashReserve,
                enable_cvar_constraint: enableCvarConstraint,
                cvar_confidence: (Number.isNaN(parsedCvarConfidence) ? 95 : parsedCvarConfidence) / 100,
                cvar_limit: (Number.isNaN(parsedCvarLimit) ? 8 : parsedCvarLimit) / 100,
                risk_horizon_days: Number.isNaN(parsedRiskHorizonDays) ? 21 : parsedRiskHorizonDays,
                enable_drawdown_constraint: enableDrawdownConstraint,
                max_drawdown_limit: (Number.isNaN(parsedMaxDrawdownLimit) ? 20 : parsedMaxDrawdownLimit) / 100,
                min_weight: (Number.isNaN(parsedMinWeight) ? 30 : parsedMinWeight) / 100,
                max_weight: (Number.isNaN(parsedMaxWeight) ? 80 : parsedMaxWeight) / 100,
                fund_investment_limits: buildFundInvestmentLimitsPayload(),
                ma_window: Number.isNaN(parsedMaWindow) ? 12 : parsedMaWindow,
            };

            const response = await fetch('/api/analyze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error((await response.json()).detail);
            const result = await response.json();
            if (result.fund_names) {
                setFundNames(previous => {
                    const merged = { ...previous, ...result.fund_names };
                    localStorage.setItem('fundNames', JSON.stringify(merged));
                    return merged;
                });
            }
            setAnalysisResult(result);
            if (result.recommended_point_index !== null && result.recommended_point_index !== undefined) {
                setSelectedPoint(result.efficient_frontier[result.recommended_point_index] || null);
            }
            setShowPortfolioDetails(false);
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

            // ACTUAL: User's real holdings + separate cash balance
            const actualHoldings = Object.entries(sanitizeLegacyHoldings(initialHoldings)).reduce((acc, [code, val]) => {
                const v = parseFloat(val);
                if (v > 0) acc[code] = v;
                return acc;
            }, {});

            const basePayload = {
                fund_codes: fundCodes,
                weights,
                fund_fees: feesAsFloats,
                asset_categories: buildAssetCategoriesPayload(fundCodes, fundAssetCategories),
                substitute_for: buildSubstituteForPayload(fundCodes, fundSubstituteFor),
                planned_purchase_days: Number(plannedPurchaseDays) || 1,
                start_date: analysisResult.backtest_period.start_date,
                end_date: analysisResult.backtest_period.end_date,
                monthly_investment: parseFloat(monthlyInvestment),
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
                risk_horizon_days: (() => {
                    const value = parseInt(riskHorizonDays, 10);
                    return Number.isNaN(value) ? 21 : value;
                })(),
                enable_drawdown_constraint: enableDrawdownConstraint,
                max_drawdown_limit: (() => {
                    const value = parseFloat(maxDrawdownLimit);
                    return (Number.isNaN(value) ? 20 : value) / 100;
                })(),
                buy_fee: Object.entries(fundBuyFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                sell_fee: Object.entries(fundSellFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                fund_investment_limits: buildFundInvestmentLimitsPayload(),
                ma_window: parseInt(maWindow)
            };

            // Run BOTH backtests in parallel
            const [idealRes, actualRes] = await Promise.all([
                fetch('/api/backtest_strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...basePayload, initial_holdings: idealHoldings, initial_cash: 0, include_walk_forward: false }) }),
                fetch('/api/backtest_strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...basePayload, initial_holdings: actualHoldings, initial_cash: totalCash, include_walk_forward: true }) })
            ]);

            if (!idealRes.ok) throw new Error((await idealRes.json()).detail);
            if (!actualRes.ok) throw new Error((await actualRes.json()).detail);

            const idealData = await idealRes.json();
            const actualData = await actualRes.json();

            // Store both results - keep backward compatible structure
            setStrategyResult({
                ...idealData,
                ideal_kelly_dca: idealData.kelly_dca,
                actual_kelly_dca: actualData.kelly_dca,
                walk_forward: actualData.walk_forward
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
            const holdingsAsFloats = Object.entries(sanitizeLegacyHoldings(initialHoldings)).reduce((acc, [code, val]) => {
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
                asset_categories: buildAssetCategoriesPayload(fundCodes, fundAssetCategories),
                substitute_for: buildSubstituteForPayload(fundCodes, fundSubstituteFor),
                planned_purchase_days: Number(plannedPurchaseDays) || 1,
                weights: selectedPoint.weights,
                current_holdings: holdingsAsFloats,
                current_cash: parseFloat(currentCash) || 0,
                monthly_budget: parseFloat(monthlyInvestment) || 0,
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
                risk_horizon_days: (() => {
                    const value = parseInt(riskHorizonDays, 10);
                    return Number.isNaN(value) ? 21 : value;
                })(),
                enable_drawdown_constraint: enableDrawdownConstraint,
                max_drawdown_limit: (() => {
                    const value = parseFloat(maxDrawdownLimit);
                    return (Number.isNaN(value) ? 20 : value) / 100;
                })(),
                buy_fee: Object.entries(fundBuyFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                sell_fee: Object.entries(fundSellFees).reduce((acc, [k, v]) => { acc[k] = parseFloat(v) / 100 || 0; return acc; }, {}),
                fund_investment_limits: buildFundInvestmentLimitsPayload(),
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
        const selected = analysisResult?.efficient_frontier?.[params.dataIndex];
        if (!selected) return;
        const { risk: chartRisk } = selected;
        setSelectedPoint(selected);
        setStrategyResult(null);
        setRecommendationResult(null);

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

    const handleResetToRecommendedPoint = () => {
        const recommended = getRecommendedFrontierPoint(analysisResult);
        if (!recommended) return;
        setSelectedPoint(recommended);
        setStrategyResult(null);
        setRecommendationResult(null);
        setBudgetError('');
    };

    const getFrontierOptions = () => {
        if (!analysisResult) return {};

        const frontierData = analysisResult.efficient_frontier.map(p => [
            p.risk,
            p.return,
            p.weights,
            p.frontier_walk_forward_sharpe,
            p.frontier_walk_forward_annualized_return,
            p.frontier_walk_forward_max_drawdown,
            p.frontier_walk_forward_weight_stability,
            p.frontier_recommendation_eligible
        ]);
        const xName = t('theoretical_vol');
        const yName = t('expected_return');
        const titleSuffix = t('title_suffix_theory');

        return {
            backgroundColor: 'transparent',
            textStyle: { color: '#b1b5ab', fontFamily: 'Outfit, Microsoft YaHei, sans-serif' },
            title: {
                text: `${t('efficient_frontier')} ${titleSuffix}`,
                left: 'center',
                top: 8,
                textStyle: { fontSize: 15, fontWeight: 600, color: '#f3f0e8' }
            },
            tooltip: {
                formatter: (p) => {
                    const risk = (p.data[0] * 100).toFixed(2);
                    const ret = (p.data[1] * 100).toFixed(2);
                    const oosSharpe = Number.isFinite(Number(p.data[3])) ? Number(p.data[3]).toFixed(2) : '--';
                    const oosReturn = Number.isFinite(Number(p.data[4])) ? `${(Number(p.data[4]) * 100).toFixed(2)}%` : '--';
                    const oosDrawdown = Number.isFinite(Number(p.data[5])) ? `${(Number(p.data[5]) * 100).toFixed(2)}%` : '--';
                    const stability = Number.isFinite(Number(p.data[6])) ? `${(Number(p.data[6]) * 100).toFixed(1)}%` : '--';
                    const eligible = p.data[7] ? t('recommendation_eligible') : t('recommendation_not_eligible');
                    return `<b>${t('tooltip_theory_title')}</b><br/>${t('tooltip_expected_return')}: ${ret}%<br/>${t('tooltip_expected_risk')}: ${risk}%<br/><br/><b>${t('recommendation_oos_evidence')}</b><br/>${t('walk_forward_return')}: ${oosReturn}<br/>${t('oos_excess_sharpe')}: ${oosSharpe}<br/>${t('walk_forward_max_dd')}: ${oosDrawdown}<br/>${t('walk_forward_stability')}: ${stability}<br/>${eligible}`;
                }
            },
            xAxis: {
                type: 'value',
                name: xName,
                nameGap: 18,
                axisLabel: { formatter: (v) => `${(v * 100).toFixed(2)}%`, color: '#7d8478' },
                axisLine: { lineStyle: { color: 'rgba(218,224,210,0.22)' } },
                splitLine: { lineStyle: { color: 'rgba(218,224,210,0.07)' } },
                min: 'dataMin'
            },
            yAxis: {
                type: 'value',
                name: yName,
                nameGap: 22,
                axisLabel: { formatter: (v) => `${(v * 100).toFixed(1)}%`, color: '#7d8478' },
                axisLine: { lineStyle: { color: 'rgba(218,224,210,0.22)' } },
                splitLine: { lineStyle: { color: 'rgba(218,224,210,0.07)' } },
                min: 'dataMin'
            },
            grid: { top: 72, right: 64, bottom: 52, left: 64, containLabel: true },
            series: [
                {
                    type: 'scatter',
                    data: frontierData,
                    symbolSize: 10,
                    itemStyle: { color: '#8da7a4', opacity: 0.88 }
                },
                {
                    name: t('selected_point'),
                    type: 'scatter',
                    silent: true,
                    data: selectedPoint ? [[selectedPoint.risk, selectedPoint.return]] : [],
                    symbolSize: 18,
                    itemStyle: {
                        color: '#d9a441',
                        borderColor: '#f3f0e8',
                        borderWidth: 2
                    },
                    z: 10
                }
            ]
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
            if (code === 'Cash') {
                return t('current_cash');
            }
            if (fundNames[code]) {
                return fundNames[code];
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



    const recommendedPoint = getRecommendedFrontierPoint(analysisResult);
    const recommendationEvidence = getRecommendationEvidence(analysisResult);
    const isRecommendedPointSelected = recommendedPoint === selectedPoint;

    return (
        <div className="portfolio-optimizer">
            <form onSubmit={handleAnalysisSubmit} className="main-form">
                <div className="workspace-grid">
                    <section className="workspace-pane assets-pane">
                        <div className="dashboard-card card-fill">
                            <div className="card-header">
                                <h3 className="card-title"><Wallet size={20} className="card-icon" /> {t('asset_config_title')}</h3>
                            </div>

                            <div className="form-group">
                                <label className="form-label" htmlFor="fundCodeInput">{t('add_fund_label')}</label>
                                <div className="inline-actions">
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

                            {fundCodes.length > 0 && <div className="section-divider" />}

                            <div className="asset-list-scroll asset-list-stack">
                                {fundCodes.length > 0 && (
                                    <>
                                        <>
                                            <div className="asset-list-header">
                                                <div>{t('header_fund')}</div>
                                                <div>{t('header_asset_category')}</div>
                                                <div>{t('header_substitute_for')}</div>
                                                <div>{t('header_buy')}</div>
                                                <div>{t('header_sell')}</div>
                                                <div>{t('header_manage')}</div>
                                                <div>{t('header_daily_limit')}</div>
                                                <div>{t('header_monthly_limit')}</div>
                                                <div></div>
                                            </div>
                                            <p className="text-[11px] text-slate-400 mb-2">{t('manage_fee_note')}</p>
                                            {fundCodes.map(code => (
                                                <div key={code} className="asset-item-row">
                                                    <span className="asset-name" title={fundNames[code] || code}>
                                                        <span className="asset-name-label">{fundNames[code] || t('fund_name_loading')}</span>
                                                        <span className="asset-code">{code}</span>
                                                    </span>
                                                    <label className="asset-field">
                                                        <span className="asset-mobile-label">{t('header_asset_category')}</span>
                                                        <select className="asset-input-small" value={ASSET_CATEGORY_OPTIONS.includes(fundAssetCategories[code]) ? fundAssetCategories[code] : 'other'} onChange={(e) => handleAssetCategoryChange(code, e.target.value)}>
                                                            {ASSET_CATEGORY_OPTIONS.map(category => (
                                                                <option key={category} value={category}>{t(`asset_category_${category}`)}</option>
                                                            ))}
                                                        </select>
                                                    </label>
                                                    <label className="asset-field">
                                                        <span className="asset-mobile-label">{t('header_substitute_for')}</span>
                                                        <select
                                                            className="asset-input-small"
                                                            value={fundSubstituteFor[code] || ''}
                                                            onChange={(e) => handleSubstituteForChange(code, e.target.value)}
                                                            disabled={Object.values(fundSubstituteFor).includes(code)}
                                                            title={Object.values(fundSubstituteFor).includes(code) ? t('substitute_primary_locked') : t('substitute_for_help')}
                                                        >
                                                            <option value="">{t('substitute_none')}</option>
                                                            {fundCodes
                                                                .filter(primary => primary !== code && !fundSubstituteFor[primary])
                                                                .map(primary => (
                                                                    <option key={primary} value={primary}>
                                                                        {fundNames[primary] || primary}
                                                                    </option>
                                                                ))}
                                                        </select>
                                                    </label>
                                                    <label className="asset-field"><span className="asset-mobile-label">{t('header_buy')}</span><input type="number" step="0.01" className="asset-input-small" value={fundBuyFees[code] || ''} onChange={(e) => handleBuyFeeChange(code, e.target.value)} placeholder="0.15" /></label>
                                                    <label className="asset-field"><span className="asset-mobile-label">{t('header_sell')}</span><input type="number" step="0.01" className="asset-input-small" value={fundSellFees[code] || ''} onChange={(e) => handleSellFeeChange(code, e.target.value)} placeholder="0.5" /></label>
                                                    <label className="asset-field"><span className="asset-mobile-label">{t('header_manage')}</span><input type="number" step="0.01" className="asset-input-small" value={fundFees[code] || ''} onChange={(e) => handleFeeChange(code, e.target.value)} placeholder="0.6" /></label>
                                                    <label className="asset-field"><span className="asset-mobile-label">{t('header_daily_limit')}</span><input type="number" step="1" min="0" className="asset-input-small" value={fundInvestmentLimits[code]?.daily_limit || ''} onChange={(e) => handleInvestmentLimitChange(code, 'daily_limit', e.target.value)} placeholder={t('limit_unlimited')} /></label>
                                                    <label className="asset-field"><span className="asset-mobile-label">{t('header_monthly_limit')}</span><input type="number" step="1" min="0" className="asset-input-small" value={fundInvestmentLimits[code]?.monthly_limit || ''} onChange={(e) => handleInvestmentLimitChange(code, 'monthly_limit', e.target.value)} placeholder={t('limit_unlimited')} /></label>
                                                    <button type="button" className="icon-btn" onClick={() => handleRemoveAsset(code)}><X size={16} /></button>
                                                </div>
                                            ))}
                                        </>
                                    </>
                                )}
                            </div>
                        </div>
                    </section>

                    <section className="workspace-pane backtest-pane">
                        <div className="dashboard-card">
                            <div className="card-header">
                                <h3 className="card-title"><Calendar size={20} className="card-icon" /> {t('backtest_title')}</h3>
                            </div>
                            <div className="form-grid two-col">
                                <div className="form-group">
                                    <label className="form-label">{t('start_date')}</label>
                                    <input type="date" className="form-input" value={startDate} onChange={(e) => { clearAnalysisOutputs(); setStartDate(e.target.value); localStorage.setItem('startDate', e.target.value); }} />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">{t('end_date')}</label>
                                    <input type="date" className="form-input" value={endDate} onChange={(e) => { clearAnalysisOutputs(); setEndDate(e.target.value); localStorage.setItem('endDate', e.target.value); }} />
                                </div>
                            </div>
                            <div className="inline-actions date-presets">
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(1)}>{t('last_1_year')}</button>
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(3)}>{t('last_3_years')}</button>
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(5)}>{t('last_5_years')}</button>
                            </div>
                        </div>
                        <button type="submit" className="btn btn-primary analyze-button" disabled={loading.analysis || fundCodes.length === 0}>
                            {loading.analysis ? t('analyzing') : t('analyze_btn')} <ArrowRight size={20} />
                        </button>
                    </section>
                </div>
            </form>

            {error && <div role="alert" className="inline-alert inline-alert-danger"><span className="alert-dot" />{error}</div>}

            {analysisResult && (
                <div className="results-stack">
                    <section className="workspace-section">
                        <div className="dashboard-card">
                            <div className="card-header justify-between">
                                <h3 className="card-title"><TrendingUp size={20} className="card-icon" /> {t('step_2_title')}</h3>
                            </div>

                            {analysisResult.warnings?.length > 0 && (
                                <details className="methodology-notes">
                                    <summary>
                                        <span className="methodology-summary-icon"><Info size={15} /></span>
                                        <span className="methodology-summary-copy">
                                            <strong>{t('methodology_notes_title')}</strong>
                                            <small>{t('methodology_notes_summary').replace('{count}', analysisResult.warnings.length)}</small>
                                        </span>
                                        <ChevronDown className="methodology-chevron" size={17} aria-hidden="true" />
                                    </summary>
                                    <div className="methodology-list">
                                        {analysisResult.warnings.map((warning, index) => (
                                            <div className="methodology-item" key={index}>
                                                <span>{String(index + 1).padStart(2, '0')}</span>
                                                <p>{warning}</p>
                                            </div>
                                        ))}
                                    </div>
                                </details>
                            )}

                            <ReactECharts className="frontier-chart" option={getFrontierOptions()} style={{ height: 430 }} onEvents={{ 'click': onChartClick }} />
                            <p className="text-center text-slate-400 text-sm mt-4">{t('chart_hint')}</p>
                            {recommendationEvidence && (
                                <div data-testid="recommendation-evidence" className="evidence-card">
                                    <div className="evidence-card-header">
                                        <h4>{t('recommendation_basis_title')}</h4>
                                        <span>
                                            {t('recommendation_candidates')
                                                .replace('{eligible}', recommendationEvidence.eligibleCount)
                                                .replace('{total}', recommendationEvidence.totalCount)}
                                        </span>
                                    </div>
                                    <p className="evidence-copy">{t('recommendation_basis_explanation')}</p>
                                    <div className="evidence-metrics">
                                        <div>
                                            <div className="metric-label">{t('oos_excess_sharpe')}</div>
                                            <div className="metric-value metric-neutral">{formatRatio(recommendationEvidence.point.frontier_walk_forward_sharpe)}</div>
                                        </div>
                                        <div>
                                            <div className="metric-label">{t('walk_forward_return')}</div>
                                            <div className="metric-value metric-success">{formatPercentValue(recommendationEvidence.point.frontier_walk_forward_annualized_return)}</div>
                                        </div>
                                        <div>
                                            <div className="metric-label">{t('walk_forward_max_dd')}</div>
                                            <div className="metric-value metric-warning">{formatPercentValue(recommendationEvidence.point.frontier_walk_forward_max_drawdown)}</div>
                                        </div>
                                        <div>
                                            <div className="metric-label">{t('walk_forward_stability')}</div>
                                            <div className="metric-value metric-violet">{formatPercentValue(recommendationEvidence.point.frontier_walk_forward_weight_stability, 1)}</div>
                                        </div>
                                    </div>
                                    {recommendationEvidence.confidence === 'limited' && (
                                        <p className="evidence-caveat">{t('recommendation_limited_confidence')}</p>
                                    )}
                                </div>
                            )}
                        </div>
                        <AssetDiagnosticsPanel diagnostics={analysisResult.asset_diagnostics} />
                        {analysisResult.covariance_ablation && (
                            <div className="dashboard-card evidence-panel">
                                <div className="card-header">
                                    <h3 className="card-title">{t('covariance_ablation_title')}</h3>
                                </div>
                                <p className="panel-copy">{t('covariance_ablation_note')}</p>
                                <div className={`state-notice ${analysisResult.covariance_ablation.promotion_status === 'candidate' ? 'state-success' : 'state-neutral'}`}>
                                    {analysisResult.covariance_ablation.promotion_status === 'candidate'
                                        ? t('covariance_candidate')
                                        : t('covariance_retain_fixed')}
                                </div>
                                <div className="mt-4 overflow-x-auto">
                                    <table className="data-table min-w-[760px]">
                                        <thead>
                                            <tr>
                                                <th>{t('ablation_segment')}</th>
                                                <th>{t('ablation_method')}</th>
                                                <th>Sharpe</th>
                                                <th>{t('walk_forward_max_dd')}</th>
                                                <th>{t('walk_forward_stability')}</th>
                                                <th>{t('shrinkage_intensity')}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {Object.entries(analysisResult.covariance_ablation.segments).flatMap(([segment, comparison]) =>
                                                ['fixed_20', 'ledoit_wolf'].map((method) => {
                                                    const metrics = comparison[method];
                                                    return (
                                                        <tr key={`${segment}-${method}`}>
                                                            <td>{t(`ablation_${segment}`)}</td>
                                                            <td>{method === 'fixed_20' ? t('fixed_shrinkage') : 'Ledoit–Wolf'}</td>
                                                            <td>{metrics.status === 'ok' ? metrics.sharpe.toFixed(2) : t('data_insufficient')}</td>
                                                            <td>{metrics.status === 'ok' ? `${(metrics.max_drawdown * 100).toFixed(1)}%` : '—'}</td>
                                                            <td>{metrics.status === 'ok' ? `${(metrics.weight_stability * 100).toFixed(1)}%` : '—'}</td>
                                                            <td>{metrics.status === 'ok' ? `${(metrics.average_shrinkage_intensity * 100).toFixed(1)}%` : '—'}</td>
                                                        </tr>
                                                    );
                                                })
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            </div>
                        )}
                    </section>

                    {selectedPoint && (
                        <section className="workspace-section">
                            <div className="dashboard-card">
                                <div className="card-header">
                                    <h3 className="card-title"><Settings size={20} className="card-icon" /> {t('strategy_title')}</h3>
                                </div>
                                <div className="strategy-stack">
                                    <div className="strategy-callout">
                                        {analysisResult?.recommended_point_index !== null && analysisResult?.recommended_point_index !== undefined && analysisResult?.efficient_frontier?.[analysisResult.recommended_point_index] === selectedPoint && (
                                            <div className="text-sm text-sky-400 font-medium mb-2">{t('auto_selected_plan')}</div>
                                        )}
                                        <p className="panel-copy">{t('manual_override_hint')}</p>
                                        <div className="inline-actions strategy-actions">
                                            {recommendedPoint && (
                                                <button
                                                    type="button"
                                                    className="text-link-btn"
                                                    onClick={handleResetToRecommendedPoint}
                                                    disabled={isRecommendedPointSelected}
                                                >
                                                    <RotateCcw size={14} />
                                                    {t('reset_to_recommended_point')}
                                                </button>
                                            )}
                                            <button type="button" className="text-link-btn" onClick={() => setShowPortfolioDetails(prev => !prev)}>
                                                <Settings size={14} />
                                                {showPortfolioDetails ? t('hide_base_portfolio') : t('view_base_portfolio')}
                                            </button>
                                        </div>
                                        {showPortfolioDetails && (
                                            <div className="details-grid">
                                                <div className="detail-panel">
                                                    <h4>{t('selected_metrics')}</h4>
                                                    <div className="detail-row">
                                                        <span>{t('expected_return')}</span>
                                                        <span className="metric-value metric-success">{(selectedPoint.return * 100).toFixed(2)}%</span>
                                                    </div>
                                                    <div className="detail-row">
                                                        <span>{t('expected_risk')}</span>
                                                        <span className="metric-value metric-warning">{(selectedPoint.risk * 100).toFixed(2)}%</span>
                                                    </div>
                                                </div>
                                                <div className="detail-panel">
                                                    <h4>{t('base_portfolio')}</h4>
                                                    <table className="data-table">
                                                        <thead><tr><th>{t('header_fund')}</th><th>{t('effective_risky_weight')}</th></tr></thead>
                                                        <tbody>
                                                            {Object.entries(selectedPoint.weights || {}).map(([code, weight]) => (
                                                                <tr key={`effective-${code}`}>
                                                                    <td className="text-sm">{fundNames[code] || analysisResult.fund_names[code] || code} <span className="text-slate-500 text-xs">({code})</span></td>
                                                                    <td className="font-mono text-sky-400">{(weight * 100).toFixed(2)}%</td>
                                                                </tr>
                                                            ))}
                                                        </tbody>
                                                    </table>
                                                </div>
                                            </div>
                                        )}
                                    </div>

                                    <div>
                                        <h5 className="subsection-title"><DollarSign size={18} /> {t('current_holdings_monthly')}</h5>

                                        <div className="holdings-input-grid">
                                            <div>
                                                <label className="form-label">{t('current_cash')}</label>
                                                <input type="number" className="form-input" value={currentCash} onChange={(e) => { setCurrentCash(e.target.value); localStorage.setItem('currentCash', e.target.value); }} placeholder="0" />
                                            </div>
                                            <div>
                                                <label className="form-label">{t('monthly_budget')}</label>
                                                <input type="number" className="form-input" value={monthlyInvestment} onChange={(e) => { setMonthlyInvestment(e.target.value); localStorage.setItem('monthlyInvestment', e.target.value); }} placeholder={t('placeholder_money')} />
                                            </div>
                                        </div>

                                        <div className="holdings-table-wrap">
                                            <table className="data-table">
                                                <thead><tr><th>{t('current_holding_val')}</th><th>{t('input_amount')}</th></tr></thead>
                                                <tbody>
                                                    {Object.entries(selectedPoint.weights).map(([code, weight]) => (
                                                        <tr key={code}>
                                                            <td>{fundNames[code] || analysisResult.fund_names[code] || code}</td>
                                                            <td><input type="number" className="form-input py-1 text-sm w-32" value={initialHoldings[code] || ''} onChange={(e) => handleHoldingChange(code, e.target.value)} placeholder="0" /></td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                        </div>

                                        <button className="text-link-btn advanced-toggle" onClick={() => setShowAdvancedParams(!showAdvancedParams)}>
                                            <Settings size={14} />
                                            {showAdvancedParams ? t('collapse_advanced') : t('expand_advanced')}
                                        </button>

                                        {showAdvancedParams && (
                                            <div className="advanced-controls">
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
                                                    <label className="form-label text-xs">{t('planned_purchase_days')}</label>
                                                    <input className="form-input text-sm" type="number" step="1" min="1" value={plannedPurchaseDays} onChange={(e) => { setPlannedPurchaseDays(e.target.value); localStorage.setItem('plannedPurchaseDays', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('planned_purchase_days_help')}</p>
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
                                                                <div className="form-group col-span-2">
                                                                    <label className="form-label text-xs">{t('risk_horizon_days')}</label>
                                                                    <input className="form-input text-sm" type="number" step="1" min="5" max="63" value={riskHorizonDays} onChange={(e) => { setRiskHorizonDays(e.target.value); localStorage.setItem('riskHorizonDays', e.target.value); }} />
                                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('risk_horizon_days_help')}</p>
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

                                        <button className="btn btn-primary strategy-submit" onClick={handleStrategySubmit} disabled={loading.strategy || !selectedPoint}>
                                            {loading.strategy ? t('analyzing') : t('start_analysis_btn')}
                                        </button>
                                        {budgetError && (
                                            <div className="inline-alert inline-alert-danger">
                                                <span className="alert-dot"></span>
                                                {budgetError}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                    </section>
                    )}

                    {strategyResult && (
                        <section className="workspace-section">
                            <div className="dashboard-card backtest-card">
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

                                <div className="chart-grid">
                                    <div><ReactECharts option={getStrategyChartOptions('ideal_kelly_dca')} style={{ height: 300 }} /></div>
                                    {strategyResult.actual_kelly_dca && (
                                        <div><ReactECharts option={getStrategyChartOptions('actual_kelly_dca')} style={{ height: 300 }} /></div>
                                    )}
                                </div>

                                {strategyResult.walk_forward?.status === 'ok' && (
                                    <div className="audit-panel">
                                        <h4 className="text-lg font-semibold text-sky-400 mb-2">{t('executable_walk_forward_title')}</h4>
                                        <p className="text-xs text-slate-400 mb-2">{t('executable_walk_forward_note')}</p>
                                        <p className="text-xs text-emerald-300 mb-4">{t('wf_unit_nav_basis')}</p>
                                        {strategyResult.walk_forward.strategies.full_strategy?.evidence_quality?.status === 'low' && (
                                            <div className="mb-4 rounded-lg border border-amber-500/40 bg-amber-900/10 p-3 text-xs text-amber-200">
                                                {t('wf_low_evidence')
                                                    .replace('{months}', strategyResult.walk_forward.strategies.full_strategy.evidence_quality.evaluation_months)
                                                    .replace('{tail}', strategyResult.walk_forward.strategies.full_strategy.cvar_tail_observations)
                                                    .replace('{downside}', strategyResult.walk_forward.strategies.full_strategy.sortino_downside_observations)}
                                            </div>
                                        )}
                                        <table className="data-table min-w-[1500px]">
                                            <thead>
                                                <tr>
                                                    <th>{t('wf_strategy')}</th>
                                                    <th>{t('walk_forward_return')}</th>
                                                    <th>{t('walk_forward_vol')}</th>
                                                    <th>{t('walk_forward_sharpe')}</th>
                                                    <th>{t('wf_sortino')}</th>
                                                    <th>{t('walk_forward_max_dd')}</th>
                                                    <th>{t('wf_worst_month')}</th>
                                                    <th>{t('wf_cvar')}</th>
                                                    <th>{t('wf_final_wealth')}</th>
                                                    <th>{t('wf_fees')}</th>
                                                    <th>{t('wf_avg_cash')}</th>
                                                    <th>{t('wf_execution_deviation')}</th>
                                                </tr>
                                            </thead>
                                            <tbody>
                                                {Object.entries(strategyResult.walk_forward.strategies).map(([name, metrics]) => (
                                                    <tr key={name} className={name === 'full_strategy' ? 'bg-sky-500/5' : ''}>
                                                        <td>{t(`wf_${name}`)}</td>
                                                        <td>{formatPercentValue(metrics.annualized_return)}</td>
                                                        <td>{formatPercentValue(metrics.annualized_volatility)}</td>
                                                        <td>{formatRatio(metrics.sharpe)}</td>
                                                        <td>{formatRatio(metrics.sortino)}</td>
                                                        <td>{formatPercentValue(metrics.max_drawdown)}</td>
                                                        <td>{formatPercentValue(metrics.worst_month)}</td>
                                                        <td>{formatPercentValue(metrics.cvar_loss)}</td>
                                                        <td>¥{metrics.final_wealth.toFixed(2)}</td>
                                                        <td>¥{metrics.total_transaction_fees.toFixed(2)}</td>
                                                        <td>{formatPercentValue(metrics.average_cash_exposure)}</td>
                                                        <td>{formatPercentValue(metrics.average_execution_deviation)}</td>
                                                    </tr>
                                                ))}
                                            </tbody>
                                        </table>
                                        {strategyResult.walk_forward.strategies.full_strategy && (() => {
                                            const full = strategyResult.walk_forward.strategies.full_strategy;
                                            const categoryEntries = Object.entries(full.average_asset_category_exposures || {})
                                                .filter(([, exposure]) => exposure > 0.00005);
                                            return (
                                                <>
                                            <div className="audit-evidence-grid">
                                                <div className="diagnostic-card diagnostic-info">
                                                    <div className="diagnostic-title">{t('wf_frontier_transmission_title')}</div>
                                                    <div className="diagnostic-copy">
                                                                {t('wf_avg_target_change')}: {formatPercentValue(full.average_frontier_target_weight_change)}
                                                                {' · '}{t('wf_avg_actual_change')}: {formatPercentValue(full.average_actual_basket_weight_change)}
                                                                {' · '}{t('wf_avg_transmission')}: {formatPercentValue(full.average_frontier_change_transmission)}
                                                            </div>
                                                        </div>
                                                <div className="diagnostic-card diagnostic-success">
                                                    <div className="diagnostic-title">{t('wf_avg_category_exposure')}</div>
                                                    <div className="diagnostic-copy diagnostic-list">
                                                                {categoryEntries.map(([category, exposure]) => (
                                                                    <span key={category}>{t(`asset_category_${category}`)} {formatPercentValue(exposure)}</span>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    </div>
                                                    <details className="audit-details">
                                                        <summary>{t('wf_monthly_audit')}</summary>
                                                        <div className="audit-details-scroll">
                                                            <table className="data-table min-w-[1200px]">
                                                                <thead>
                                                                    <tr>
                                                                        <th>{t('wf_month')}</th>
                                                                        <th>{t('wf_full_kelly_raw')}</th>
                                                                        <th>{t('wf_fractional_kelly_raw')}</th>
                                                                        <th>{t('wf_kelly_clipped')}</th>
                                                                        <th>{t('wf_actual_position')}</th>
                                                                        <th>{t('wf_kelly_changed_trade')}</th>
                                                                        <th>{t('wf_target_weight_change')}</th>
                                                                        <th>{t('wf_actual_weight_change')}</th>
                                                                        <th>{t('wf_transmission')}</th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {(full.data_access_audit || []).map((month) => (
                                                                        <tr key={month.realized_date}>
                                                                            <td>{month.realized_date?.slice(0, 7)}</td>
                                                                            <td>{formatPercentValue(month.full_kelly_raw)}</td>
                                                                            <td>{formatPercentValue(month.fractional_kelly_raw)}</td>
                                                                            <td>{formatPercentValue(month.kelly_clipped_target)}</td>
                                                                            <td>{formatPercentValue(month.actual_fund_position)}</td>
                                                                            <td>{month.kelly_changed_trade ? t('wf_yes') : t('wf_no')}</td>
                                                                            <td>{formatPercentValue(month.frontier_target_weight_change)}</td>
                                                                            <td>{formatPercentValue(month.actual_basket_weight_change)}</td>
                                                                            <td>{formatPercentValue(month.frontier_change_transmission)}</td>
                                                                        </tr>
                                                                    ))}
                                                                </tbody>
                                                            </table>
                                                        </div>
                                                    </details>
                                                </>
                                            );
                                        })()}
                                        {strategyResult.walk_forward.kelly_window_comparison?.status === 'selected' && (
                                            <div className="diagnostic-card diagnostic-info">
                                                <div className="diagnostic-title">
                                                    {t('kelly_window_platform')}: {strategyResult.walk_forward.kelly_window_comparison.selected_window_months} {t('months')}
                                                </div>
                                                <p className="diagnostic-copy">{t('kelly_window_platform_note')}</p>
                                                <div className="diagnostic-copy diagnostic-list">
                                                    {strategyResult.walk_forward.kelly_window_comparison.windows.map((item) => (
                                                        <span key={item.window_months}>
                                                            {item.window_months}{t('month_short')}: {(item.annualized_return * 100).toFixed(1)}% / Sharpe {item.sharpe.toFixed(2)} / DD {(item.max_drawdown * 100).toFixed(1)}%
                                                        </span>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                        {strategyResult.walk_forward.covariance_ablation && (
                                            <div className="diagnostic-card diagnostic-violet">
                                                <div className="diagnostic-title">{t('complete_covariance_ablation')}</div>
                                                <p className="diagnostic-copy">{t('complete_covariance_ablation_note')}</p>
                                                <div className="diagnostic-copy diagnostic-list">
                                                    {Object.entries(strategyResult.walk_forward.covariance_ablation.segments).map(([segment, comparison]) => (
                                                        <span key={segment}>
                                                            {t(`ablation_${segment}`)}:
                                                            {comparison.fixed_20.status === 'ok' && comparison.ledoit_wolf.status === 'ok'
                                                                ? ` 20% ${comparison.fixed_20.sharpe.toFixed(2)} / LW ${comparison.ledoit_wolf.sharpe.toFixed(2)} · DD ${(comparison.fixed_20.max_drawdown * 100).toFixed(1)}%/${(comparison.ledoit_wolf.max_drawdown * 100).toFixed(1)}%`
                                                                : ` ${t('data_insufficient')}`}
                                                        </span>
                                                    ))}
                                                </div>
                                                <div className="mt-2 text-xs text-violet-200">
                                                    {strategyResult.walk_forward.covariance_ablation.promotion_status === 'candidate'
                                                        ? t('covariance_candidate')
                                                        : t('covariance_retain_fixed')}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                )}
                                {strategyResult.walk_forward?.status === 'insufficient_data' && (
                                    <div className="mt-6 p-3 rounded-lg border border-amber-500/40 bg-amber-900/10 text-amber-300 text-sm">
                                        {t('wf_insufficient_data')}
                                    </div>
                                )}
                            </div>
                        </section>
                    )}

                    {recommendationResult && (
                        <section className="workspace-section recommendation-section">
                            <div className="recommendation-card">
                                <div className="card-header">
                                    <h3 className="card-title recommendation-title"><TrendingUp size={24} /> {t('recommend_title')}</h3>
                                    <button className="text-link-btn" onClick={handleExport} aria-label={t('export_report')}>
                                        <Download size={16} /> {t('export_report')}
                                    </button>
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
                                        <div className="recommendation-stat-value">{((recommendationResult.target_fund_ratio ?? recommendationResult.target_equity_ratio) * 100).toFixed(0)}%</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">{t('target_equity_exposure')}</div>
                                        <div className="recommendation-stat-value">{((recommendationResult.target_equity_exposure ?? 0) * 100).toFixed(0)}%</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">{t('target_risk_exposure')}</div>
                                        <div className="recommendation-stat-value">{((recommendationResult.target_risk_asset_exposure ?? 0) * 100).toFixed(0)}%</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">{t('suggested_buy_total')}</div>
                                        <div className="recommendation-stat-value text-white">¥{recommendationResult.recommended_monthly_investment.toFixed(2)}</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">{t('monthly_budget_label')}</div>
                                        <div className="recommendation-stat-value text-slate-400">¥{recommendationResult.monthly_budget}</div>
                                    </div>
                                </div>

                                <div className={`recommendation-diagnostic ${recommendationResult.decision_readiness !== 'manual_review_required' || !recommendationResult.risk_limit_enforceable ? 'diagnostic-warning' : ''}`}>
                                    <div className="diagnostic-title">{t('decision_readiness')}</div>
                                    <div className="diagnostic-copy">
                                        {t(`decision_${recommendationResult.decision_readiness || 'research_only'}`)}
                                    </div>
                                    <div className="diagnostic-copy">
                                        {t('base_non_riskfree_ratio')}: {((recommendationResult.base_non_riskfree_fund_ratio || 0) * 100).toFixed(1)}%
                                        {' × '}{t('tactical_deployment_ratio')}: {((recommendationResult.tactical_deployment_ratio || 0) * 100).toFixed(1)}%
                                        {' = '}{t('final_non_riskfree_ratio')}: {((recommendationResult.final_non_riskfree_fund_ratio || 0) * 100).toFixed(1)}%
                                    </div>
                                    <div className="diagnostic-copy">
                                        {t('safe_sleeve_ratio')}: {((recommendationResult.base_safe_sleeve_ratio || 0) * 100).toFixed(1)}%
                                        {' · '}{t('residual_cash_ratio')}: {((recommendationResult.residual_cash_ratio || 0) * 100).toFixed(1)}%
                                        {' · '}{t('actual_risk_ratio')}: {((recommendationResult.actual_risk_ratio || 0) * 100).toFixed(1)}%
                                    </div>
                                    {!recommendationResult.risk_limit_enforceable && (
                                        <div className="diagnostic-copy">
                                            {t('risk_not_enforceable')} · {t('cash_reserve_shortfall')}: ¥{Number(recommendationResult.cash_reserve_shortfall || 0).toFixed(2)}
                                        </div>
                                    )}
                                </div>

                                {recommendationResult.window_robustness && (
                                    <div className={`recommendation-diagnostic ${recommendationResult.window_robustness.status === 'unstable' ? 'diagnostic-warning' : ''}`}>
                                        <div className="diagnostic-title">Kelly 回看窗口稳健性（3 年基准）</div>
                                        <div className="diagnostic-copy">{recommendationResult.window_robustness.message}</div>
                                        {recommendationResult.window_robustness.measurements?.length > 0 && (
                                            <div className="diagnostic-copy diagnostic-list">
                                                {recommendationResult.window_robustness.measurements.map((item) => (
                                                    <span key={item.window_months} className="mr-3">
                                                        {item.window_months}月：{item.available ? `${(item.target_risky_ratio * 100).toFixed(1)}%` : '数据不足'}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                )}
                                {recommendationResult.optimizer_info?.cvar_confidence_status && (
                                    <div className={`recommendation-diagnostic ${recommendationResult.optimizer_info.cvar_warning_only ? 'diagnostic-warning' : ''}`}>
                                        <div className="diagnostic-title">{t('risk_diagnostics')}</div>
                                        <div className="diagnostic-copy">
                                            {t('risk_data_source')}: {t(`risk_source_${recommendationResult.optimizer_info.cvar_data_source}`)}
                                            {' · '}{t('risk_horizon')}: {recommendationResult.optimizer_info.risk_horizon_days} {t('trading_days')}
                                            {' · '}{t('risk_observations')}: {recommendationResult.optimizer_info.cvar_return_observations}
                                            {' / '}{t('risk_effective_observations')}: {recommendationResult.optimizer_info.cvar_effective_return_observations}
                                            {' · '}{t('risk_tail_samples')}: {recommendationResult.optimizer_info.cvar_effective_tail_count}
                                        </div>
                                        {recommendationResult.optimizer_info.cvar_warning_only && (
                                            <div className="diagnostic-copy">{t('cvar_low_confidence_warning')}</div>
                                        )}
                                    </div>
                                )}
                                {recommendationResult.execution_allocation && (
                                    <div className={`recommendation-diagnostic ${recommendationResult.execution_allocation.fallback_used ? 'diagnostic-warning' : ''}`}>
                                        <div className="diagnostic-title">{t('execution_allocation_diagnostics')}</div>
                                        <div className="diagnostic-copy">
                                            {t('execution_status')}: {recommendationResult.execution_allocation.status}
                                            {' · '}{t('execution_unspent')}: ¥{Number(recommendationResult.execution_allocation.unspent_budget || 0).toFixed(2)}
                                        </div>
                                        {recommendationResult.execution_allocation.unspent_reason_label && (
                                            <div className="diagnostic-copy">
                                                {t('execution_unspent_reason')}: {recommendationResult.execution_allocation.unspent_reason_label}
                                            </div>
                                        )}
                                        {recommendationResult.execution_allocation.tracking_error_before !== null && recommendationResult.execution_allocation.tracking_error_before !== undefined && (
                                            <div className="diagnostic-copy">
                                                {t('tracking_error')}: {formatPercentValue(recommendationResult.execution_allocation.tracking_error_before)}
                                                {' → '}{formatPercentValue(recommendationResult.execution_allocation.tracking_error_after)}
                                            </div>
                                        )}
                                        {Object.keys(recommendationResult.execution_allocation.substitute_purchases || {}).length > 0 && (
                                            <div className="diagnostic-copy">{t('substitute_purchase_note')}</div>
                                        )}
                                    </div>
                                )}
                                {recommendationResult.fund_advice && (
                                    <table className="recommendation-table">
                                        <thead>
                                            <tr>
                                                <th>{t('table_fund')}</th>
                                                <th>{t('table_action')}</th>
                                                <th>{t('table_current')}</th>
                                                <th>{t('table_amount')}</th>
                                                <th>{t('table_gap')}</th>
                                                <th>{t('table_target')}</th>
                                                <th>{t('table_ideal_target')}</th>
                                                <th>{t('table_reason')}</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {recommendationResult.fund_advice.map(advice => {
                                                const actionMeta = getAdviceActionMeta(advice.action, t);
                                                const currentHolding = advice.current_holding ?? 0;
                                                const afterTrade = advice.executable_holding ?? advice.target_holding;
                                                const executableGap = afterTrade === undefined || afterTrade === null ? null : afterTrade - currentHolding;
                                                const idealTarget = advice.ideal_holding ?? advice.target_holding;
                                                return (
                                                    <tr key={advice.code}>
                                                        <td>{advice.name}</td>
                                                        <td>
                                                            <span className={`action-badge ${actionMeta.badgeClass}`}>
                                                                {actionMeta.label}
                                                            </span>
                                                        </td>
                                                        <td className="font-mono">¥{advice.current_holding?.toFixed(2) ?? '--'}</td>
                                                        <td className="font-mono">¥{advice.amount.toFixed(2)}</td>
                                                        <td className={`font-mono ${executableGap > 0 ? 'text-emerald-400' : executableGap < 0 ? 'text-amber-400' : ''}`}>{executableGap === undefined || executableGap === null ? '--' : `¥${executableGap.toFixed(2)}`}</td>
                                                        <td className="font-mono">¥{afterTrade?.toFixed(2) ?? '--'}</td>
                                                        <td className="font-mono text-slate-400">¥{idealTarget?.toFixed(2) ?? '--'}</td>
                                                        <td className="text-slate-500 text-xs">{advice.reason}</td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                )}
                            </div>
                        </section>
                    )}
                </div>
            )}
        </div>
    );
}

export default PortfolioOptimizer;
