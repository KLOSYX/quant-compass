from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles
from pydantic import BaseModel
import akshare as ak
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from typing import List, Dict, Optional
import traceback
from datetime import date

app = FastAPI()
router = APIRouter()

FUND_LIST_CACHE = None
MAX_SINGLE_WEIGHT = 0.5  # prevent over-concentration in a single fund
MIN_WEIGHT_THRESHOLD = 0.01  # drop tiny weights that are hard to execute

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)

class AnalysisRequest(BaseModel):
    fund_codes: List[str]
    fund_fees: Dict[str, float]
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    risk_free_rate: Optional[float] = None

class StrategyBacktestRequest(BaseModel):
    fund_codes: List[str]
    weights: Dict[str, float]
    fund_fees: Dict[str, float]
    start_date: date
    end_date: date
    monthly_investment: float
    risk_free_rate: Optional[float] = None

class DCAOptimizeRequest(BaseModel):
    fund_codes: List[str]
    fund_fees: Dict[str, float]
    start_date: date
    end_date: date
    monthly_investment: float
    risk_free_rate: Optional[float] = None

def get_fund_data(fund_codes: List[str], start_date: Optional[date], end_date: Optional[date], risk_free_rate: Optional[float]) -> (pd.DataFrame, Dict[str, str], List[str]):
    global FUND_LIST_CACHE
    if FUND_LIST_CACHE is None:
        try:
            print("Initializing fund list cache...")
            FUND_LIST_CACHE = ak.fund_name_em()
            FUND_LIST_CACHE.set_index('基金代码', inplace=True)
            print("Fund list cache initialized.")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to initialize fund list cache: {e}")

    fund_data = {}
    fund_names = {}
    warnings = []
    if fund_codes:
        for code in fund_codes:
            try:
                try:
                    fund_names[code] = FUND_LIST_CACHE.loc[code, '基金简称']
                except KeyError:
                    fund_names[code] = f"{code} (名称未找到)"

                fund_nav = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
                fund_nav['净值日期'] = pd.to_datetime(fund_nav['净值日期'])
                fund_nav = fund_nav.set_index('净值日期')['单位净值'].astype(float)
                fund_data[code] = fund_nav.resample('ME').last()

            except Exception as e:
                raise HTTPException(status_code=400, detail=f"获取基金 {code} 的净值数据时发生错误: {e}")
    
    df = pd.DataFrame(fund_data)
    df = df.sort_index()

    if not df.empty:
        latest_start_date = max(df[c].first_valid_index() for c in df.columns if pd.notna(df[c].first_valid_index()))
        user_start = pd.to_datetime(start_date) if start_date else latest_start_date
        user_end = pd.to_datetime(end_date) if end_date else df.index.max()
        actual_start = max(latest_start_date, user_start)
        actual_end = min(user_end, df.index.max())
    else:
        if not start_date or not end_date:
            raise HTTPException(status_code=400, detail="当没有选择基金时，必须提供开始和结束日期。")
        user_start = pd.to_datetime(start_date)
        user_end = pd.to_datetime(end_date)
        actual_start, actual_end = user_start, user_end
        df = pd.DataFrame(index=pd.date_range(start=actual_start, end=actual_end, freq='ME'))

    if risk_free_rate is not None:
        monthly_rf_return = (1 + risk_free_rate) ** (1/12) - 1
        rf_index = pd.date_range(start=actual_start, end=actual_end, freq='ME')
        rf_returns = pd.Series(monthly_rf_return, index=rf_index)
        rf_nav = (1 + rf_returns).cumprod()
        df['RiskFree'] = rf_nav
        fund_names['RiskFree'] = '无风险资产'

    if actual_start > user_start and fund_codes:
        warnings.append(f"注意：部分基金在您选择的开始日期 {user_start.strftime('%Y-%m-%d')} 尚未成立，实际回测已从 {actual_start.strftime('%Y-%m-%d')} 开始。")

    if actual_start >= actual_end:
        raise HTTPException(status_code=400, detail="在指定的时间范围内，所选基金没有重叠的交易日。")

    df_filtered = df.loc[actual_start:actual_end]
    df_processed = df_filtered.ffill().dropna()

    if df_processed.empty:
        raise HTTPException(status_code=400, detail="数据处理后为空，无法进行分析。")

    return df_processed, fund_names, warnings

