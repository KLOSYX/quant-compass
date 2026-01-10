import React, { useState } from 'react';
import axios from 'axios';
import { Search } from 'lucide-react';

function ValueInvesting() {
    const [peMax, setPeMax] = useState(20);
    const [pbMax, setPbMax] = useState(2);
    const [stocks, setStocks] = useState([]);
    const [loading, setLoading] = useState(false);

    const handleScreen = async () => {
        setLoading(true);
        try {
            const response = await axios.get(`http://localhost:8000/strategy/value-investing?pe_max=${peMax}&pb_max=${pbMax}`);
            setStocks(response.data);
        } catch (error) {
            console.error("Error screening value stocks:", error);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="app-main">
            <div className="full-width">
                <div className="dashboard-card mb-6">
                    <div className="card-header">
                        <h3 className="card-title"><Search size={20} className="card-icon" /> 价值投资筛选器</h3>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
                        <div className="form-group">
                            <label className="form-label">最大市盈率 (PE)</label>
                            <input
                                type="number"
                                className="form-input"
                                value={peMax}
                                onChange={(e) => setPeMax(parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">最大市净率 (PB)</label>
                            <input
                                type="number"
                                className="form-input"
                                value={pbMax}
                                onChange={(e) => setPbMax(parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="form-group">
                            <button className="btn btn-primary w-full" onClick={handleScreen} disabled={loading}>
                                {loading ? '筛选中...' : '开始筛选'}
                            </button>
                        </div>
                    </div>
                </div>

                {stocks.length > 0 && (
                    <div className="dashboard-card">
                        <div className="card-header">
                            <h3 className="card-title">筛选结果 ({stocks.length})</h3>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>代码</th>
                                        <th>名称</th>
                                        <th>最新价</th>
                                        <th>市盈率 (动态)</th>
                                        <th>市净率</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {stocks.map(stock => (
                                        <tr key={stock['代码']}>
                                            <td className="font-mono text-slate-400">{stock['代码']}</td>
                                            <td className="font-medium text-white">{stock['名称']}</td>
                                            <td className="text-emerald-400">¥{stock['最新价']}</td>
                                            <td>{stock['市盈率-动态'].toFixed(2)}</td>
                                            <td>{stock['市净率'].toFixed(2)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}

                {stocks.length === 0 && !loading && (
                    <div className="text-center p-12 text-slate-500 dashboard-card border-dashed">
                        <Search size={48} className="mx-auto mb-4 opacity-20" />
                        <p>暂无数据，请调整参数后筛选</p>
                    </div>
                )}
            </div>
        </div>
    );
}

export default ValueInvesting;
