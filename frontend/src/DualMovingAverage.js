import React, { useState } from 'react';
import { useLanguage } from './LanguageContext';
import ReactECharts from 'echarts-for-react';
import axios from 'axios';
import { Activity } from 'lucide-react';

function DualMovingAverage() {
    const { t } = useLanguage();
    const [symbol, setSymbol] = useState('');
    const [chartOptions, setChartOptions] = useState({});
    const [shortWindow, setShortWindow] = useState(5);
    const [longWindow, setLongWindow] = useState(20);
    const [loading, setLoading] = useState(false);

    const handleSearch = async () => {
        setLoading(true);
        try {
            // Fetch basic stock data
            const response = await axios.get(`http://localhost:8000/stock/${symbol}`);
            const data = response.data;

            // Fetch backtesting data
            const strategyResponse = await axios.get(`http://localhost:8000/strategy/dual-moving-average/${symbol}?short_window=${shortWindow}&long_window=${longWindow}`);
            const strategyData = strategyResponse.data;

            setChartOptions({
                backgroundColor: 'transparent',
                textStyle: { color: '#F8FAFC' },
                title: { text: `${t('chart_title')} (${symbol})`, left: 'center', textStyle: { color: '#F8FAFC' } },
                legend: {
                    data: [t('legend_close'), t('legend_short'), t('legend_long'), t('legend_buy'), t('legend_sell')],
                    top: 30,
                    textStyle: { color: '#94A3B8' }
                },
                grid: { top: 70, left: '3%', right: '4%', bottom: '3%', containLabel: true },
                xAxis: {
                    type: 'category',
                    data: data.dates,
                    axisLabel: { color: '#94A3B8' },
                    splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
                },
                yAxis: {
                    type: 'value',
                    scale: true,
                    axisLabel: { color: '#94A3B8' },
                    splitLine: { lineStyle: { color: 'rgba(255,255,255,0.05)' } }
                },
                tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
                series: [
                    {
                        name: t('legend_close'),
                        data: data.prices,
                        type: 'line',
                        lineStyle: { color: '#ffffff', width: 1 }
                    },
                    {
                        name: t('legend_short'),
                        data: strategyData.short_ma,
                        type: 'line',
                        smooth: true,
                        itemStyle: { color: '#F59E0B' }
                    },
                    {
                        name: t('legend_long'),
                        data: strategyData.long_ma,
                        type: 'line',
                        smooth: true,
                        itemStyle: { color: '#3B82F6' }
                    },
                    {
                        name: t('legend_buy'),
                        type: 'scatter',
                        symbol: 'arrow',
                        symbolSize: 15,
                        itemStyle: { color: '#10B981' },
                        data: strategyData.buy_signals.map(d => [d.date, d.price])
                    },
                    {
                        name: t('legend_sell'),
                        type: 'scatter',
                        symbol: 'arrow',
                        symbolRotate: 180,
                        symbolSize: 15,
                        itemStyle: { color: '#EF4444' },
                        data: strategyData.sell_signals.map(d => [d.date, d.price])
                    }
                ]
            });
        } catch (error) {
            console.error("Error fetching data:", error);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="app-main">
            <div className="full-width">
                <div className="dashboard-card mb-6">
                    <div className="card-header">
                        <h3 className="card-title"><Activity size={20} className="card-icon" /> {t('dma_title')}</h3>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
                        <div className="md:col-span-2 form-group">
                            <label className="form-label">{t('stock_code')}</label>
                            <div className="flex gap-2">
                                <input
                                    type="text"
                                    className="form-input"
                                    placeholder={t('input_placeholder')}
                                    value={symbol}
                                    onChange={(e) => setSymbol(e.target.value)}
                                />
                            </div>
                        </div>
                        <div className="form-group">
                            <label className="form-label">{t('short_ma')}</label>
                            <input
                                type="number"
                                className="form-input"
                                value={shortWindow}
                                onChange={(e) => setShortWindow(parseInt(e.target.value))}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">{t('long_ma')}</label>
                            <input
                                type="number"
                                className="form-input"
                                value={longWindow}
                                onChange={(e) => setLongWindow(parseInt(e.target.value))}
                            />
                        </div>
                        <div className="md:col-span-4 mt-2">
                            <button className="btn btn-primary w-full" type="button" onClick={handleSearch} disabled={loading}>
                                {loading ? t('backtesting') : t('search_backtest')}
                            </button>
                        </div>
                    </div>
                </div>

                {chartOptions.series && (
                    <div className="dashboard-card h-[500px]">
                        <ReactECharts option={chartOptions} style={{ height: '100%', width: '100%' }} />
                    </div>
                )}
            </div>
        </div>
    );
}

export default DualMovingAverage;
