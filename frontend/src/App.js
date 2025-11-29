
import React, { useState, useEffect } from 'react';
import ReactECharts from 'echarts-for-react';
import './App.css';

const getISODate = (date) => date.toISOString().split('T')[0];

function App() {
    const [fundCodes, setFundCodes] = useState([]);
    const [fundFees, setFundFees] = useState({});
    const [currentInput, setCurrentInput] = useState('');
    const [hasRiskFree, setHasRiskFree] = useState(false);
    const [riskFreeRate, setRiskFreeRate] = useState('2.0');
    const [startDate, setStartDate] = useState(() => {
        const d = new Date();
        d.setFullYear(d.getFullYear() - 3);
        return getISODate(d);
    });
    const [endDate, setEndDate] = useState(getISODate(new Date()));
    const [analysisResult, setAnalysisResult] = useState(null);
    const [selectedPoint, setSelectedPoint] = useState(null);
    const [totalInvestmentAmount, setTotalInvestmentAmount] = useState('');
    const [monthlyInvestment, setMonthlyInvestment] = useState('');
    const [strategyResult, setStrategyResult] = useState(null);
    const [error, setError] = useState(null);
    const [loading, setLoading] = useState({ analysis: false, strategy: false, optimize: false });

    useEffect(() => {
        const savedFundCodes = localStorage.getItem('fundCodes');
        const savedFundFees = localStorage.getItem('fundFees');
        const savedHasRiskFree = localStorage.getItem('hasRiskFree');
        const savedRiskFreeRate = localStorage.getItem('riskFreeRate');
        if (savedFundCodes) setFundCodes(JSON.parse(savedFundCodes));
        if (savedFundFees) setFundFees(JSON.parse(savedFundFees));
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

    const handleRiskFreeRateChange = (rate) => {
        setRiskFreeRate(rate);
        localStorage.setItem('riskFreeRate', rate);
    }

    const setDateRange = (years) => {
        const end = new Date();
        const start = new Date();
        start.setFullYear(start.getFullYear() - years);
        setStartDate(getISODate(start));
        setEndDate(getISODate(end));
    };

    const handleAnalysisSubmit = async (e) => {
        e.preventDefault();
        setLoading({ ...loading, analysis: true });
        setError(null);
        setAnalysisResult(null);
        setSelectedPoint(null);
        setStrategyResult(null);
        setMonthlyInvestment('');
        setTotalInvestmentAmount('');

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

    const handleStrategySubmit = async () => {
        if (!selectedPoint || !monthlyInvestment) return;
        setLoading({ ...loading, strategy: true });
        setError(null);
        setStrategyResult(null);

        try {
            const feesAsFloats = Object.entries(fundFees).reduce((acc, [code, fee]) => {
                const parsedFee = parseFloat(fee);
                acc[code] = isNaN(parsedFee) ? 0 : parsedFee / 100;
                return acc;
            }, {});

            const payload = {
                fund_codes: fundCodes,
                weights: selectedPoint.weights,
                fund_fees: feesAsFloats,
                start_date: analysisResult.backtest_period.start_date,
                end_date: analysisResult.backtest_period.end_date,
                monthly_investment: parseFloat(monthlyInvestment),
                risk_free_rate: hasRiskFree ? (parseFloat(riskFreeRate) || 0) / 100 : null,
            };

            const response = await fetch('/api/backtest_strategies', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error((await response.json()).detail);
            setStrategyResult(await response.json());
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading({ ...loading, strategy: false });
        }
    };

    const handleOptimizeDCA = async () => {
        if (!analysisResult || !monthlyInvestment) return;
        setLoading({ ...loading, optimize: true });
        setError(null);
        setStrategyResult(null);

        try {
            const feesAsFloats = Object.entries(fundFees).reduce((acc, [code, fee]) => {
                const parsedFee = parseFloat(fee);
                acc[code] = isNaN(parsedFee) ? 0 : parsedFee / 100;
                return acc;
            }, {});

            const payload = {
                fund_codes: fundCodes,
                fund_fees: feesAsFloats,
                start_date: analysisResult.backtest_period.start_date,
                end_date: analysisResult.backtest_period.end_date,
                monthly_investment: parseFloat(monthlyInvestment),
                risk_free_rate: hasRiskFree ? (parseFloat(riskFreeRate) || 0) / 100 : null,
            };

            const response = await fetch('/api/optimize_dca', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error((await response.json()).detail);
            const data = await response.json();
            setSelectedPoint({ risk: data.risk, return: data.return, weights: data.weights });
            setStrategyResult(null);
            setTotalInvestmentAmount('');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading({ ...loading, optimize: false });
        }
    };

    const onChartClick = (params) => {
        const [risk, ret, weights] = params.data;
        setSelectedPoint({ risk, return: ret, weights });
        setStrategyResult(null);
        setTotalInvestmentAmount('');
    };

    const getFrontierOptions = () => {
        if (!analysisResult) return {};
        const frontierData = analysisResult.efficient_frontier.map(p => [p.risk, p.return, p.weights]);
        return {
            title: { text: '有效前沿 (点击图中的点以选择组合)', left: 'center', textStyle: { fontSize: 16 } },
            tooltip: { formatter: (p) => `<b>预期回报:</b> ${(p.data[1] * 100).toFixed(2)}%<br/><b>预期风险:</b> ${(p.data[0] * 100).toFixed(2)}%` },
            xAxis: { type: 'value', name: '风险 (年化波动率)', axisLabel: { formatter: (v) => `${(v * 100).toFixed(1)}%` } },
            yAxis: { type: 'value', name: '回报 (年化)', axisLabel: { formatter: (v) => `${(v * 100).toFixed(1)}%` } },
            series: [{ type: 'scatter', data: frontierData, symbolSize: 10 }]
        };
    };

    const getStrategyChartOptions = (strategyType) => {
        if (!strategyResult) return {};
        const attributionData = strategyResult[strategyType].attribution;
        const dates = Object.keys(attributionData).sort();
        const assetCodes = Object.keys(attributionData[dates[0]]);

        const series = assetCodes.map(code => ({
            name: analysisResult.fund_names[code] || code,
            type: 'line',
            stack: 'Total',
            areaStyle: {},
            emphasis: { focus: 'series' },
            data: dates.map(date => attributionData[date][code])
        }));

        return {
            title: { text: strategyType === 'dca' ? '月月投 (DCA) 收益归因' : '攒钱一次投 (Lump Sum) 收益归因', left: 'center' },
            tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { backgroundColor: '#6a7985' } } },
            legend: { data: assetCodes.map(code => analysisResult.fund_names[code] || code), top: 30, type: 'scroll' },
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
                                {(fundCodes.length > 0 || hasRiskFree) && <hr/>}
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
                                        <label>风险资产年化总费率 (%)</label>
                                        {fundCodes.map(code => (
                                            <div key={code} className="asset-row">
                                                <span className="asset-name">{analysisResult?.fund_names[code] || code}</span>
                                                <input type="number" value={fundFees[code] || ''} onChange={(e) => handleFeeChange(code, e.target.value)} placeholder="总费率" />
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
                                    <input type="date" id="startDate" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                                </div>
                                <div className="form-group">
                                    <label htmlFor="endDate">结束日期</label>
                                    <input type="date" id="endDate" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
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
                            <h3>2. 选择您的投资策略</h3>
                            <p>这是根据您选择的基金和输入的费率计算出的最优投资组合曲线。请点击图表上的点，以选择您偏好的风险回报组合。</p>
                            <ReactECharts option={getFrontierOptions()} style={{ height: 400 }} onEvents={{ 'click': onChartClick }} />
                        </div>

                        {selectedPoint && (
                            <div className="result-section">
                                <h3>3. 查看策略构成并计算投资金额</h3>
                                <p>您已选择一个预期回报为 <strong>{(selectedPoint.return * 100).toFixed(2)}%</strong>、预期风险为 <strong>{(selectedPoint.risk * 100).toFixed(2)}%</strong> 的投资组合。请输入您的总投资金额，以计算每只基金的具体分配额度。</p>
                                <div className="form-group">
                                    <label>总投资金额</label>
                                    <input type="number" value={totalInvestmentAmount} onChange={(e) => setTotalInvestmentAmount(e.target.value)} placeholder="例如: 50000" className="investment-input" />
                                </div>
                                <table className="allocation-table">
                                    <thead><tr><th>基金名称 (代码)</th><th>投资权重</th>{totalInvestmentAmount && <th>投资金额</th>}</tr></thead>
                                    <tbody>{Object.entries(selectedPoint.weights).map(([code, weight]) => <tr key={code}><td>{analysisResult.fund_names[code]} ({code})</td><td>{(weight * 100).toFixed(2)}%</td>{totalInvestmentAmount && <td>¥{(totalInvestmentAmount * weight).toFixed(2)}</td>}</tr>)}</tbody>
                                </table>
                                <hr />
                                <h3>4. 对比长期投资习惯</h3>
                                <p style={{marginTop: '20px'}}>现在，请输入您**每月**计划投资的金额，以对比“月月投”和“攒钱一次投”这两种长期投资习惯的最终效果。</p>
                                <div className="form-group investment-input-wrapper">
                                    <input type="number" value={monthlyInvestment} onChange={(e) => setMonthlyInvestment(e.target.value)} placeholder="例如: 1000" className="investment-input" />
                                    <button onClick={handleOptimizeDCA} disabled={loading.optimize || !monthlyInvestment || !analysisResult}>{loading.optimize ? '定投优化中...' : '定投最优权重'}</button>
                                    <button onClick={handleStrategySubmit} disabled={loading.strategy || !monthlyInvestment || !selectedPoint}>{loading.strategy ? '对比中...' : '开始对比'}</button>
                                </div>
                            </div>
                        )}

                        {strategyResult && (
                            <div className="result-section">
                                <h3>5. 最终回测对比</h3>
                                <div className="strategy-cards">
                                    <div className="summary-card"><h4>攒钱一次投 (Lump Sum)</h4><p><strong>总投入:</strong> ¥{strategyResult.lump_sum.total_invested.toFixed(2)}</p><p><strong>期末价值:</strong> ¥{strategyResult.lump_sum.final_value.toFixed(2)}</p><p><strong>总收益率:</strong> {((strategyResult.lump_sum.final_value / strategyResult.lump_sum.total_invested - 1) * 100).toFixed(2)}%</p><p><strong>最大回撤:</strong> {(strategyResult.lump_sum.max_drawdown * 100).toFixed(2)}%</p></div>
                                    <div className="summary-card"><h4>月月投 (DCA)</h4><p><strong>总投入:</strong> ¥{strategyResult.dca.total_invested.toFixed(2)}</p><p><strong>期末价值:</strong> ¥{strategyResult.dca.final_value.toFixed(2)}</p><p><strong>总收益率:</strong> {((strategyResult.dca.final_value / strategyResult.dca.total_invested - 1) * 100).toFixed(2)}%</p><p><strong>最大回撤:</strong> {(strategyResult.dca.max_drawdown * 100).toFixed(2)}%</p></div>
                                </div>
                                <div className="attribution-charts">
                                    <ReactECharts option={getStrategyChartOptions('lump_sum')} style={{ height: 400 }} />
                                    <ReactECharts option={getStrategyChartOptions('dca')} style={{ height: 400 }} />
                                </div>
                            </div>
                        )}
                    </div>
                )}
            </main>
        </div>
    );
}

export default App;
