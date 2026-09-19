const escapeHtml = value => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');

const format = (value, locale, options = {}) => new Intl.NumberFormat(locale, options).format(Number(value) || 0);
const money = (value, locale) => format(value, locale, { style: 'currency', currency: 'CNY' });
const percent = (value, locale) => value === null || value === undefined ? '—' : format(value, locale, { style: 'percent', minimumFractionDigits: 2 });

const CATEGORY_LABELS = {
    zh: {
        equity: '股票权益', bond: '债券', commodity: '商品', gold: '黄金',
        money_market: '货币基金', cash_equivalent: '现金等价物', other: '其他'
    },
    en: {
        equity: 'Equity', bond: 'Bond', commodity: 'Commodity', gold: 'Gold',
        money_market: 'Money Market', cash_equivalent: 'Cash Equivalent', other: 'Other'
    }
};

export const buildProjection = ({ initialCapital, monthlyInvestment, annualReturn, years = 10 }) => {
    const monthlyReturn = Math.pow(1 + Number(annualReturn || 0), 1 / 12) - 1;
    const points = [{ month: 0, value: Number(initialCapital) || 0 }];
    for (let month = 1; month <= years * 12; month += 1) {
        points.push({
            month,
            value: points[points.length - 1].value * (1 + monthlyReturn) + (Number(monthlyInvestment) || 0)
        });
    }
    return points;
};

const projectionChart = (points, labels, locale) => {
    const width = 900;
    const height = 320;
    const left = 85;
    const bottom = 45;
    const maxValue = Math.max(...points.map(point => point.value), 1);
    const x = month => left + month / (points.length - 1) * (width - left - 20);
    const y = value => height - bottom - value / maxValue * (height - bottom - 20);
    const path = points.map((point, index) => `${index ? 'L' : 'M'}${x(point.month)},${y(point.value)}`).join(' ');
    const ticks = [0, 0.25, 0.5, 0.75, 1].map(ratio => (
        `<line x1="${left}" y1="${y(maxValue * ratio)}" x2="880" y2="${y(maxValue * ratio)}"/>` +
        `<text x="78" y="${y(maxValue * ratio) + 4}" text-anchor="end">${escapeHtml(money(maxValue * ratio, locale))}</text>`
    )).join('');
    const years = points.filter(point => point.month % 12 === 0).map(point => (
        `<text x="${x(point.month)}" y="300" text-anchor="middle">${point.month / 12}</text>`
    )).join('');
    return `<svg viewBox="0 0 ${width} ${height}" aria-label="${escapeHtml(labels.curve)}"><g stroke="#e2e8f0">${ticks}</g><path d="${path}" fill="none" stroke="#0284c7" stroke-width="3"/>${years}</svg>`;
};

