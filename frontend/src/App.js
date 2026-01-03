
import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import './App.css';

const getISODate = (date) => date.toISOString().split('T')[0];
const formatDD = (obj, key, fallbackKey) => {
    const val = obj?.[key] ?? obj?.[fallbackKey];
    if (val === undefined || val === null) return '--';
    return `${(val * 100).toFixed(2)}%`;
};

function App() {
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
    const [buyFee, setBuyFee] = useState(() => localStorage.getItem('buyFee') || 0.15); // pct
    const [sellFee, setSellFee] = useState(() => localStorage.getItem('sellFee') || 0.5); // pct
    const [maWindow, setMaWindow] = useState(() => localStorage.getItem('maWindow') || 12);
    const [strategyResult, setStrategyResult] = useState(null);
    const [recommendationResult, setRecommendationResult] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState({ analysis: false, strategy: false, recommendation: false });
    const [autoTuned, setAutoTuned] = useState(false);
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

            const payload = {
                fund_codes: fundCodes,
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
        const [chartRisk, chartReturn, weights, maxDrawdown, originalRisk] = params.data;
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

                // Interpolate:
                // Low Risk (0) -> Min 40 / Max 80  (More defensive, but still valid)
                // High Risk (1) -> Min 90 / Max 100 (Almost fully invested to match high return expectation)
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
            setAutoTuned(true);

            // Auto expand advanced settings to show the change (optional, but good for visibility)
            // setShowAdvancedParams(true);
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
            title: { text: `有效前沿${titleSuffix}`, left: 'center', textStyle: { fontSize: 16 } },
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
            xAxis: { type: 'value', name: xName, axisLabel: { formatter: (v) => `${(v * 100).toFixed(1)}%` }, min: 'dataMin' },
            yAxis: { type: 'value', name: yName, axisLabel: { formatter: (v) => `${(v * 100).toFixed(1)}%` }, min: 'dataMin' },
            series: [{ type: 'scatter', data: frontierData, symbolSize: 10, itemStyle: { color: showStrategyFrontier ? '#d32f2f' : '#1976d2' } }]
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

        // Safety check for names
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
            areaStyle: {},
            emphasis: { focus: 'series' },
            data: dates.map(date => attributionData[date][code])
        }));

        const titleMap = {
            'lump_sum': '攒钱一次投 (Lump Sum) 收益归因',
            'dca': '月月投 (DCA) 收益归因',
            'kelly_dca': 'VA/Kelly 定投 (理论配置) 收益归因',
            'ideal_kelly_dca': 'VA/Kelly 定投 (理论配置) 收益归因',
            'actual_kelly_dca': 'VA/Kelly 定投 (实际持仓) 收益归因'
        };
        return {
            title: { text: titleMap[strategyType] || 'Strategy Attribution', left: 'center' },
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#6a7985' } } },
            legend: { data: assetCodes.map(code => getAssetName(code)), top: 30, type: 'scroll' },
            grid: { top: 70, left: '3%', right: '4%', bottom: '3%', containLabel: true },
            xAxis: { type: 'category', boundaryGap: false, data: dates },
            yAxis: { type: 'value', axisLabel: { formatter: '¥{value}' } },
            series: series
        };
    };



    return (
        <div className="App">
            <header className="App-header"><h1>Quant Compass</h1><p>您的量化投资导航</p></header>
            <main>
                <form onSubmit={handleAnalysisSubmit} className="main-form">
                    <div className="form-container">
                        <div className="left-column">
                            <div className="card">
                                <h3>资产配置</h3>
                                <div className="form-group">
                                    <label htmlFor="fundCodeInput">添加风险资产 (基金代码)</label>
                                    <div className="fund-input-wrapper"><input type="text" id="fundCodeInput" value={currentInput} onChange={(e) => setCurrentInput(e.target.value)} onKeyPress={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddFundCode(); } }} placeholder="输入代码后按 Enter" /><button type="button" onClick={handleAddFundCode}>添加</button></div>
                                </div>
                                <div className="form-group">
                                    <button type="button" className="link-button" onClick={handleAddRiskFree} disabled={hasRiskFree}>+ 添加无风险资产</button>
                                </div>
                                {(fundCodes.length > 0 || hasRiskFree) && <hr />}
                                {hasRiskFree && (
                                    <div className="asset-list">
                                        <label>无风险资产年化回报率 (%)</label>
                                        <div className="asset-row">
                                            <span className="asset-name">无风险资产</span>
                                            <input type="number" value={riskFreeRate} onChange={(e) => handleRiskFreeRateChange(e.target.value)} placeholder="回报率" />
                                            <button type="button" className="remove-btn" onClick={() => handleRemoveAsset('RiskFree')}>&times;</button>
                                        </div>
                                    </div>
                                )}
                                {fundCodes.length > 0 && (
                                    <div className="asset-list">
                                        <div className="asset-header-row" style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1fr 40px', gap: '5px', marginBottom: '5px', fontSize: '0.8em', color: '#666', fontWeight: 'bold' }}>
                                            <span>基金 (代码)</span>
                                            <span>申购 %</span>
                                            <span>赎回 %</span>
                                            <span>管理 %</span>
                                            <span></span>
                                        </div>
                                        {fundCodes.map(code => (
                                            <div key={code} className="asset-row" style={{ display: 'grid', gridTemplateColumns: '1.2fr 1fr 1fr 1fr 40px', gap: '5px', alignItems: 'center' }}>
                                                <span className="asset-name" title={analysisResult?.fund_names[code] || code}>{analysisResult?.fund_names[code] || code}</span>
                                                <input type="number" step="0.01" value={fundBuyFees[code] || ''} onChange={(e) => handleBuyFeeChange(code, e.target.value)} placeholder="0.15" />
                                                <input type="number" step="0.01" value={fundSellFees[code] || ''} onChange={(e) => handleSellFeeChange(code, e.target.value)} placeholder="0.5" />
                                                <input type="number" step="0.01" value={fundFees[code] || ''} onChange={(e) => handleFeeChange(code, e.target.value)} placeholder="0.6" />
                                                <button type="button" className="remove-btn" onClick={() => handleRemoveAsset(code)}>&times;</button>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        </div>
                        <div className="right-column">
                            <div className="card">
                                <h3>回测参数</h3>
                                <div className="form-group">
                                    <label htmlFor="startDate">开始日期</label>
                                    <input type="date" id="startDate" value={startDate} onChange={(e) => { setStartDate(e.target.value); localStorage.setItem('startDate', e.target.value); }} />
                                </div>
                                <div className="form-group">
                                    <label htmlFor="endDate">结束日期</label>
                                    <input type="date" id="endDate" value={endDate} onChange={(e) => { setEndDate(e.target.value); localStorage.setItem('endDate', e.target.value); }} />
                                </div>
                                <div className="date-shortcuts">
                                    <button type="button" onClick={() => setDateRange(1)}>近1年</button>
                                    <button type="button" onClick={() => setDateRange(3)}>近3年</button>
                                    <button type="button" onClick={() => setDateRange(5)}>近5年</button>
                                </div>
                            </div>
                            <button type="submit" className="submit-button" disabled={loading.analysis || (fundCodes.length === 0 && !hasRiskFree)}>{loading.analysis ? '分析中...' : '1. 寻找最优策略'}</button>
                        </div>
                    </div>
                </form>

                {error && <div className="error">{error}</div>}

                {analysisResult && (
                    <div className="result">
                        <h2>分析结果</h2>
                        {analysisResult.warnings?.length > 0 && <div className="result-section warning-section"><h3>注意事项</h3>{analysisResult.warnings.map((w, i) => <p key={i} className="warning">{w}</p>)}</div>}
                        <div className="result-section">

                            <h3>2. 选择目标组合 (Asset Allocation)</h3>
                            <p>这是有效前沿曲线。请点击图表上的任意一点，以选择您想要的<strong>目标资产配置比例</strong>（即每只基金的持仓权重）。</p>

                            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: '10px', gap: '15px' }}>
                                <span style={{ fontWeight: showStrategyFrontier ? 'normal' : 'bold', color: showStrategyFrontier ? '#ccc' : '#1976d2' }}>理论预期 (Theoretical)</span>
                                <label className="switch">
                                    <input type="checkbox" checked={showStrategyFrontier} onChange={(e) => setShowStrategyFrontier(e.target.checked)} />
                                    <span className="slider round"></span>
                                </label>
                                <span style={{ fontWeight: showStrategyFrontier ? 'bold' : 'normal', color: showStrategyFrontier ? '#d32f2f' : '#ccc' }}>策略实测 (VA/Kelly Actual)</span>
                            </div>

                            <ReactECharts option={getFrontierOptions()} style={{ height: 400 }} onEvents={{ 'click': onChartClick }} />
                        </div>

                        {selectedPoint && (
                            <div className="result-section">
                                <h3>3. 策略详情 (Strategy Details)</h3>
                                <p>您已选择一个预期回报为 <strong>{(selectedPoint.return * 100).toFixed(2)}%</strong>、预期风险为 <strong>{(selectedPoint.risk * 100).toFixed(2)}%</strong> 的投资组合。</p>
                                <table className="allocation-table">
                                    <thead><tr><th>基金名称 (代码)</th><th>目标权重</th></tr></thead>
                                    <tbody>{Object.entries(selectedPoint.weights).map(([code, weight]) => <tr key={code}><td>{analysisResult.fund_names[code]} ({code})</td><td>{(weight * 100).toFixed(2)}%</td></tr>)}</tbody>
                                </table>
                                <hr />
                                <h3>4. 模拟配置与回测 (Configuration & Backtest)</h3>
                                <p style={{ marginTop: '20px' }}>请输入您的**当前持仓**和**每月预算**，系统将为您生成具体的操作建议并进行回测对比。</p>

                                <h5 style={{ marginTop: '15px', color: '#555' }}>A. 输入当前持仓 (可选)</h5>
                                <table className="allocation-table">
                                    <thead><tr><th>基金名称 (代码)</th><th>当前持有金额 (元)</th></tr></thead>
                                    <tbody>{Object.entries(selectedPoint.weights).map(([code, weight]) => <tr key={code}><td>{analysisResult.fund_names[code]} ({code})</td><td><input type="number" value={initialHoldings[code] || ''} onChange={(e) => handleHoldingChange(code, e.target.value)} placeholder="0" style={{ width: '120px' }} /></td></tr>)}</tbody>
                                </table>
                                <div style={{ marginTop: '10px', display: 'flex', alignItems: 'center', gap: '10px' }}>
                                    <label>当前闲置现金 (Current Cash):</label>
                                    <input type="number" value={currentCash} onChange={(e) => { setCurrentCash(e.target.value); localStorage.setItem('currentCash', e.target.value); }} placeholder="0" style={{ width: '120px' }} />
                                </div>

                                {Object.values(initialHoldings).some(v => parseFloat(v) > 0) && (
                                    <p style={{ marginTop: '10px', padding: '10px', backgroundColor: '#e3f2fd', borderRadius: '4px' }}>
                                        <strong>💡 提示：</strong>您已填写当前持仓，"VA/Kelly 定投"策略将从您的现有仓位开始模拟，展示如何根据市场信号动态调整仓位。
                                    </p>
                                )}

                                <div className="form-group investment-input-wrapper" style={{ marginTop: '20px', borderTop: '1px dashed #ccc', paddingTop: '20px', display: 'flex', flexDirection: 'column', gap: '15px' }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                                        <label style={{ fontWeight: 'bold' }}>B. 每月定投预算:</label>
                                        <input type="number" value={monthlyInvestment} onChange={(e) => { setMonthlyInvestment(e.target.value); localStorage.setItem('monthlyInvestment', e.target.value); }} placeholder="例如: 1000" className="investment-input" />
                                    </div>

                                    <div style={{}}>
                                        <button className="text-button" type="button" onClick={() => setShowAdvancedParams(!showAdvancedParams)} style={{ background: 'none', border: 'none', color: '#007bff', cursor: 'pointer', padding: 0, fontSize: '0.9em', textDecoration: 'underline' }}>
                                            {showAdvancedParams ? '收起高级设置 ▲' : '展开高级设置 (Advanced Strategy Settings) ▼'}
                                        </button>
                                        {showAdvancedParams && (
                                            <div className="advanced-settings-panel" style={{ marginTop: '10px', padding: '15px', backgroundColor: '#f9f9f9', borderRadius: '8px', border: '1px solid #eee' }}>
                                                <p style={{ fontSize: '0.85em', color: '#666', marginBottom: '10px' }}>
                                                    <strong>说明：</strong> 定制 "VA/Kelly 定投 (价值平均)" 策略的激进程度和风险控制参数。
                                                    {autoTuned && <span style={{ color: 'green', marginLeft: '10px', fontWeight: 'bold' }}>✨ 已根据您的风险偏好自动优化参数</span>}
                                                </p>
                                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px' }}>
                                                    <div className="form-group">
                                                        <label title="单次最大买入倍数 (相对于月定投额)">最大买入倍数 (Max Buy Multiplier):</label>
                                                        <input type="number" step="0.1" value={maxBuyMultiplier} onChange={(e) => { setMaxBuyMultiplier(e.target.value); localStorage.setItem('maxBuyMultiplier', e.target.value); }} />
                                                    </div>
                                                    <div className="form-group">
                                                        <label title="卖出阈值 (偏差超过总资产的%)">卖出阈值 (Sell Threshold %):</label>
                                                        <input type="number" step="0.5" value={sellThreshold} onChange={(e) => { setSellThreshold(e.target.value); localStorage.setItem('sellThreshold', e.target.value); }} />
                                                    </div>
                                                    <div className="form-group">
                                                        <label title="高估时的最低持仓比例">最低持仓 (Min Weight %):</label>
                                                        <input type="number" step="5" value={minWeight} onChange={(e) => { setMinWeight(e.target.value); localStorage.setItem('minWeight', e.target.value); }} />
                                                    </div>
                                                    <div className="form-group">
                                                        <label title="低估时的最高持仓比例">最高持仓 (Max Weight %):</label>
                                                        <input type="number" step="5" value={maxWeight} onChange={(e) => { setMaxWeight(e.target.value); localStorage.setItem('maxWeight', e.target.value); }} />
                                                    </div>
                                                    <div className="form-group">
                                                        <label title="移动平均线窗口大小 (月)">均线窗口 (MA Window Months):</label>
                                                        <input type="number" step="1" value={maWindow} onChange={(e) => { setMaWindow(e.target.value); localStorage.setItem('maWindow', e.target.value); }} />
                                                    </div>
                                                </div>
                                                {parseInt(minWeight) > parseInt(maxWeight) && (
                                                    <div style={{ gridColumn: '1 / -1', color: 'red', marginTop: '5px', fontSize: '0.9em', fontWeight: 'bold' }}>
                                                        ⚠️ 错误：最低持仓 (Min Weight) 不能高于最高持仓 (Max Weight)
                                                    </div>
                                                )}
                                            </div>

                                        )}
                                    </div>

                                    <button onClick={handleStrategySubmit} disabled={loading.strategy || !monthlyInvestment || !selectedPoint} style={{ alignSelf: 'flex-start', padding: '10px 20px', fontSize: '1.1em' }}>
                                        {loading.strategy ? '分析中...' : '开始分析 & 获取建议 (Start Analysis)'}
                                    </button>
                                </div>
                            </div>
                        )}

                        {strategyResult && (
                            <div className="result-section">
                                <h3>5. 最终回测对比</h3>
                                <div style={{ marginBottom: '15px', padding: '10px', backgroundColor: '#fff3cd', borderRadius: '4px', fontSize: '0.9em' }}>
                                    <strong>为什么回测收益率 != 组合预期回报率？</strong><br />
                                    1. <strong>时间段不同</strong>：预期回报是历史长期平均，而回测是特定时间段（可能处于牛/熊市）。<br />
                                    2. <strong>资金占用</strong>：VA/Kelly 策略会持有现金（低风险低回报），拉低了牛市中的总收益率。
                                </div>
                                <div className="strategy-cards">
                                    <div className="summary-card">
                                        <h4>攒钱一次投 (Lump Sum)</h4>
                                        <p style={{ fontSize: '0.85em', color: '#666', marginBottom: '8px' }}>单笔全额买入，长期持有 (Buy & Hold)</p>
                                        <p><strong>总投入:</strong> ¥{strategyResult.lump_sum.total_invested.toFixed(2)}</p>
                                        <p><strong>期末价值:</strong> ¥{strategyResult.lump_sum.final_value.toFixed(2)}</p>
                                        <p><strong>年化收益率:</strong> {(strategyResult.lump_sum.annualized_return * 100).toFixed(2)}%</p>
                                        <p><strong>最大回撤(市值) <span title="基于账户总资产市值的回撤，反映实际金额的缩水程度 (受资金进出影响)。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.lump_sum, 'max_drawdown_value', 'max_drawdown')}</p>
                                        <p><strong>最大回撤(净值化) <span title="基于单位净值的回撤，排除资金进出影响，单纯反映策略本身的投资表现。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.lump_sum, 'max_drawdown_nav', 'max_drawdown')}</p>
                                    </div>
                                    <div className="summary-card">
                                        <h4>月月投 (DCA)</h4>
                                        <p style={{ fontSize: '0.85em', color: '#666', marginBottom: '8px' }}>每月定额定比买入，不进行再平衡</p>
                                        <p><strong>总投入:</strong> ¥{strategyResult.dca.total_invested.toFixed(2)}</p>
                                        <p><strong>期末价值:</strong> ¥{strategyResult.dca.final_value.toFixed(2)}</p>
                                        <p><strong>年化收益率:</strong> {(strategyResult.dca.annualized_return * 100).toFixed(2)}%</p>
                                        <p><strong>最大回撤(市值) <span title="基于账户总资产市值的回撤，反映实际金额的缩水程度 (受资金进出影响)。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.dca, 'max_drawdown_value', 'max_drawdown')}</p>
                                        <p><strong>最大回撤(净值化) <span title="基于单位净值的回撤，排除资金进出影响，单纯反映策略本身的投资表现。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.dca, 'max_drawdown_nav', 'max_drawdown')}</p>
                                    </div>
                                    <div className="summary-card" style={{ borderLeft: '5px solid #4caf50' }}>
                                        <h4>VA/Kelly (理论配置)</h4>
                                        <p style={{ fontSize: '0.85em', color: '#666', marginBottom: '8px' }}>假设初始资金按目标权重完美配置</p>
                                        <p><strong>总投入:</strong> ¥{(strategyResult.ideal_kelly_dca || strategyResult.kelly_dca).total_invested.toFixed(2)}</p>
                                        <p><strong>期末价值:</strong> ¥{(strategyResult.ideal_kelly_dca || strategyResult.kelly_dca).final_value.toFixed(2)}</p>
                                        <p><strong>年化收益率:</strong> {((strategyResult.ideal_kelly_dca || strategyResult.kelly_dca).annualized_return * 100).toFixed(2)}%</p>
                                        <p><strong>最大回撤(市值) <span title="基于账户总资产市值的回撤，反映实际金额的缩水程度 (受资金进出影响)。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.ideal_kelly_dca || strategyResult.kelly_dca, 'max_drawdown_value', 'max_drawdown')}</p>
                                        <p><strong>最大回撤(净值化) <span title="基于单位净值的回撤，排除资金进出影响，单纯反映策略本身的投资表现。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.ideal_kelly_dca || strategyResult.kelly_dca, 'max_drawdown_nav', 'max_drawdown')}</p>
                                    </div>
                                    {strategyResult.actual_kelly_dca && (
                                        <div className="summary-card" style={{ borderLeft: '5px solid #ff9800' }}>
                                            <h4>VA/Kelly (实际持仓)</h4>
                                            <p style={{ fontSize: '0.85em', color: '#666', marginBottom: '8px' }}>基于您输入的真实持仓进行回测</p>
                                            <p><strong>总投入:</strong> ¥{strategyResult.actual_kelly_dca.total_invested.toFixed(2)}</p>
                                            <p><strong>期末价值:</strong> ¥{strategyResult.actual_kelly_dca.final_value.toFixed(2)}</p>
                                            <p><strong>年化收益率:</strong> {(strategyResult.actual_kelly_dca.annualized_return * 100).toFixed(2)}%</p>
                                            <p><strong>最大回撤(市值) <span title="基于账户总资产市值的回撤，反映实际金额的缩水程度 (受资金进出影响)。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.actual_kelly_dca, 'max_drawdown_value', 'max_drawdown')}</p>
                                            <p><strong>最大回撤(净值化) <span title="基于单位净值的回撤，排除资金进出影响，单纯反映策略本身的投资表现。" style={{ cursor: 'help', textDecoration: 'underline dotted', fontSize: '0.8em', color: '#888' }}>[?]</span>:</strong> {formatDD(strategyResult.actual_kelly_dca, 'max_drawdown_nav', 'max_drawdown')}</p>
                                        </div>
                                    )}
                                </div>
                                <div className="attribution-charts">
                                    <div><ReactECharts option={getStrategyChartOptions('lump_sum')} style={{ height: 400 }} /></div>
                                    <div><ReactECharts option={getStrategyChartOptions('dca')} style={{ height: 400 }} /></div>
                                    <div><ReactECharts option={getStrategyChartOptions('ideal_kelly_dca')} style={{ height: 400 }} /></div>
                                    {strategyResult.actual_kelly_dca && (
                                        <div><ReactECharts option={getStrategyChartOptions('actual_kelly_dca')} style={{ height: 400 }} /></div>
                                    )}
                                </div>
                            </div>
                        )}

                        {recommendationResult && (
                            <div className="result-section" style={{ backgroundColor: '#f0f9ff', border: '1px solid #b3e5fc' }}>
                                <h3>🎯 当前投资建议 (实时)</h3>
                                <div className="recommendation-content" style={{ display: 'flex', gap: '20px', alignItems: 'flex-start' }}>
                                    <div className="signal-box" style={{ flex: 1, padding: '15px', backgroundColor: 'white', borderRadius: '8px', boxShadow: '0 2px 4px rgba(0,0,0,0.05)' }}>
                                        <h4>市场信号</h4>
                                        <div style={{ fontSize: '1.2em', fontWeight: 'bold', color: recommendationResult.market_signal === 'undervalued' ? 'green' : recommendationResult.market_signal === 'overvalued' ? 'red' : '#fbc02d' }}>
                                            {recommendationResult.market_signal === 'undervalued' ? '🟢 低估 (机会)' : recommendationResult.market_signal === 'overvalued' ? '🔴 高估 (风险)' : '🟡 中性 (正常)'}
                                        </div>
                                        <p style={{ margin: '10px 0', fontSize: '0.9em', color: '#666' }}>
                                            当前价格 ¥{recommendationResult.current_price.toFixed(2)} <br />
                                            MA窗口均线 ¥{recommendationResult.ma_value.toFixed(2)}
                                        </p>
                                        <p><strong>目标仓位:</strong> {(recommendationResult.target_equity_ratio * 100).toFixed(0)}%</p>
                                    </div>

                                    <div className="action-box" style={{ flex: 2, padding: '15px', backgroundColor: 'white', borderRadius: '8px', boxShadow: '0 2px 4px rgba(0,0,0,0.05)' }}>
                                        <h4>建议操作</h4>
                                        <div style={{ marginBottom: '10px', fontSize: '0.9em', color: '#555' }}>
                                            <p>当前权益市值: <strong>¥{recommendationResult.current_equity_value.toFixed(2)}</strong></p>
                                            <p>当前无风险资产: <strong>¥{(recommendationResult.current_risk_free_value || 0).toFixed(2)}</strong></p>
                                            <p>当前闲置现金: <strong>¥{(recommendationResult.current_cash || 0).toFixed(2)}</strong></p>
                                            <p style={{ marginTop: '5px' }}>总财富 (含本月预算): <strong>¥{(recommendationResult.current_equity_value + (recommendationResult.current_risk_free_value || 0) + (recommendationResult.current_cash || 0) + recommendationResult.monthly_budget).toFixed(2)}</strong></p>
                                        </div>
                                        <p>根据信号，目标权益市值应为: <strong>¥{recommendationResult.target_equity_value.toFixed(2)}</strong></p>
                                        <hr style={{ margin: '10px 0', borderTop: '1px dashed #eee' }} />

                                        {recommendationResult.gap > 0 ? (
                                            <div>
                                                <p style={{ fontSize: '1.1em' }}>建议您本月投资：<span style={{ color: 'green', fontWeight: 'bold', fontSize: '1.3em' }}>¥{recommendationResult.recommended_monthly_investment.toFixed(2)}</span></p>
                                                {recommendationResult.recommended_monthly_investment > recommendationResult.monthly_budget && (
                                                    <p style={{ fontSize: '0.9em', color: '#ff9800' }}>⚠️ 建议金额超过了您的月预算，建议您尽可能多投。</p>
                                                )}
                                                {recommendationResult.recommended_monthly_investment < recommendationResult.monthly_budget && (
                                                    <p style={{ fontSize: '0.9em', color: '#2196f3' }}>ℹ️ 建议金额小于您的月预算，剩余资金可留作现金储备。</p>
                                                )}
                                            </div>
                                        ) : (
                                            <div>
                                                <p style={{ fontSize: '1.1em', color: 'red' }}><strong>建议本月暂停投入，或卖出部分持仓。</strong></p>
                                                <p>当前仓位已过高 (超出目标 ¥{Math.abs(recommendationResult.gap).toFixed(2)})。</p>
                                            </div>
                                        )}

                                        {recommendationResult.fund_advice && (
                                            <>
                                                <h5 style={{ marginTop: '20px', marginBottom: '10px' }}>👇 具体操作计划 (基于本月预算 ¥{recommendationResult.monthly_budget})</h5>
                                                <table className="allocation-table" style={{ fontSize: '0.9em' }}>
                                                    <thead>
                                                        <tr>
                                                            <th>基金名称</th>
                                                            <th>当前持仓</th>
                                                            <th>目标持仓</th>
                                                            <th>差额</th>
                                                            <th>建议操作</th>
                                                            <th>为什么？</th>
                                                            <th>执行金额</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody>
                                                        {recommendationResult.fund_advice.map(advice => (
                                                            <tr key={advice.code} style={{ backgroundColor: advice.action === 'Buy' ? '#e8f5e9' : advice.action === 'Sell' ? '#ffebee' : 'transparent' }}>
                                                                <td>{advice.name}</td>
                                                                <td>¥{advice.current_holding.toFixed(0)}</td>
                                                                <td>¥{advice.target_holding.toFixed(0)}</td>
                                                                <td style={{ color: advice.gap > 0 ? 'green' : 'red' }}>{advice.gap > 0 ? '+' : ''}{advice.gap.toFixed(0)}</td>
                                                                <td style={{ fontWeight: 'bold', color: advice.action === 'Buy' || advice.action === '存入' ? 'green' : advice.action === 'Sell' ? 'red' : 'black' }}>
                                                                    {advice.action === 'Buy' ? '买入' : advice.action === 'Sell' ? '卖出' : advice.action === '存入' ? '存入' : '持有'}
                                                                </td>
                                                                <td style={{ fontSize: '0.85em', color: '#666' }}>{advice.reason}</td>
                                                                <td>
                                                                    {advice.amount > 0 ? (
                                                                        <span style={{ fontWeight: 'bold' }}>¥{advice.amount.toFixed(2)}</span>
                                                                    ) : '-'}
                                                                </td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}
                    </div>
                )
                }
            </main >
        </div >
    );
}

export default App;
