import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import { Plus, X, ArrowRight, Settings, Info, TrendingUp, DollarSign, Wallet, Calendar } from 'lucide-react';

const getISODate = (date) => date.toISOString().split('T')[0];
const formatDD = (obj, key, fallbackKey) => {
    const val = obj?.[key] ?? obj?.[fallbackKey];
    if (val === undefined || val === null) return '--';
    return `${(val * 100).toFixed(2)}%`;
};

function PortfolioOptimizer() {
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
    const [maxBuyMultiplier, setMaxBuyMultiplier] = useState(() => localStorage.getItem('maxBuyMultiplier') || 3.0);
    const [sellThreshold, setSellThreshold] = useState(() => localStorage.getItem('sellThreshold') || 5.0); // pct
    const [minWeight, setMinWeight] = useState(() => localStorage.getItem('minWeight') || 30); // pct
    const [maxWeight, setMaxWeight] = useState(() => localStorage.getItem('maxWeight') || 80); // pct
    const [maWindow, setMaWindow] = useState(() => localStorage.getItem('maWindow') || 12);
    const [strategyResult, setStrategyResult] = useState(null);
    const [recommendationResult, setRecommendationResult] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState({ analysis: false, strategy: false, recommendation: false });
    const [showStrategyFrontier, setShowStrategyFrontier] = useState(false);

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

            const payload = {
                fund_codes: fundCodes,
                fund_fees: feesAsFloats,
                start_date: startDate,
                end_date: endDate,
                risk_free_rate: hasRiskFree ? (parseFloat(riskFreeRate) || 0) / 100 : null,
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
                max_buy_multiplier: parseFloat(maxBuyMultiplier),
                sell_threshold: parseFloat(sellThreshold) / 100,
                min_weight: parseFloat(minWeight) / 100,
                max_weight: parseFloat(maxWeight) / 100,
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
        if (!selectedPoint || !monthlyInvestment) return;
        setStrategyResult(null);
        setRecommendationResult(null);
        await Promise.all([runBacktests(selectedPoint.weights), getRecommendation()]);
    };


    const onChartClick = (params) => {
        const [chartRisk, chartReturn, weights, _maxDrawdown, originalRisk] = params.data;
        // Use originalRisk if available (Strategy View), otherwise use chartRisk (Theoretical View)
        const relevantRisk = originalRisk !== undefined ? originalRisk : chartRisk;

        setSelectedPoint({ risk: relevantRisk, return: chartReturn, weights });
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
                const riskLevel = (relevantRisk - minRisk) / (maxRisk - minRisk); // 0 to 1
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

        let frontierData;
        let xName, yName, titleSuffix;

        if (showStrategyFrontier && analysisResult.strategy_frontier) {
            // Strategy Frontier: [Risk(Vol), Return(Annualized), Weights, MaxDD, OriginalRisk]
            frontierData = analysisResult.strategy_frontier.map(p => [
                p.risk,
                p.return,
                p.weights,
                p.max_drawdown,
                p.original_risk
            ]);
            xName = '实际策略波动率 (Annualized Vol)';
            yName = '策略回测年化回报 (DCA Annualized)';
            titleSuffix = ' - VA/Kelly 实测数据';
        } else {
            // Theoretical: [Risk, Return, Weights, null, OriginalRisk]
            frontierData = analysisResult.efficient_frontier.map(p => [
                p.risk,
                p.return,
                p.weights,
                null,
                p.risk
            ]);
            xName = '理论波动率 (Annualized Vol)';
            yName = '理论预期回报 (Expected Return)';
            titleSuffix = ' - 现代投资组合理论';
        }

        return {
            backgroundColor: 'transparent',
            textStyle: { color: '#F8FAFC' },
            title: { text: `有效前沿${titleSuffix}`, left: 'center', textStyle: { fontSize: 16, color: '#F8FAFC' } },
            tooltip: {
                formatter: (p) => {
                    const risk = (p.data[0] * 100).toFixed(2);
                    const ret = (p.data[1] * 100).toFixed(2);
                    if (showStrategyFrontier) {
                        const dd = (p.data[3] * 100).toFixed(2);
                        return `<b>VA/Kelly 策略回测:</b><br/>年化回报: ${ret}%<br/>实际波动: ${risk}%<br/>最大回撤: ${dd}%`;
                    } else {
                        return `<b>理论组合预期:</b><br/>预期回报: ${ret}%<br/>预期风险: ${risk}%`;
                    }
                }
            },
            xAxis: {
                type: 'value',
                name: xName,
                axisLabel: { formatter: (v) => `${(v * 100).toFixed(1)}%`, color: '#94A3B8' },
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
            series: [{ type: 'scatter', data: frontierData, symbolSize: 10, itemStyle: { color: showStrategyFrontier ? '#EF4444' : '#3B82F6' } }]
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
            'lump_sum': '攒钱一次投 (Lump Sum)',
            'dca': '月月投 (DCA)',
            'ideal_kelly_dca': 'VA/Kelly (理论配置)',
            'actual_kelly_dca': 'VA/Kelly (实际持仓)'
        };
        return {
            backgroundColor: 'transparent',
            textStyle: { color: '#F8FAFC' },
            title: { text: titleMap[strategyType] || 'Strategy Attribution', left: 'center', textStyle: { color: '#F8FAFC' } },
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#6a7985' } } },
            legend: { data: assetCodes.map(code => getAssetName(code)), top: 30, type: 'scroll', textStyle: { color: '#94A3B8' } },
            grid: { top: 70, left: '3%', right: '4%', bottom: '3%', containLabel: true },
            xAxis: { type: 'category', boundaryGap: false, data: dates, axisLabel: { color: '#94A3B8' } },
            yAxis: { type: 'value', axisLabel: { formatter: '¥{value}', color: '#94A3B8' }, splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } } },
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
                                <h3 className="card-title"><Wallet size={20} className="card-icon" /> 资产配置</h3>
                            </div>

                            <div className="form-group">
                                <label className="form-label" htmlFor="fundCodeInput">添加风险资产 (基金代码)</label>
                                <div className="flex gap-2">
                                    <input
                                        type="text"
                                        id="fundCodeInput"
                                        className="form-input"
                                        value={currentInput}
                                        onChange={(e) => setCurrentInput(e.target.value)}
                                        onKeyPress={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddFundCode(); } }}
                                        placeholder="输入代码"
                                        style={{ flex: 1 }}
                                    />
                                    <button type="button" className="btn btn-primary" onClick={handleAddFundCode}>
                                        <Plus size={16} /> 添加
                                    </button>
                                </div>
                            </div>

                            <div className="form-group">
                                <button type="button" className="btn btn-secondary w-full" onClick={handleAddRiskFree} disabled={hasRiskFree}>
                                    + 添加无风险资产
                                </button>
                            </div>

                            {(fundCodes.length > 0 || hasRiskFree) && <div className="border-t border-glass my-4"></div>}

                            {hasRiskFree && (
                                <div className="risk-free-row">
                                    <span className="risk-free-label">无风险资产</span>
                                    <span className="text-xs text-slate-400">年化回报率 (%)</span>
                                    <input type="number" className="form-input risk-free-input" value={riskFreeRate} onChange={(e) => handleRiskFreeRateChange(e.target.value)} placeholder="%" />
                                    <button type="button" className="icon-btn" onClick={() => handleRemoveAsset('RiskFree')}><X size={16} /></button>
                                </div>
                            )}

                            <div className="space-y-3 max-h-[400px] overflow-y-auto pr-2">
                                {fundCodes.length > 0 && (
                                    <>
                                        <>
                                            <div className="asset-list-header">
                                                <div>基金</div>
                                                <div>申购%</div>
                                                <div>赎回%</div>
                                                <div>管理%</div>
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
                                <h3 className="card-title"><Calendar size={20} className="card-icon" /> 回测参数</h3>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="form-group">
                                    <label className="form-label">开始日期</label>
                                    <input type="date" className="form-input" value={startDate} onChange={(e) => { setStartDate(e.target.value); localStorage.setItem('startDate', e.target.value); }} />
                                </div>
                                <div className="form-group">
                                    <label className="form-label">结束日期</label>
                                    <input type="date" className="form-input" value={endDate} onChange={(e) => { setEndDate(e.target.value); localStorage.setItem('endDate', e.target.value); }} />
                                </div>
                            </div>
                            <div className="flex gap-2 mt-4">
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(1)}>近1年</button>
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(3)}>近3年</button>
                                <button type="button" className="btn btn-secondary text-sm py-1" onClick={() => setDateRange(5)}>近5年</button>
                            </div>
                        </div>
                        <button type="submit" className="btn btn-primary w-full py-4 text-lg shadow-lg-glow" disabled={loading.analysis || (fundCodes.length === 0 && !hasRiskFree)}>
                            {loading.analysis ? '分析中...' : '1. 寻找最优策略'} <ArrowRight size={20} />
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
                                <h3 className="card-title"><TrendingUp size={20} className="card-icon" /> 2. 选择目标组合 (Asset Allocation)</h3>
                                <div className="toggle-container">
                                    <span className={`toggle-label ${!showStrategyFrontier ? 'active' : ''}`}>理论预期</span>
                                    <div className={`toggle-switch ${showStrategyFrontier ? 'checked' : ''}`} onClick={() => setShowStrategyFrontier(!showStrategyFrontier)}></div>
                                    <span className={`toggle-label ${showStrategyFrontier ? 'active' : ''}`}>策略实测</span>
                                </div>
                            </div>

                            {analysisResult.warnings?.length > 0 && (
                                <div className="mb-4 p-4 bg-yellow-500/10 border border-yellow-500/20 rounded-lg">
                                    {analysisResult.warnings.map((w, i) => <p key={i} className="text-yellow-500 text-sm flex items-center gap-2"><Info size={14} /> {w}</p>)}
                                </div>
                            )}

                            <ReactECharts option={getFrontierOptions()} style={{ height: 400 }} onEvents={{ 'click': onChartClick }} />
                            <p className="text-center text-slate-400 text-sm mt-4">点击图表上的任意点选择目标配置</p>
                        </div>
                    </div>

                    {selectedPoint && (
                        <div className="full-width">
                            <div className="dashboard-card">
                                <div className="card-header">
                                    <h3 className="card-title"><Settings size={20} className="card-icon" /> 3. 策略详情 & 模拟配置</h3>
                                </div>
                                <div className="grid grid-cols-12 gap-8">
                                    <div className="col-span-4">
                                        <div className="p-4 bg-slate-900/50 rounded-lg mb-6">
                                            <h4 className="text-slate-400 text-sm uppercase mb-4">选定组合指标</h4>
                                            <div className="flex justify-between mb-2">
                                                <span>预期回报</span>
                                                <span className="text-emerald-400 font-mono font-bold">{(selectedPoint.return * 100).toFixed(2)}%</span>
                                            </div>
                                            <div className="flex justify-between">
                                                <span>预期风险</span>
                                                <span className="text-amber-400 font-mono font-bold">{(selectedPoint.risk * 100).toFixed(2)}%</span>
                                            </div>
                                        </div>

                                        <table className="data-table">
                                            <thead><tr><th>基金</th><th>目标权重</th></tr></thead>
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
                                        <h5 className="text-lg font-medium mb-4 flex items-center gap-2"><DollarSign size={18} className="text-sky-400" /> 当前持仓与月预算</h5>

                                        <div className="grid grid-cols-2 gap-6 mb-6">
                                            <div>
                                                <label className="form-label">当前闲置现金</label>
                                                <input type="number" className="form-input" value={currentCash} onChange={(e) => { setCurrentCash(e.target.value); localStorage.setItem('currentCash', e.target.value); }} placeholder="0" />
                                            </div>
                                            <div>
                                                <label className="form-label">每月定投预算</label>
                                                <input type="number" className="form-input" value={monthlyInvestment} onChange={(e) => { setMonthlyInvestment(e.target.value); localStorage.setItem('monthlyInvestment', e.target.value); }} placeholder="例如: 1000" />
                                            </div>
                                        </div>

                                        <div className="mb-6">
                                            <table className="data-table">
                                                <thead><tr><th>当前持仓 (元)</th><th>输入金额</th></tr></thead>
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
                                            {showAdvancedParams ? '收起高级设置' : '展开高级设置'}
                                        </button>

                                        {showAdvancedParams && (
                                            <div className="p-4 bg-slate-900/50 rounded-lg border border-slate-700/50 mb-6 grid grid-cols-2 gap-4">
                                                <div className="form-group">
                                                    <label className="form-label text-xs">最大买入倍数 (Max Buy Multiplier)</label>
                                                    <input className="form-input text-sm" type="number" step="0.1" value={maxBuyMultiplier} onChange={(e) => { setMaxBuyMultiplier(e.target.value); localStorage.setItem('maxBuyMultiplier', e.target.value); }} />
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label text-xs">卖出阈值 (Sell Threshold %)</label>
                                                    <input className="form-input text-sm" type="number" step="0.5" value={sellThreshold} onChange={(e) => { setSellThreshold(e.target.value); localStorage.setItem('sellThreshold', e.target.value); }} />
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label text-xs">最低持仓 (Min Weight %)</label>
                                                    <input className="form-input text-sm" type="number" step="5" value={minWeight} onChange={(e) => { setMinWeight(e.target.value); localStorage.setItem('minWeight', e.target.value); }} />
                                                </div>
                                                <div className="form-group">
                                                    <label className="form-label text-xs">最高持仓 (Max Weight %)</label>
                                                    <input className="form-input text-sm" type="number" step="5" value={maxWeight} onChange={(e) => { setMaxWeight(e.target.value); localStorage.setItem('maxWeight', e.target.value); }} />
                                                </div>
                                                <div className="form-group col-span-2">
                                                    <label className="form-label text-xs">均线窗口 (Months)</label>
                                                    <input className="form-input text-sm" type="number" step="1" value={maWindow} onChange={(e) => { setMaWindow(e.target.value); localStorage.setItem('maWindow', e.target.value); }} />
                                                </div>
                                            </div>
                                        )}

                                        <button className="btn btn-primary w-full" onClick={handleStrategySubmit} disabled={loading.strategy || !monthlyInvestment || !selectedPoint}>
                                            {loading.strategy ? '分析中...' : '开始分析 & 获取建议'}
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    )}

                    {strategyResult && (
                        <div className="full-width">
                            <div className="dashboard-card mb-6">
                                <div className="card-header">
                                    <h3 className="card-title">回测数据对比</h3>
                                </div>
                                <div className="stat-grid">
                                    <div className="stat-item">
                                        <div className="stat-label">Lump Sum 年化</div>
                                        <div className="stat-value">{(strategyResult.lump_sum.annualized_return * 100).toFixed(2)}%</div>
                                        <div className="text-xs text-slate-500 mt-1">最大回撤: {formatDD(strategyResult.lump_sum, 'max_drawdown_value', 'max_drawdown')}</div>
                                    </div>
                                    <div className="stat-item">
                                        <div className="stat-label">DCA 年化</div>
                                        <div className="stat-value">{(strategyResult.dca.annualized_return * 100).toFixed(2)}%</div>
                                        <div className="text-xs text-slate-500 mt-1">最大回撤: {formatDD(strategyResult.dca, 'max_drawdown_value', 'max_drawdown')}</div>
                                    </div>
                                    <div className="stat-item border-l-4 border-emerald-500 bg-emerald-900/10">
                                        <div className="stat-label text-emerald-400">VA/Kelly (理论)</div>
                                        <div className="stat-value text-emerald-400">{((strategyResult.ideal_kelly_dca || strategyResult.kelly_dca).annualized_return * 100).toFixed(2)}%</div>
                                        <div className="text-xs text-emerald-600 mt-1">最大回撤: {formatDD(strategyResult.ideal_kelly_dca || strategyResult.kelly_dca, 'max_drawdown_value', 'max_drawdown')}</div>
                                    </div>
                                    {strategyResult.actual_kelly_dca && (
                                        <div className="stat-item border-l-4 border-amber-500 bg-amber-900/10">
                                            <div className="stat-label text-amber-400">VA/Kelly (实际)</div>
                                            <div className="stat-value text-amber-400">{(strategyResult.actual_kelly_dca.annualized_return * 100).toFixed(2)}%</div>
                                            <div className="text-xs text-amber-600 mt-1">最大回撤: {formatDD(strategyResult.actual_kelly_dca, 'max_drawdown_value', 'max_drawdown')}</div>
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
                                    <h3 className="card-title text-xl text-emerald-400"><TrendingUp size={24} /> 智能投资建议</h3>
                                </div>

                                <div className="recommendation-header">
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">市场信号</div>
                                        <div className="recommendation-stat-value" style={{ color: recommendationResult.market_signal === 'undervalued' ? '#34D399' : recommendationResult.market_signal === 'overvalued' ? '#F87171' : '#FBBF24' }}>
                                            {recommendationResult.market_signal === 'undervalued' ? '低估 (机会)' : recommendationResult.market_signal === 'overvalued' ? '高估 (风险)' : '中性'}
                                        </div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">建议目标仓位</div>
                                        <div className="recommendation-stat-value">{(recommendationResult.target_equity_ratio * 100).toFixed(0)}%</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">建议本月投入</div>
                                        <div className="recommendation-stat-value text-white">¥{recommendationResult.recommended_monthly_investment.toFixed(2)}</div>
                                    </div>
                                    <div className="recommendation-stat">
                                        <div className="recommendation-stat-label">月预算</div>
                                        <div className="recommendation-stat-value text-slate-400">¥{recommendationResult.monthly_budget}</div>
                                    </div>
                                </div>

                                {recommendationResult.fund_advice && (
                                    <table className="recommendation-table">
                                        <thead>
                                            <tr>
                                                <th>基金</th>
                                                <th>动作</th>
                                                <th>金额</th>
                                                <th>目标仓位</th>
                                                <th>原因</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {recommendationResult.fund_advice.map(advice => (
                                                <tr key={advice.code}>
                                                    <td>{advice.name}</td>
                                                    <td>
                                                        <span className={`action-badge ${advice.action === 'Buy' ? 'buy' : advice.action === 'Sell' ? 'sell' : 'hold'}`}>
                                                            {advice.action === 'Buy' ? '买入' : advice.action === 'Sell' ? '卖出' : advice.action}
                                                        </span>
                                                    </td>
                                                    <td className="font-mono">¥{advice.amount.toFixed(2)}</td>
                                                    <td className="font-mono">¥{advice.target_holding?.toFixed(2) ?? '--'}</td>
                                                    <td className="text-slate-500 text-xs">{advice.reason}</td>
                                                </tr>
                                            ))}
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