const renderTable = (headers, body) => (
    `<table><thead><tr>${headers.map(header => `<th>${escapeHtml(header)}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table>`
);

export const createPortfolioReportHtml = ({
    language = 'zh', generatedAt = new Date(), selectedPoint, fundNames = {},
    initialHoldings = {}, currentCash = 0, recommendationResult
}) => {
    if (!selectedPoint || !recommendationResult) throw Error('Missing calculated portfolio results');
    const isChinese = language !== 'en';
    const locale = isChinese ? 'zh-CN' : 'en-US';
    const labels = isChinese ? {
        title: '量化罗盘投资计划', time: '导出时间', allocation: '目前的投资资金分配', asset: '资产',
        value: '当前金额', share: '当前占比', cash: '闲置现金', target: '选定目标配置', weight: '目标权重',
        metrics: '预期年化收益 / 风险', plan: '最终定投计划', action: '操作', amount: '本次金额', after: '执行后持仓',
        ideal: '理论目标', reason: '原因', budget: '每月预算', monthly: '建议本月投入', fundRatio: '建议目标基金组合仓位',
        equityExposure: '目标股票权益暴露', riskExposure: '目标风险资产暴露', categories: '资产类别暴露',
        category: '资产类别', currentCategory: '当前金额 / 占比', targetCategory: '目标金额 / 占比',
        curve: '未来收益预期曲线（10 年）', note: '曲线按选定组合的预期年化收益率、当前总资产和建议月投入复利推算，仅为情景估算，不代表收益保证。现金不计收益。'
    } : {
        title: 'Quant Compass Investment Plan', time: 'Exported', allocation: 'Current Capital Allocation', asset: 'Asset',
        value: 'Current Value', share: 'Current Share', cash: 'Idle Cash', target: 'Selected Target Allocation', weight: 'Target Weight',
        metrics: 'Expected Annual Return / Risk', plan: 'Final DCA Plan', action: 'Action', amount: 'Amount', after: 'After Trade',
        ideal: 'Ideal Target', reason: 'Reason', budget: 'Monthly Budget', monthly: 'Recommended This Month', fundRatio: 'Target Fund Allocation',
        equityExposure: 'Target Equity Exposure', riskExposure: 'Target Risk-Asset Exposure', categories: 'Asset Category Exposure',
        category: 'Asset Category', currentCategory: 'Current Value / Share', targetCategory: 'Target Value / Share',
        curve: 'Expected Future Value (10 Years)', note: 'Scenario based on the selected expected return, current capital and recommended monthly contribution. Not a return guarantee. Cash earns no return.'
    };
    const holdings = Object.entries(initialHoldings).filter(([, value]) => Number(value) > 0);
    const total = holdings.reduce((sum, [, value]) => sum + Number(value), 0) + (Number(currentCash) || 0);
    const currentRows = [...holdings, ['Cash', currentCash]].filter(([, value]) => Number(value) > 0).map(([code, value]) => (
        `<tr><td>${escapeHtml(code === 'Cash' ? labels.cash : fundNames[code] || code)}</td><td>${escapeHtml(money(value, locale))}</td><td>${escapeHtml(percent(Number(value) / Math.max(total, 1), locale))}</td></tr>`
    )).join('');
    const targetRows = Object.entries(selectedPoint.weights || {}).map(([code, weight]) => (
        `<tr><td>${escapeHtml(fundNames[code] || code)}</td><td>${escapeHtml(percent(weight, locale))}</td></tr>`
    )).join('');
    const adviceRows = (recommendationResult.fund_advice || []).map(advice => (
        `<tr><td>${escapeHtml(advice.name || advice.code)}</td><td>${escapeHtml(advice.action)}</td><td>${escapeHtml(money(advice.amount, locale))}</td><td>${escapeHtml(money(advice.executable_holding ?? advice.target_holding, locale))}</td><td>${escapeHtml(money(advice.ideal_holding ?? advice.target_holding, locale))}</td><td>${escapeHtml(advice.reason)}</td></tr>`
    )).join('');
    const currentValues = recommendationResult.current_category_values || {};
    const targetValues = recommendationResult.target_category_values || {};
    const currentExposures = recommendationResult.current_category_exposures || {};
    const targetExposures = recommendationResult.target_category_exposures || {};
    const categories = [...new Set([...Object.keys(currentValues), ...Object.keys(targetValues)])];
    const categoryRows = categories.map(category => (
        `<tr><td>${escapeHtml(CATEGORY_LABELS[isChinese ? 'zh' : 'en'][category] || category)}</td>` +
        `<td>${escapeHtml(money(currentValues[category], locale))} / ${escapeHtml(percent(currentExposures[category], locale))}</td>` +
        `<td>${escapeHtml(money(targetValues[category], locale))} / ${escapeHtml(percent(targetExposures[category], locale))}</td></tr>`
    )).join('');
    const monthly = Number(recommendationResult.recommended_monthly_investment) || 0;
    const fundRatio = recommendationResult.target_fund_ratio ?? recommendationResult.target_equity_ratio ?? 0;
    const categorySection = categories.length
        ? `<h2>${labels.categories}</h2>${renderTable([labels.category, labels.currentCategory, labels.targetCategory], categoryRows)}`
        : '';
    const conditionalRows = Object.entries(recommendationResult.rebalancing?.conditional_buys_after_settlement || {}).map(([code, amount]) =>
        `<tr><td>${escapeHtml(fundNames[code] || code)}</td><td>${escapeHtml(money(amount, locale))}</td></tr>`
    );
    const conditionalSection = conditionalRows.length ? `<h2>${isChinese ? '到账后条件买入' : 'Conditional purchases after settlement'}</h2><p>${isChinese ? '卖出款实际到账后，更新账户并重新核验可购日期、资格和剩余额度；不计入本次可用现金。' : 'After proceeds settle, update the account and recheck dates, eligibility and remaining limits; these are not funded by current available cash.'}</p>${renderTable([labels.asset, labels.amount], conditionalRows)}` : '';
    const curve = selectedPoint.return === null || selectedPoint.return === undefined
        ? `<p class="note">${isChinese ? '收益数据不可估计，不生成收益预测曲线。' : 'Return estimate unavailable; no return projection generated.'}</p>`
        : projectionChart(buildProjection({ initialCapital: total, monthlyInvestment: monthly, annualReturn: selectedPoint.return }), labels, locale);
    return `<!doctype html><html lang="${isChinese ? 'zh-CN' : 'en'}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>${escapeHtml(labels.title)}</title><style>body{color:#172033;background:#f8fafc;font:15px/1.55 system-ui}main{max-width:1040px;margin:auto;padding:40px 24px}h2{margin-top:30px;border-bottom:2px solid #0ea5e9}.meta,.note{color:#64748b}.summary{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}.pill{padding:10px;background:#e0f2fe;border-radius:8px}table{width:100%;border-collapse:collapse;background:white}th,td{padding:10px;border:1px solid #dbe4ee;text-align:left}th{background:#eaf5fb}svg{width:100%;background:white;border:1px solid #dbe4ee}@media print{main{padding:0}table,svg{break-inside:avoid}}</style></head><body><main><h1>${escapeHtml(labels.title)}</h1><p class="meta">${labels.time}: ${escapeHtml(generatedAt.toLocaleString(locale))}</p><h2>${labels.allocation}</h2>${renderTable([labels.asset, labels.value, labels.share], currentRows)}<h2>${labels.target}</h2><p class="pill">${labels.metrics}: <b>${percent(selectedPoint.return, locale)} / ${percent(selectedPoint.risk, locale)}</b></p>${renderTable([labels.asset, labels.weight], targetRows)}${categorySection}<h2>${labels.plan}</h2><div class="summary"><span class="pill">${labels.budget}: <b>${money(recommendationResult.monthly_budget, locale)}</b></span><span class="pill">${labels.monthly}: <b>${money(monthly, locale)}</b></span><span class="pill">${labels.fundRatio}: <b>${percent(fundRatio, locale)}</b></span><span class="pill">${labels.equityExposure}: <b>${percent(recommendationResult.target_equity_exposure, locale)}</b></span><span class="pill">${labels.riskExposure}: <b>${percent(recommendationResult.target_risk_asset_exposure, locale)}</b></span></div>${renderTable([labels.asset, labels.action, labels.amount, labels.after, labels.ideal, labels.reason], adviceRows)}${conditionalSection}<h2>${labels.curve}</h2>${curve}<p class="note">${labels.note}</p></main></body></html>`;
};

export const downloadPortfolioReport = options => {
    const url = URL.createObjectURL(new Blob([createPortfolioReportHtml(options)], { type: 'text/html;charset=utf-8' }));
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `quant-compass-investment-plan-${new Date().toISOString().slice(0, 10)}.html`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
};