def calculate_efficient_frontier(df, fund_fees):
    if list(df.columns) == ['RiskFree']:
        monthly_returns = df.pct_change().fillna(0)
        expected_return = monthly_returns['RiskFree'].mean()
        return [{
            "risk": 0,
            "return": expected_return * 12,
            "weights": {"RiskFree": 1.0}
        }]

    monthly_returns = df.pct_change().fillna(0)
    for code in monthly_returns.columns:
        if code == 'RiskFree': continue
        monthly_fee = fund_fees.get(code, 0) / 12
        monthly_returns[code] -= monthly_fee

    expected_returns = monthly_returns.mean()
    cov_matrix = monthly_returns.cov()
    if 'RiskFree' in cov_matrix.columns:
        cov_matrix['RiskFree'] = 0
        cov_matrix.loc['RiskFree'] = 0

    num_assets = len(df.columns)
    bounds = tuple((0, MAX_SINGLE_WEIGHT) for _ in range(num_assets))

    def clean_weights(raw_weights):
        """Zero out tiny weights and renormalize to keep allocations executable."""
        weights = pd.Series(raw_weights, index=df.columns)
        weights[weights < MIN_WEIGHT_THRESHOLD] = 0
        total = weights.sum()
        if total > 0:
            weights /= total
        else:
            weights = pd.Series(raw_weights, index=df.columns)
        return weights

    def portfolio_variance(w): return np.dot(w.T, np.dot(cov_matrix, w))

    mvp_constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w) - 1})
    mvp_result = minimize(
        portfolio_variance,
        [1./num_assets]*num_assets,
        method='SLSQP',
        bounds=bounds,
        constraints=mvp_constraints
    )
    mvp_return = np.sum(expected_returns * mvp_result.x)

    frontier_returns = np.linspace(mvp_return, expected_returns.max(), 20)
    frontier_points = []

    for target in frontier_returns:
        constraints = (
            {'type': 'eq', 'fun': lambda w: np.sum(expected_returns * w) - target},
            {'type': 'eq', 'fun': lambda w: np.sum(w) - 1}
        )
        result = minimize(
            portfolio_variance,
            [1./num_assets]*num_assets,
            method='SLSQP',
            bounds=bounds,
            constraints=constraints
        )
        
        if result.success:
            cleaned_weights = clean_weights(result.x)
            # re-evaluate variance with cleaned weights to reflect practical allocation
            risk = np.sqrt(np.dot(cleaned_weights.T, np.dot(cov_matrix, cleaned_weights))) * np.sqrt(12)
            ret = target * 12
            frontier_points.append({
                "risk": risk,
                "return": ret,
                "weights": cleaned_weights.to_dict()
            })

    return frontier_points

def calculate_max_drawdown(nav_series: pd.Series) -> float:
    """Return max drawdown as a decimal (e.g., 0.2 for -20%)."""
    if nav_series.empty:
        return 0.0
    rolling_max = nav_series.cummax()
    drawdowns = (nav_series - rolling_max) / rolling_max
    return drawdowns.min()

def backtest_lump_sum(df_nav, weights_dict, total_investment):
    weights = pd.Series(weights_dict).reindex(df_nav.columns).fillna(0)
    initial_nav = df_nav.iloc[0]
    initial_shares = (total_investment * weights) / initial_nav
    portfolio_history = df_nav.dot(initial_shares.T)
    attribution_history = df_nav.multiply(initial_shares, axis='columns')
    max_drawdown = calculate_max_drawdown(portfolio_history)
    return {
        "total_invested": total_investment,
        "final_value": portfolio_history.iloc[-1],
        "max_drawdown": float(max_drawdown),
        "history": {date.strftime('%Y-%m'): value for date, value in portfolio_history.to_dict().items()},
        "attribution": {date.strftime('%Y-%m'): row.to_dict() for date, row in attribution_history.iterrows()}
    }

def backtest_dca(df_nav, weights_dict, monthly_investment):
    weights = pd.Series(weights_dict).reindex(df_nav.columns).fillna(0)
    total_shares = pd.Series(0.0, index=df_nav.columns)
    total_invested = 0.0
    portfolio_history = {}
    attribution_history = {}

    for timestamp, nav_row in df_nav.iterrows():
        total_invested += monthly_investment
        shares_bought = (monthly_investment * weights) / nav_row
        total_shares += shares_bought
        current_asset_values = total_shares * nav_row
        attribution_history[timestamp] = current_asset_values.to_dict()
        portfolio_history[timestamp] = current_asset_values.sum()
    portfolio_series = pd.Series(portfolio_history)
    max_drawdown = calculate_max_drawdown(portfolio_series)

    return {
        "total_invested": total_invested,
        "final_value": list(portfolio_history.values())[-1],
        "max_drawdown": float(max_drawdown),
        "history": {date.strftime('%Y-%m'): value for date, value in portfolio_history.items()},
        "attribution": {date.strftime('%Y-%m'): value for date, value in attribution_history.items()}
    }

