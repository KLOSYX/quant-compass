import React, { useState, useEffect, useRef } from 'react';
import { useLanguage } from './LanguageContext';
import ReactECharts from 'echarts-for-react';
import { Plus, X, ArrowRight, Settings, Info, TrendingUp, DollarSign, Wallet, Calendar, RotateCcw, ChevronDown } from 'lucide-react';
import AssetDiagnosticsPanel from './AssetDiagnosticsPanel';
import { downloadPortfolioReport } from './exportPortfolioReport';

import { getISODate, formatDD, formatPercentValue, formatRatio, formatMoney, getStoredNumber, getStoredPercentWithLegacyRatioSupport, sanitizeLegacyHoldings, ASSET_CATEGORY_OPTIONS, buildAssetCategoriesPayload, buildSubstituteForPayload, getRecommendedFrontierPoint, getRecommendationEvidence } from "./portfolioViewUtils";
import MonthlyRecommendation from "./MonthlyRecommendation";

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
    const [selectedPoint, setSelectedPoint] = useState(() => {
        try { return JSON.parse(localStorage.getItem('confirmedTarget') || 'null'); }
        catch (_) { return null; }
    });
    const [monthlyInvestment, setMonthlyInvestment] = useState(() => localStorage.getItem('monthlyInvestment') || '');
    const [initialHoldings, setInitialHoldings] = useState(() => {
        let stored = {};
        try {
            stored = JSON.parse(localStorage.getItem('initialHoldings') || '{}');
        } catch (_) {
            stored = {};
        }
        const sanitized = sanitizeLegacyHoldings(stored);
        return sanitized;
    });
    const [currentCash, setCurrentCash] = useState(() => localStorage.getItem('currentCash') || '');

    // Advanced Strategy Parameters
    const [showAdvancedParams, setShowAdvancedParams] = useState(false);
    const [estimationWindow, setEstimationWindow] = useState(() => getStoredNumber('estimationWindow', 36));
    const [rebalanceBand, setRebalanceBand] = useState(() => getStoredNumber('rebalanceBand', 2));
    const [availableCash, setAvailableCash] = useState(() => getStoredNumber('availableCash', 0));
    const [pendingProceeds, setPendingProceeds] = useState(() => getStoredNumber('pendingProceeds', 0));
    const [pendingSells, setPendingSells] = useState(() => JSON.parse(localStorage.getItem('pendingSells') || '{}'));
    const [redemptionLimits, setRedemptionLimits] = useState(() => JSON.parse(localStorage.getItem('redemptionLimits') || '{}'));
    const [purchaseDates, setPurchaseDates] = useState('');
    const [addedExceptionCodes, setAddedExceptionCodes] = useState([]);
    const exceptionCodes = fundCodes.filter(code => addedExceptionCodes.includes(code)
        || (pendingSells[code] !== undefined && pendingSells[code] !== '')
        || (redemptionLimits[code] !== undefined && redemptionLimits[code] !== ''));
    const updateException = (code, value, state, setter, storageKey) => {
        const next = { ...state, [code]: value };
        setter(next);
        localStorage.setItem(storageKey, JSON.stringify(next));
    };
    const removeException = code => {
        setAddedExceptionCodes(previous => previous.filter(item => item !== code));
        updateException(code, '', pendingSells, setPendingSells, 'pendingSells');
        updateException(code, '', redemptionLimits, setRedemptionLimits, 'redemptionLimits');
    };

    const executionSettings = (live = false) => ({
        rebalance_band: Number(rebalanceBand) / 100,
        available_existing_cash: Number(availableCash),
        redemption_limits: Object.fromEntries(Object.entries(redemptionLimits).filter(([, v]) => v !== '').map(([c, v]) => [c, Number(v)])),
        ...(live ? {
            pending_sale_proceeds: Number(pendingProceeds),
            pending_sell_amounts: Object.fromEntries(Object.entries(pendingSells).filter(([, v]) => v !== '').map(([c, v]) => [c, Number(v)])),
            planned_purchase_dates: purchaseDates.trim() ? purchaseDates.trim().split(/[，,\s]+/) : []
        } : {})
    });
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
        setStrategyResult(null);
        setRecommendationResult(null);
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
        setLoading(prev => ({ ...prev, analysis: true }));
        setError(null);
        setAnalysisResult(null);
        setStrategyResult(null);
        setRecommendationResult(null);

        try {
            const feesAsFloats = Object.entries(fundFees).reduce((acc, [code, fee]) => {
                const parsedFee = parseFloat(fee);
                acc[code] = isNaN(parsedFee) ? 0 : parsedFee / 100;
                return acc;
            }, {});
            const parsedEstimationWindow = parseInt(estimationWindow, 10);
            const parsedMinimumCashReserve = parseFloat(minimumCashReserve);
            const parsedCvarConfidence = parseFloat(cvarConfidence);
            const parsedCvarLimit = parseFloat(cvarLimit);
            const parsedRiskHorizonDays = parseInt(riskHorizonDays, 10);
            const parsedMaxDrawdownLimit = parseFloat(maxDrawdownLimit);

            const payload = {
                target_weights: selectedPoint?.weights && Object.keys(selectedPoint.weights).every(code => fundCodes.includes(code))
                    ? selectedPoint.weights : undefined,
                fund_codes: fundCodes,
                fund_fees: feesAsFloats,
                asset_categories: buildAssetCategoriesPayload(fundCodes, fundAssetCategories),
                substitute_for: buildSubstituteForPayload(fundCodes, fundSubstituteFor),
                planned_purchase_days: Number(plannedPurchaseDays) || 1,
                start_date: startDate,
                end_date: endDate,
                strategy_mode: 'fixed_weight',
                estimation_window: Number.isNaN(parsedEstimationWindow) ? 36 : parsedEstimationWindow,
                minimum_cash_reserve: Number.isNaN(parsedMinimumCashReserve) ? 0 : parsedMinimumCashReserve,
                enable_cvar_constraint: enableCvarConstraint,
                cvar_confidence: (Number.isNaN(parsedCvarConfidence) ? 95 : parsedCvarConfidence) / 100,
                cvar_limit: (Number.isNaN(parsedCvarLimit) ? 8 : parsedCvarLimit) / 100,
                risk_horizon_days: Number.isNaN(parsedRiskHorizonDays) ? 21 : parsedRiskHorizonDays,
                enable_drawdown_constraint: enableDrawdownConstraint,
                max_drawdown_limit: (Number.isNaN(parsedMaxDrawdownLimit) ? 20 : parsedMaxDrawdownLimit) / 100,
                fund_investment_limits: buildFundInvestmentLimitsPayload(),
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
            if (selectedPoint?.weights && Object.keys(selectedPoint.weights).every(code => fundCodes.includes(code) || code === 'RiskFree')) {
                setSelectedPoint({ ...selectedPoint, risk: null, return: null, target_source: 'confirmed_selection' });
            } else if (selectedPoint?.weights) {
                setSelectedPoint(null);
                localStorage.removeItem('confirmedTarget');
                setError(t('target_universe_changed'));
            } else if (result.recommended_point_index !== null && result.recommended_point_index !== undefined) {
                const initialTarget = result.efficient_frontier[result.recommended_point_index] || null;
                setSelectedPoint(initialTarget);
            } else if (result.fallback_target) {
                setSelectedPoint(result.fallback_target);
            }
            setShowPortfolioDetails(false);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(prev => ({ ...prev, analysis: false }));
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
                    idealHoldings[code] = Math.max(0, totalCapital - totalCash) * weight;
                });
            }

            // ACTUAL: User's real holdings + separate cash balance
            const actualHoldings = Object.entries(sanitizeLegacyHoldings(initialHoldings)).reduce((acc, [code, val]) => {
                const v = parseFloat(val);
                if (Number.isFinite(v)) acc[code] = v;
                return acc;
            }, {});

            const basePayload = {
                ...executionSettings(),
                fund_codes: fundCodes,
                weights,
                fund_fees: feesAsFloats,
                asset_categories: buildAssetCategoriesPayload(fundCodes, fundAssetCategories),
                substitute_for: buildSubstituteForPayload(fundCodes, fundSubstituteFor),
                planned_purchase_days: Number(plannedPurchaseDays) || 1,
                start_date: analysisResult.backtest_period.start_date,
                end_date: analysisResult.backtest_period.end_date,
                monthly_investment: parseFloat(monthlyInvestment),
                strategy_mode: 'fixed_weight',
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
            };

            // Run BOTH backtests in parallel
            const [idealRes, actualRes] = await Promise.all([
                fetch('/api/backtest_strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...basePayload, initial_holdings: idealHoldings, initial_cash: totalCash, include_walk_forward: false }) }),
                fetch('/api/backtest_strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...basePayload, initial_holdings: actualHoldings, initial_cash: totalCash, include_walk_forward: true }) })
            ]);

            if (!idealRes.ok) throw new Error((await idealRes.json()).detail);
            if (!actualRes.ok) throw new Error((await actualRes.json()).detail);

            const idealData = await idealRes.json();
            const actualData = await actualRes.json();

            // Store both results - keep backward compatible structure
            setStrategyResult({
                ...idealData,
                ideal_fixed_target: idealData.fixed_target,
                actual_fixed_target: actualData.fixed_target,
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
                if (!isNaN(parsed)) {
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
                ...executionSettings(true),
                monthly_budget: parseFloat(monthlyInvestment) || 0,
                strategy_mode: 'fixed_weight',
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
            };

            const response = await fetch('/api/current_recommendation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error((await response.json()).detail);
            setRecommendationResult(await response.json());
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading((prev) => ({ ...prev, recommendation: false }));
        }
    };

    const handleStrategySubmit = async () => {
        if (!selectedPoint) return;
        if (Object.keys(selectedPoint.weights).some(code => !fundCodes.includes(code) && code !== 'RiskFree')) {
            setError(t('target_universe_changed'));
            return;
        }
        localStorage.setItem('confirmedTarget', JSON.stringify(selectedPoint));
        if (!monthlyInvestment) {
            setBudgetError(t('monthly_budget_required'));
            return;
        }
        setBudgetError('');
        setStrategyResult(null);
        setRecommendationResult(null);
        if (analysisResult?.analysis_status === 'target_only_fallback') {
            await getRecommendation();
        } else {
            await Promise.all([runBacktests(selectedPoint.weights), getRecommendation()]);
        }
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
        setSelectedPoint(selected);
        localStorage.setItem('confirmedTarget', JSON.stringify(selected));
        setStrategyResult(null);
        setRecommendationResult(null);


    };

    const handleResetToRecommendedPoint = () => {
        const recommended = getRecommendedFrontierPoint(analysisResult);
        if (!recommended) return;
        setSelectedPoint(recommended);
        localStorage.setItem('confirmedTarget', JSON.stringify(recommended));
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
            'ideal_fixed_target': t('strat_ideal_fixed_target'),
            'actual_fixed_target': t('strat_actual_fixed_target')
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

                            {analysisResult.analysis_status === 'target_only_fallback' ? (
                                <div role="status" className="recommendation-diagnostic diagnostic-warning">
                                    {analysisResult.fallback_message}
                                </div>
                            ) : (
                                <>
                                    <ReactECharts className="frontier-chart" option={getFrontierOptions()} style={{ height: 430 }} onEvents={{ 'click': onChartClick }} />
                                    <p className="text-center text-slate-400 text-sm mt-4">{t('chart_hint')}</p>
                                </>
                            )}
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
                                                        <span className="metric-value metric-success">{formatPercentValue(selectedPoint.return)}</span>
                                                    </div>
                                                    <div className="detail-row">
                                                        <span>{t('expected_risk')}</span>
                                                        <span className="metric-value metric-warning">{formatPercentValue(selectedPoint.risk)}</span>
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

                                        <button type="button" aria-expanded={showAdvancedParams} aria-controls="advanced-settings" className="text-link-btn advanced-toggle" onClick={() => setShowAdvancedParams(!showAdvancedParams)}>
                                            <Settings size={14} />
                                            {showAdvancedParams ? t('collapse_advanced') : t('expand_advanced')}
                                        </button>

                                        {showAdvancedParams && (
                                            <div className="settings-panel" id="advanced-settings">
                                                <p className="settings-help">{t('settings_intro')}</p>
                                                <div className="settings-fields">
                                                    <div className="form-group">
                                                            <label className="form-label text-xs" htmlFor="setting-minimum_cash_reserve">{t('minimum_cash_reserve')}</label>
                                                            <input id="setting-minimum_cash_reserve" className="form-input text-sm" type="number" step="100" min="0" value={minimumCashReserve} onChange={(e) => { setMinimumCashReserve(e.target.value); localStorage.setItem('minimumCashReserve', e.target.value); }} />
                                                            <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('minimum_cash_reserve_help')}</p>
                                                        </div>
                                                    <div className="form-group">
                                                    <label className="form-label text-xs" htmlFor="available-cash">{t('available_cash')}</label>
                                                    <input id="available-cash" className="form-input text-sm" type="number" min="0" value={availableCash} onChange={e => { setAvailableCash(e.target.value); localStorage.setItem('availableCash', e.target.value); }} />
                                                </div>
                                                </div>
                                                <details className="settings-disclosure">
                                                    <summary>{t('settings_transactions')}<span className="settings-summary">{exceptionCodes.length || Number(pendingProceeds) ? t('settings_has_values') : t('settings_only_if_needed')}</span></summary>
                                                    <div className="settings-body">
                                                        <div className="form-group">
                                                    <label className="form-label text-xs" htmlFor="pending-proceeds">{t('pending_proceeds')}</label>
                                                    <input id="pending-proceeds" className="form-input text-sm" type="number" min="0" value={pendingProceeds} onChange={e => { setPendingProceeds(e.target.value); localStorage.setItem('pendingProceeds', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400">{t('pending_proceeds_help')}</p>
                                                </div>
                                                        <p className="settings-help">{t('settings_exceptions_help')}</p>
                                                        <div className="fund-exceptions">
                                                            {exceptionCodes.map(code => (
                                                                <div className="fund-exception-row" key={code}>
                                                                    <div className="fund-exception-name">{fundNames[code] || code}<small>{code}</small></div>
                                                                    <div className="form-group">
                                                                        <label htmlFor={`pending-${code}`}>{t('settings_pending_short')}</label>
                                                                        <input id={`pending-${code}`} className="form-input" type="number" min="0" value={pendingSells[code] ?? ''} placeholder="0" onChange={e => updateException(code, e.target.value, pendingSells, setPendingSells, 'pendingSells')} />
                                                                    </div>
                                                                    <div className="form-group">
                                                                        <label htmlFor={`redeem-${code}`}>{t('settings_redemption_short')}</label>
                                                                        <input id={`redeem-${code}`} className="form-input" type="number" min="0" value={redemptionLimits[code] ?? ''} placeholder={t('settings_unlimited')} onChange={e => updateException(code, e.target.value, redemptionLimits, setRedemptionLimits, 'redemptionLimits')} />
                                                                    </div>
                                                                    <button type="button" className="text-link-btn exception-remove" onClick={() => removeException(code)} aria-label={`${t('settings_remove')} ${fundNames[code] || code}`}><X size={16} /></button>
                                                                </div>
                                                            ))}
                                                        </div>
                                                        {exceptionCodes.length < fundCodes.length && <div className="exception-add">
                                                            <label htmlFor="exception-fund">{t('settings_add_fund')}</label>
                                                            <select id="exception-fund" className="form-input" value="" onChange={e => { if (e.target.value) setAddedExceptionCodes(previous => [...previous, e.target.value]); }}>
                                                                <option value="">{t('settings_select_fund')}</option>
                                                                {fundCodes.filter(code => !exceptionCodes.includes(code)).map(code => <option key={code} value={code}>{fundNames[code] || code}</option>)}
                                                            </select>
                                                        </div>}
                                                    </div>
                                                </details>
                                                <details className="settings-disclosure">
                                                    <summary>{t('settings_schedule')}<span className="settings-summary">{purchaseDates || Number(plannedPurchaseDays) !== 1 ? t('settings_has_values') : t('settings_single_purchase')}</span></summary>
                                                    <div className="settings-body settings-fields">
                                                        <div className="form-group">
                                                    <label className="form-label text-xs" htmlFor="setting-planned_purchase_days">{t('planned_purchase_days')}</label>
                                                    <input id="setting-planned_purchase_days" className="form-input text-sm" type="number" step="1" min="1" value={plannedPurchaseDays} onChange={(e) => { setPlannedPurchaseDays(e.target.value); localStorage.setItem('plannedPurchaseDays', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('planned_purchase_days_help')}</p>
                                                </div>
                                                        <div className="form-group">
                                                    <label className="form-label text-xs" htmlFor="purchase-dates">{t('purchase_dates')}</label>
                                                    <input id="purchase-dates" className="form-input text-sm" value={purchaseDates} placeholder="2026-09-21,2026-09-22" onChange={e => setPurchaseDates(e.target.value)} />
                                                </div>
                                                    </div>
                                                </details>
                                                <details className="settings-disclosure">
                                                    <summary>{t('settings_model')}<span className="settings-summary">{t('settings_model_summary')}</span></summary>
                                                    <div className="settings-body settings-fields">
                                                        <div className="form-group">
                                                    <label className="form-label text-xs" htmlFor="rebalance-band">{t('rebalance_band')}</label>
                                                    <input id="rebalance-band" className="form-input text-sm" type="number" min="0" max="100" step="0.5" value={rebalanceBand} onChange={e => { setRebalanceBand(e.target.value); localStorage.setItem('rebalanceBand', e.target.value); }} />
                                                    <p className="text-[11px] text-slate-400">{t('rebalance_band_help')}</p>
                                                </div>
                                                        <div className="form-group">
                                                            <label className="form-label text-xs" htmlFor="setting-estimation_window">{t('estimation_window')}</label>
                                                            <input id="setting-estimation_window" className="form-input text-sm" type="number" step="1" min="6" value={estimationWindow} onChange={(e) => { setEstimationWindow(e.target.value); localStorage.setItem('estimationWindow', e.target.value); }} />
                                                            <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('estimation_window_help')}</p>
                                                        </div>
                                                        <div className="form-group">
                                                            <label className="form-label text-xs">{t('constraint_priority_note')}</label>
                                                        </div>
                                                        <div className="form-group">
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
                                                                <div className="form-group">
                                                                    <label className="form-label text-xs">{t('risk_horizon_days')}</label>
                                                                    <input className="form-input text-sm" type="number" step="1" min="5" max="63" value={riskHorizonDays} onChange={(e) => { setRiskHorizonDays(e.target.value); localStorage.setItem('riskHorizonDays', e.target.value); }} />
                                                                    <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('risk_horizon_days_help')}</p>
                                                                </div>
                                                            </>
                                                        )}
                                                        <div className="form-group">
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
                                                            <div className="form-group">
                                                                <label className="form-label text-xs">{t('max_drawdown_limit')}</label>
                                                                <input className="form-input text-sm" type="number" step="1" min="1" max="99" value={maxDrawdownLimit} onChange={(e) => { setMaxDrawdownLimit(e.target.value); localStorage.setItem('maxDrawdownLimit', e.target.value); }} />
                                                                <p className="text-[11px] text-slate-400 mt-1 leading-4">{t('max_drawdown_limit_help')}</p>
                                                            </div>
                                                        )}


                                                    </div>
                                                </details>
                                            </div>
                                        )}

                                        <button className="btn btn-primary strategy-submit" onClick={handleStrategySubmit} disabled={loading.strategy || loading.recommendation || !selectedPoint}>
                                            {loading.strategy || loading.recommendation ? t('analyzing') : t('start_analysis_btn')}
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
                                            <div className="backtest-note-line">3. {t('backtest_note_fixed_target_theory')}</div>
                                            {strategyResult.actual_fixed_target && (
                                                <div className="backtest-note-line">4. {t('backtest_note_fixed_target_actual')}</div>
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
                                        <div className="stat-label text-emerald-400">{t('fixed_target_theory')}</div>
                                        <div className="stat-value text-emerald-400">{((strategyResult.ideal_fixed_target || strategyResult.fixed_target).annualized_return * 100).toFixed(2)}%</div>
                                        <div className="text-xs text-emerald-600 mt-1">{t('max_drawdown')}: {formatDD(strategyResult.ideal_fixed_target || strategyResult.fixed_target, 'max_drawdown_value', 'max_drawdown')}</div>
                                    </div>
                                    {strategyResult.actual_fixed_target && (
                                        <div className="stat-item border-l-4 border-amber-500 bg-amber-900/10">
                                            <div className="stat-label text-amber-400">{t('fixed_target_actual')}</div>
                                            <div className="stat-value text-amber-400">{(strategyResult.actual_fixed_target.annualized_return * 100).toFixed(2)}%</div>
                                            <div className="text-xs text-amber-600 mt-1">{t('max_drawdown')}: {formatDD(strategyResult.actual_fixed_target, 'max_drawdown_value', 'max_drawdown')}</div>
                                        </div>
                                    )}
                                </div>

                                <div className="chart-grid">
                                    <div><ReactECharts option={getStrategyChartOptions('ideal_fixed_target')} style={{ height: 300 }} /></div>
                                    {strategyResult.actual_fixed_target && (
                                        <div><ReactECharts option={getStrategyChartOptions('actual_fixed_target')} style={{ height: 300 }} /></div>
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
                                                                        <th>{t('wf_actual_position')}</th>
                                                                        <th>{t('wf_target_weight_change')}</th>
                                                                        <th>{t('wf_actual_weight_change')}</th>
                                                                        <th>{t('wf_transmission')}</th>
                                                                    </tr>
                                                                </thead>
                                                                <tbody>
                                                                    {(full.data_access_audit || []).map((month) => (
                                                                        <tr key={month.realized_date}>
                                                                            <td>{month.realized_date?.slice(0, 7)}</td>
                                                                            <td>{formatPercentValue(month.actual_fund_position)}</td>
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

                    <MonthlyRecommendation recommendationResult={recommendationResult} onExport={handleExport} />
                </div>
            )}
        </div>
    );
}

export default PortfolioOptimizer;

export { sanitizeLegacyHoldings, buildAssetCategoriesPayload, buildSubstituteForPayload, getRecommendedFrontierPoint, getRecommendationEvidence } from "./portfolioViewUtils";
