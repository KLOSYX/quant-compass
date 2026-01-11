import React, { useState } from 'react';
import { useLanguage } from './LanguageContext';
import axios from 'axios';
import { Search } from 'lucide-react';

function ValueInvesting() {
    const { t } = useLanguage();
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
                        <h3 className="card-title"><Search size={20} className="card-icon" /> {t('vi_title')}</h3>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-end">
                        <div className="form-group">
                            <label className="form-label">{t('max_pe')}</label>
                            <input
                                type="number"
                                className="form-input"
                                value={peMax}
                                onChange={(e) => setPeMax(parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="form-group">
                            <label className="form-label">{t('max_pb')}</label>
                            <input
                                type="number"
                                className="form-input"
                                value={pbMax}
                                onChange={(e) => setPbMax(parseFloat(e.target.value))}
                            />
                        </div>
                        <div className="form-group">
                            <button className="btn btn-primary w-full" onClick={handleScreen} disabled={loading}>
                                {loading ? t('screening') : t('start_screen_btn')}
                            </button>
                        </div>
                    </div>
                </div>

                {stocks.length > 0 && (
                    <div className="dashboard-card">
                        <div className="card-header">
                            <h3 className="card-title">{t('screen_results')} ({stocks.length})</h3>
                        </div>
                        <div className="overflow-x-auto">
                            <table className="data-table">
                                <thead>
                                    <tr>
                                        <th>{t('col_code')}</th>
                                        <th>{t('col_name')}</th>
                                        <th>{t('col_price')}</th>
                                        <th>{t('col_pe')}</th>
                                        <th>{t('col_pb')}</th>
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
                        <p>{t('no_data')}</p>
                    </div>
                )}
            </div>
        </div>
    );
}

export default ValueInvesting;