def optimize_dca(df_nav, fund_fees, monthly_investment):
    if monthly_investment <= 0:
        raise HTTPException(status_code=400, detail="定投金额需要大于 0。")

    monthly_returns = df_nav.pct_change().fillna(0)
    for code in monthly_returns.columns:
        if code == 'RiskFree': 
            continue
        monthly_fee = fund_fees.get(code, 0) / 12
        monthly_returns[code] -= monthly_fee

    expected_returns = monthly_returns.mean()
    cov_matrix = monthly_returns.cov()
    if 'RiskFree' in cov_matrix.columns:
        cov_matrix['RiskFree'] = 0
        cov_matrix.loc['RiskFree'] = 0

    num_assets = len(df_nav.columns)
    bounds = tuple((0, MAX_SINGLE_WEIGHT) for _ in range(num_assets))

    def clean_weights(raw_weights):
        weights = pd.Series(raw_weights, index=df_nav.columns)
        weights[weights < MIN_WEIGHT_THRESHOLD] = 0
        total = weights.sum()
        if total > 0:
            weights /= total
        else:
            weights = pd.Series(raw_weights, index=df_nav.columns)
        return weights

    def objective(w):
        cleaned = clean_weights(w)
        nav_adjusted = (1 + monthly_returns).cumprod()
        dca_result = backtest_dca(nav_adjusted, cleaned.to_dict(), monthly_investment)
        return -dca_result["final_value"]

    constraints = ({'type': 'eq', 'fun': lambda w: np.sum(w) - 1})
    result = minimize(
        objective,
        [1./num_assets]*num_assets,
        method='SLSQP',
        bounds=bounds,
        constraints=constraints
    )

    if not result.success:
        raise HTTPException(status_code=500, detail="定投优化未能收敛，请调整参数或稍后重试。")

    cleaned_weights = clean_weights(result.x)
    nav_adjusted = (1 + monthly_returns).cumprod()
    dca_result = backtest_dca(nav_adjusted, cleaned_weights.to_dict(), monthly_investment)
    risk = np.sqrt(np.dot(cleaned_weights.T, np.dot(cov_matrix, cleaned_weights))) * np.sqrt(12)
    expected_ret = np.sum(expected_returns * cleaned_weights) * 12

    return {
        "weights": cleaned_weights.to_dict(),
        "risk": risk,
        "return": expected_ret,
        "max_drawdown": dca_result["max_drawdown"],
        "backtest": dca_result
    }

@router.post("/analyze")
async def analyze_portfolio(request: AnalysisRequest):
    if not request.fund_codes and request.risk_free_rate is None:
        raise HTTPException(status_code=400, detail="请至少选择一只基金或添加无风险资产。")
    try:
        fund_df, fund_names, warnings = get_fund_data(request.fund_codes, request.start_date, request.end_date, request.risk_free_rate)
        efficient_frontier_points = calculate_efficient_frontier(fund_df, request.fund_fees)
        return {
            "efficient_frontier": efficient_frontier_points,
            "fund_names": fund_names,
            "backtest_period": {"start_date": fund_df.index.min().strftime('%Y-%m-%d'), "end_date": fund_df.index.max().strftime('%Y-%m-%d')},
            "warnings": warnings
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/backtest_strategies")
async def run_strategy_backtests(request: StrategyBacktestRequest):
    try:
        fund_df, _, _ = get_fund_data(request.fund_codes, request.start_date, request.end_date, request.risk_free_rate)
        
        monthly_returns = fund_df.pct_change().fillna(0)
        for code in monthly_returns.columns:
            if code == 'RiskFree': continue
            monthly_fee = request.fund_fees.get(code, 0) / 12
            monthly_returns[code] -= monthly_fee
        
        nav_adjusted = (1 + monthly_returns).cumprod()

        num_months = len(fund_df)
        total_lump_sum_investment = request.monthly_investment * num_months
        lump_sum_results = backtest_lump_sum(nav_adjusted, request.weights, total_lump_sum_investment)
        
        dca_results = backtest_dca(nav_adjusted, request.weights, request.monthly_investment)
        
        return {
            "lump_sum": lump_sum_results,
            "dca": dca_results
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/optimize_dca")
async def optimize_dca_route(request: DCAOptimizeRequest):
    try:
        fund_df, _, _ = get_fund_data(request.fund_codes, request.start_date, request.end_date, request.risk_free_rate)
        result = optimize_dca(fund_df, request.fund_fees, request.monthly_investment)
        return {
            "weights": result["weights"],
            "risk": result["risk"],
            "return": result["return"],
            "max_drawdown": result["max_drawdown"],
            "backtest": result["backtest"],
            "backtest_period": {"start_date": fund_df.index.min().strftime('%Y-%m-%d'), "end_date": fund_df.index.max().strftime('%Y-%m-%d')}
        }
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

app.include_router(router, prefix="/api")
app.mount("/", StaticFiles(directory="static", html=True), name="static")
