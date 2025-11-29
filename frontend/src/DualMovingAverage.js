
import React, { useState } from 'react';
import ReactECharts from 'echarts-for-react';
import axios from 'axios';

function DualMovingAverage() {
    const [symbol, setSymbol] = useState('');
    const [chartOptions, setChartOptions] = useState({});
    const [shortWindow, setShortWindow] = useState(5);
    const [longWindow, setLongWindow] = useState(20);

    const handleSearch = async () => {
        try {
            // Fetch basic stock data
            const response = await axios.get(`http://localhost:8000/stock/${symbol}`);
            const data = response.data;

            // Fetch backtesting data
            const strategyResponse = await axios.get(`http://localhost:8000/strategy/dual-moving-average/${symbol}?short_window=${shortWindow}&long_window=${longWindow}`);
            const strategyData = strategyResponse.data;

            setChartOptions({
                legend: {
                    data: ['Close', 'Short MA', 'Long MA', 'Buy Signal', 'Sell Signal']
                },
                xAxis: {
                    type: 'category',
                    data: data.dates
                },
                yAxis: {
                    type: 'value',
                    scale: true
                },
                series: [
                    {
                        name: 'Close',
                        data: data.prices,
                        type: 'line'
                    },
                    {
                        name: 'Short MA',
                        data: strategyData.short_ma,
                        type: 'line',
                        smooth: true
                    },
                    {
                        name: 'Long MA',
                        data: strategyData.long_ma,
                        type: 'line',
                        smooth: true
                    },
                    {
                        name: 'Buy Signal',
                        type: 'scatter',
                        symbol: 'arrow',
                        symbolSize: 10,
                        itemStyle: {
                            color: 'red'
                        },
                        data: strategyData.buy_signals.map(d => [d.date, d.price])
                    },
                    {
                        name: 'Sell Signal',
                        type: 'scatter',
                        symbol: 'arrow',
                        symbolRotate: 180,
                        symbolSize: 10,
                        itemStyle: {
                            color: 'green'
                        },
                        data: strategyData.sell_signals.map(d => [d.date, d.price])
                    }
                ]
            });
        } catch (error) {
            console.error("Error fetching data:", error);
        }
    };

    return (
        <div>
            <div className="row mt-4">
                <div className="col-md-8 offset-md-2">
                    <div className="input-group mb-3">
                        <input
                            type="text"
                            className="form-control"
                            placeholder="输入股票代码 (e.g., 000001)"
                            value={symbol}
                            onChange={(e) => setSymbol(e.target.value)}
                        />
                        <button className="btn btn-primary" type="button" onClick={handleSearch}>查询并回测</button>
                    </div>
                    <div className="row">
                        <div className="col-md-6">
                            <div className="input-group mb-3">
                                <span className="input-group-text">短期均线</span>
                                <input
                                    type="number"
                                    className="form-control"
                                    value={shortWindow}
                                    onChange={(e) => setShortWindow(parseInt(e.target.value))}
                                />
                            </div>
                        </div>
                        <div className="col-md-6">
                            <div className="input-group mb-3">
                                <span className="input-group-text">长期均线</span>
                                <input
                                    type="number"
                                    className="form-control"
                                    value={longWindow}
                                    onChange={(e) => setLongWindow(parseInt(e.target.value))}
                                />
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {chartOptions.series && (
                <div className="row mt-4">
                    <div className="col-12">
                        <ReactECharts option={chartOptions} style={{ height: '500px' }} />
                    </div>
                </div>
            )}
        </div>
    );
}

export default DualMovingAverage;
