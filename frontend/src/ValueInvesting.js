
import React, { useState } from 'react';
import axios from 'axios';

function ValueInvesting() {
    const [peMax, setPeMax] = useState(20);
    const [pbMax, setPbMax] = useState(2);
    const [stocks, setStocks] = useState([]);

    const handleScreen = async () => {
        try {
            const response = await axios.get(`http://localhost:8000/strategy/value-investing?pe_max=${peMax}&pb_max=${pbMax}`);
            setStocks(response.data);
        } catch (error) {
            console.error("Error screening value stocks:", error);
        }
    };

    return (
        <div className="mt-5">
            <h2>价值投资策略</h2>
            <div className="row mt-4">
                <div className="col-md-4">
                    <div className="input-group mb-3">
                        <span className="input-group-text">最大市盈率 (PE)</span>
                        <input
                            type="number"
                            className="form-control"
                            value={peMax}
                            onChange={(e) => setPeMax(parseFloat(e.target.value))}
                        />
                    </div>
                </div>
                <div className="col-md-4">
                    <div className="input-group mb-3">
                        <span className="input-group-text">最大市净率 (PB)</span>
                        <input
                            type="number"
                            className="form-control"
                            value={pbMax}
                            onChange={(e) => setPbMax(parseFloat(e.target.value))}
                        />
                    </div>
                </div>
                <div className="col-md-4">
                    <button className="btn btn-primary" onClick={handleScreen}>筛选</button>
                </div>
            </div>

            {stocks.length > 0 && (
                <table className="table table-striped mt-4">
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
                                <td>{stock['代码']}</td>
                                <td>{stock['名称']}</td>
                                <td>{stock['最新价']}</td>
                                <td>{stock['市盈率-动态'].toFixed(2)}</td>
                                <td>{stock['市净率'].toFixed(2)}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
        </div>
    );
}

export default ValueInvesting;
