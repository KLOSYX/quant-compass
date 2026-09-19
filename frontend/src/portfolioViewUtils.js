const getISODate = (date) => date.toISOString().split('T')[0];
const formatDD = (obj, key, fallbackKey) => {
    const val = obj?.[key] ?? obj?.[fallbackKey];
    if (val === undefined || val === null) return '--';
    return `${(val * 100).toFixed(2)}%`;
};
const formatPercentValue = (value, digits = 2) => (
    value === undefined || value === null || !Number.isFinite(Number(value))
        ? '--'
        : `${(Number(value) * 100).toFixed(digits)}%`
);
const formatRatio = (value, digits = 2) => (
    value === undefined || value === null || !Number.isFinite(Number(value))
        ? '--'
        : Number(value).toFixed(digits)
);

// Format money values for better readability (e.g., 1234567 -> "123.46万")
const formatMoney = (value) => {
    if (value === null || value === undefined) return '--';
    const num = Number(value);
    if (isNaN(num)) return '--';

    const absNum = Math.abs(num);
    const sign = num < 0 ? '-' : '';

    if (absNum >= 100000000) {
        // >= 1亿
        return `${sign}${(absNum / 100000000).toFixed(2)}亿`;
    } else if (absNum >= 10000) {
        // >= 1万
        return `${sign}${(absNum / 10000).toFixed(2)}万`;
    } else if (absNum >= 1) {
        return `${sign}${absNum.toFixed(2)}`;
    } else {
        return `${sign}${absNum.toFixed(2)}`;
    }
};

const getAdviceActionMeta = (action, t) => {
    if (action === 'Buy') {
        return { label: t('action_buy'), badgeClass: 'buy' };
    }
    if (action === 'Sell') {
        return { label: t('action_sell'), badgeClass: 'sell' };
    }
    if (action === '存入' || action === 'Deposit') {
        return { label: t('action_deposit'), badgeClass: 'deposit' };
    }
    if (action === '取用' || action === 'Withdraw') {
        return { label: t('action_withdraw'), badgeClass: 'withdraw' };
    }
    return { label: t('action_hold'), badgeClass: 'hold' };
};

const getAllocationSignalLabel = (signal, t) => {
    if (signal === 'undervalued') return t('allocation_upper');
    if (signal === 'overvalued') return t('allocation_lower');
    return t('allocation_neutral');
};

const getStoredNumber = (key, fallback) => {
    const raw = localStorage.getItem(key);
    if (raw === null || raw === undefined || raw === '') return fallback;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const getStoredPercentWithLegacyRatioSupport = (key, fallback) => {
    const raw = localStorage.getItem(key);
    if (raw === null || raw === undefined || raw === '') return fallback;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) return fallback;
    // Legacy versions persisted ratios (0~1). Current UI expects percentages (0~100).
    if (parsed > 0 && parsed <= 1) {
        const migrated = parsed * 100;
        localStorage.setItem(key, String(migrated));
        return migrated;
    }
    return parsed;
};

export const sanitizeLegacyHoldings = (holdings = {}) => ({ ...holdings });
export const ASSET_CATEGORY_OPTIONS = ['equity', 'bond', 'commodity', 'gold', 'money_market', 'cash_equivalent', 'other'];
export const buildAssetCategoriesPayload = (fundCodes, categories = {}) => Object.fromEntries(
    fundCodes.map(code => [code, ASSET_CATEGORY_OPTIONS.includes(categories[code]) ? categories[code] : 'other'])
);
export const buildSubstituteForPayload = (fundCodes, relationships = {}) => {
    const currentCodes = new Set(fundCodes);
    return Object.fromEntries(
        fundCodes
            .map(code => [code, String(relationships[code] || '').trim()])
            .filter(([code, primary]) => primary && primary !== code && currentCodes.has(primary))
    );
};
export const getRecommendedFrontierPoint = (analysis) => {
    const index = analysis?.recommended_point_index;
    if (!Number.isInteger(index) || index < 0) return null;
    return analysis?.efficient_frontier?.[index] || null;
};
export const getRecommendationEvidence = (analysis) => {
    const point = getRecommendedFrontierPoint(analysis);
    const selection = analysis?.recommended_point_selection;
    if (!point || !selection) return null;
    return {
        point,
        eligibleCount: selection.eligible_count ?? 0,
        totalCount: selection.total_count ?? analysis?.efficient_frontier?.length ?? 0,
        confidence: selection.confidence || 'none'
    };
};

export { getISODate, formatDD, formatPercentValue, formatRatio, formatMoney, getAdviceActionMeta, getAllocationSignalLabel, getStoredNumber, getStoredPercentWithLegacyRatioSupport };
